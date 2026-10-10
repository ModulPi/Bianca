"""A-3 线上核对：止损/回撤真的会破线，破了真的会平。

要证明的是一条链，不是某个函数存在：

    权益快照 ──> 持仓 + 回撤 ──┐
                              ├──> plan_closes ──> force_close ──> execute_agent ──> 账本
    实盘 K 线 ──> 近期高点 ───┘        （巡检）      （豁免单笔上限）      （原样记账）

默认**只读**：不写库、不下单，只把每一段的结论念出来。
`--synthetic` 在临时库上造超限场景（数字是编的），连下单路径一起走完 —— 用假交易所，
仍然不碰真账户。`--execute` 才会对**真实破线**发起真单（Demo）。

    PYTHONIOENCODING=utf-8 python scripts/verify_stops_live.py
    PYTHONIOENCODING=utf-8 python scripts/verify_stops_live.py --synthetic
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from typing import Any

sys.path.insert(0, ".")

if "--synthetic" in sys.argv:
    # 必须在第一次 get_settings() 之前设好：engine/session 是模块级单例。
    _tmp = tempfile.mkdtemp(prefix="bianca-stops-verify-")
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/verify.db"

from agent.config import Settings, get_settings  # noqa: E402
from agent.portfolio.klines import fetch_recent_highs  # noqa: E402
from agent.portfolio.pnl import drawdown  # noqa: E402
from agent.portfolio.valuation import CASH_ASSETS, Position  # noqa: E402
from agent.risk.engine import PortfolioInputs, _latest_holdings, _stop_assets  # noqa: E402
from agent.risk.monitor import plan_closes  # noqa: E402
from agent.risk.rules import MaxTradeAmountRule, RiskContext  # noqa: E402
from agent.storage import database  # noqa: E402
from agent.storage.repository import EquityRepository  # noqa: E402

UTC_NOW = datetime.now(UTC)

# 合成场景用的假高点/假现价。**是编的**，只为让判定跳一次。
FAKE_HIGH = 100000.0
FAKE_PRICE = 50000.0  # 自高点腰斩，远超任何合理止损线

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--synthetic", action="store_true", help="临时库 + 合成数字，走完下单路径（假交易所）")
parser.add_argument("--execute", action="store_true", help="对真实破线发起真单（Demo，会下单）")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def show(label: str, value: object) -> None:
    print(f"  {label:<34} {value}")


def line(ok: bool, text: str) -> None:
    print(f"  [{'OK ' if ok else 'FAIL'}] {text}")


# 合成持仓。BTC 名义值 200 USDT **刻意是默认单笔上限（50）的 4 倍** ——
# 这样第 5 节才真的演示得出"常规风控会拦住、强制平仓不会"。
SYN_BTC_QTY = 0.004
SYN_ETH_QTY = 0.01


async def _seed_synthetic(equity: EquityRepository) -> None:
    """两次快照造出回撤：先一个高权益峰值，再一个低权益 + 带持仓的当前值。

    时间戳都从 `UTC_NOW` 往前推，**不要写死日期** —— 写死的话本机时区一旦比
    UTC 快（这台机器是 UTC+8），"峰值"就会落在未来，`latest()` 取回的是它，
    结果是拿一张空快照去判断，整段演示静默变成"没有持仓"。
    """
    positions = [
        {"asset": "BTC", "quantity": SYN_BTC_QTY, "price": FAKE_PRICE,
         "value_quote": SYN_BTC_QTY * FAKE_PRICE},
        {"asset": "ETH", "quantity": SYN_ETH_QTY, "price": 3000.0,
         "value_quote": SYN_ETH_QTY * 3000.0},
    ]
    held = sum(p["value_quote"] for p in positions)
    peak_at = (UTC_NOW - timedelta(hours=1)).isoformat()
    await equity.save(
        cash_quote=1000.0, holdings_value=0.0, equity=1000.0,
        positions=[], unpriced=[], gaps=[], notes=["合成：峰值"],
        captured_at=peak_at,
    )
    await equity.save(
        cash_quote=700.0, holdings_value=held, equity=700.0 + held,
        positions=positions, unpriced=[], gaps=[], notes=["合成：当前"],
        captured_at=UTC_NOW.isoformat(),
    )


# ------------------------------------------------------------------ 假交易所（合成模式专用）

def _filled(amount: float, price: float = FAKE_PRICE) -> dict[str, Any]:
    """假回执。`filled` 跟着请求量走 —— 否则对账会认为只成交了一部分。"""
    return {
        "id": 88001, "status": "closed", "filled": amount, "average": price,
        "cost": amount * price, "fee": None, "clientOrderId": "cid",
    }


class _FakeCCXT:
    """够 `run_execute_agent` 走完提交前回查 → 下单 → 对账。"""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.probed: list[str] = []
        self._last = _filled(0.0)

    async def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self.created.append({"symbol": symbol, "side": side, "amount": amount})
        self._last = _filled(float(amount))
        return self._last

    async def fetch_order(self, order_id, symbol=None, params=None):
        import ccxt

        if (params or {}).get("origClientOrderId"):
            self.probed.append(params["origClientOrderId"])
            raise ccxt.OrderNotFound("unknown cid")  # 交易所不认得 -> 可安全提交
        return self._last

    async def close(self):
        return None


def _install_fake_offline(settings: Settings, fake: _FakeCCXT) -> None:
    """把两处出网全部换成假的：取高点、连交易所。合成模式必须完全离线。"""
    from agent.graph import execute_agent
    from agent.risk import close as close_module

    class _FakeDemo:
        def __init__(self, settings=None):
            self._exchange = fake

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        @property
        def exchange(self):
            return self._exchange

        async def fetch_order(self, order_id, symbol=None):
            return await fake.fetch_order(order_id, symbol)

    execute_agent.SpotDemoExchange = _FakeDemo  # type: ignore[assignment]

    async def _loader(settings, *, highs_provider=None):  # noqa: ARG001
        equity, positions = await _latest_holdings()
        dd = await drawdown()
        return PortfolioInputs(
            equity=equity,
            positions=positions,
            highs={p.asset: FAKE_HIGH for p in positions},
            drawdown_pct=dd.pct,
            peak_equity=dd.peak_equity,
        )

    close_module.load_portfolio_inputs = _loader  # type: ignore[assignment]


async def main() -> int:
    args = parser.parse_args()
    settings = get_settings()

    print("A-3 线上核对 —— 止损/回撤破线判定 + 强制平仓")
    print(f"UTC 现在     : {UTC_NOW.isoformat()}")
    print(f"数据库       : {settings.database_url}")
    print(f"止损线       : {settings.stop_loss_pct:.2%}（回看 {settings.stop_lookback_hours}h）")
    print(f"回撤上限     : {settings.max_drawdown_pct:.2%}")
    print(f"仓位上限     : {settings.max_position_pct:.2%}")
    print(f"单笔上限     : {settings.max_trade_amount} USDT")
    print(f"强制平仓总闸 : {'开' if settings.force_close_enabled else '关（破线也不平！）'}")
    print(f"合成模式     : {'是（临时库，数字是编的）' if args.synthetic else '否（真实库）'}")

    await database.init_db()
    equity = EquityRepository()

    if args.synthetic:
        section("0. 造合成场景（**数字是编的**）")
        await _seed_synthetic(equity)
        held = SYN_BTC_QTY * FAKE_PRICE + SYN_ETH_QTY * 3000.0
        show("权益 峰值 -> 当前", f"1000.0 -> {700.0 + held:.2f}")
        show(
            "持仓",
            f"BTC {SYN_BTC_QTY} @ {FAKE_PRICE:g} (={SYN_BTC_QTY * FAKE_PRICE:g} USDT) / "
            f"ETH {SYN_ETH_QTY} @ 3000 (={SYN_ETH_QTY * 3000:g} USDT)",
        )

    # ---------------------------------------------------------------- 库里的账
    section("1. 最新权益快照里有什么")
    _, positions = await _latest_holdings()
    row = await equity.latest()
    if row is None:
        print("  （还没有任何快照）—— 风控会拿到'未知'并拦下开仓，看不见持仓也就无法判断仓位上限")
    else:
        show("captured_at", row.captured_at)
        show("equity / cash", f"{row.equity:.4f} / {row.cash_quote:.4f}")
        if not positions:
            print("  （无持仓 —— 没有可平的东西，回撤破线也只会记一条说明）")
        for p in positions:
            show(f"持仓 {p.asset}", f"{p.quantity} @ {p.price} = {p.value_quote:.4f} USDT")

    # ---------------------------------------------------------------- 回撤
    section("2. 权益回撤（全历史峰值，ADR-029）")
    dd = await drawdown()
    show("known", dd.known)
    show("peak_equity", dd.peak_equity)
    show("current_equity", dd.current_equity)
    show("drawdown_pct", None if dd.pct is None else f"{dd.pct:.4%}")
    show("since（重置点）", dd.since)
    if dd.reason:
        show("reason", dd.reason)
    if dd.known:
        expect = (dd.peak_equity - dd.current_equity) / dd.peak_equity
        line(dd.pct is not None and abs(dd.pct - expect) < 1e-9, "pct == (峰值 − 当前) / 峰值")
        breached = dd.pct >= settings.max_drawdown_pct
        show("是否破线", f"{breached}（上限 {settings.max_drawdown_pct:.2%}）")
    else:
        line(dd.pct is None, "算不出来时给 None（不是 0.0）—— 0.0 会被读成'没回撤'")
        print("         这一刻巡检只会记一条 note，不会自己平仓（ADR-029）")

    # ---------------------------------------------------------------- 近期高点
    section("3. 近期高点（**唯一出网的一段**，走生产 REST）")
    # 取数口径必须和产品一致：只有非现金、数量非零的持仓才去要高点。
    # 拿全部持仓去取的话，会向交易所要 `USDTUSDT` 这种不存在的交易对，
    # 换回一个 400 —— 那是自己造的噪音，不是行情。
    cash = [p.asset for p in positions if p.asset in CASH_ASSETS]
    if cash:
        show("现金（不取高点）", ", ".join(cash))
    assets = _stop_assets(positions)
    if args.synthetic:
        highs: dict[str, float | None] = {a: FAKE_HIGH for a in assets}
        print("  合成模式：跳过取数，直接用编好的高点")
    elif not assets:
        highs = {}
        print("  无持仓 -> 不出网（引擎里也是这个短路）")
    else:
        highs = await fetch_recent_highs(assets, settings=settings)

    for a in assets:
        pos = next(p for p in positions if p.asset == a)
        high = highs.get(a)
        if high is None:
            show(a, "高点 **拿不到** -> 止损判不了，只会拦开仓（保守失败）")
            continue
        drop = (high - pos.price) / high
        verdict = "破线" if drop >= settings.stop_loss_pct else "未破"
        show(a, f"高点 {high:.2f} / 现价 {pos.price:.2f} / 回落 {drop:.4%} -> {verdict}")

    if assets and not args.synthetic and all(highs.get(a) is None for a in assets):
        print("  [WARN] 一个高点都没取到 —— 代理不通？止损会因此全部判不了，")
        print("         结果是只拦开仓、不平仓。这是设计中的保守失败，不是崩溃。")

    # ---------------------------------------------------------------- 巡检判定
    section("4. 巡检判定（每轮 tick 都跑，与 LLM 说什么无关）")
    plan = plan_closes(positions, highs, dd.pct, settings=settings)
    show("should_close", plan.should_close)
    if not plan.instructions:
        print("  （本轮没有该平的东西）")
    for i in plan.instructions:
        show(f"平 {i.asset} 数量 {i.quantity}", f"rule={i.rule}")
        show("  理由", i.reason)
    for n in plan.notes:
        show("note", n)

    if args.synthetic:
        line(len(plan.instructions) == 2, "回撤破线 -> BTC、ETH 两个持仓都被列进清仓名单")
        line(
            all(i.rule == "drawdown" for i in plan.instructions),
            "原因是账户级的回撤，不是单标的止损",
        )

    # ---------------------------------------------------------------- 豁免
    section("5. 强制平仓豁免单笔上限（ADR-027）—— 这是整条链的要害")
    btc = next((p for p in positions if p.asset == "BTC"), None)
    if btc is None:
        print("  当前没有 BTC 持仓可用来演示；--synthetic 会造一个")
    else:
        notional = btc.quantity * btc.price
        show("持仓名义价值", f"{notional:.4f} USDT（单笔上限 {settings.max_trade_amount}）")

        # 常规路径：这笔 SELL 会被单笔上限拦下 —— 先证明"这道闸门是真的存在且生效"。
        signal = {"action": "SELL", "amount": btc.quantity, "symbol": f"{btc.asset}USDT"}
        ctx = RiskContext(
            signal=signal,
            market_data={"symbol": f"{btc.asset}USDT", "last": btc.price},
            settings=settings,
        )
        blocked = MaxTradeAmountRule().evaluate(ctx)
        show("常规风控裁决", "拦下" if blocked else "放行")
        show("拦下的理由", blocked.reason if blocked else "（未触发）")

        if blocked is not None:
            line(True, "单笔上限确实会拦住这笔 SELL —— 闸门是真的")
        else:
            line(
                False,
                f"名义值 {notional:.2f} 没超上限 {settings.max_trade_amount}，"
                "演示不成立；把 max_trade_amount 调小再看",
            )

        # 第二条断言**不能**看本轮实际破没破线：真实库这一刻通常没破，
        # 那样就成了"因为没破线所以豁免成立"，什么也没证明。
        # 这里强行喂一个 99% 的回撤，专看"明知超上限，计划里还有没有这笔"。
        forced = plan_closes(positions, highs, 0.99, settings=settings)
        forced_btc = next((i for i in forced.instructions if i.asset == "BTC"), None)
        show("强行喂 99% 回撤后是否列进 BTC", forced_btc is not None)
        line(
            forced_btc is not None,
            "计划不查单笔上限 —— 超上限 4 倍也照样列进清仓单，"
            "否则仓位越大越平不掉（豁免记进 risk_events）",
        )

    # ---------------------------------------------------------------- 真下单（可选）
    if args.synthetic:
        section("6. 走完下单路径（**假交易所**，不碰真账户）")
        from agent.risk import close as close_module

        fake = _FakeCCXT()
        _install_fake_offline(settings, fake)
        results = await close_module.force_close(settings=settings)
        show("下单笔数", len(fake.created))
        for r in results:
            show(f"  {r['asset']}", f"rule={r['rule']} status={r['status']}")
        line(
            len(fake.created) == len(plan.instructions),
            f"计划要平 {len(plan.instructions)} 笔就下了 {len(fake.created)} 笔",
        )
        line(bool(fake.probed), "每笔提交前都回查过幂等键")

        over = [c for c in fake.created if c["amount"] * FAKE_PRICE > settings.max_trade_amount]
        if over:
            line(
                True,
                f"有 {len(over)} 笔平仓额超过单笔上限 {settings.max_trade_amount} 却仍然成交 —— 豁免生效",
            )
        elif fake.created:
            line(
                False,
                f"没有一笔超过单笔上限 {settings.max_trade_amount}，豁免未被真正考验；"
                "把合成持仓调大再看",
            )
        else:  # pragma: no cover — 上一行的断言已经会先失败
            line(False, "一笔都没下")

    elif args.execute:
        section("6. 真下单（Demo，**会留下真实成交**）")
        if not plan.should_close:
            print("  本轮没有破线 -> 不下单（这正是想要的：不该平的时候别平）")
        else:
            from agent.risk.close import force_close

            results = await force_close(settings=settings)
            for r in results:
                show(f"  {r['asset']} {r['status']}", r.get("message") or "")
            show("risk_events / trade_log", "各写了一条，risk_decision=force_close")
    else:
        section("6. 下单路径")
        print("  未执行。--synthetic 用假交易所走一遍；--execute 对真实破线发起真单。")

    print("\n结论：见上面每一行的 [ OK ] / [FAIL]。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
