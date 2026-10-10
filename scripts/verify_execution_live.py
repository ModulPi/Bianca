"""执行路径的线上验证（Demo 账户）。离线测试全绿不等于回执字段如我们所想。

离线测试用的是假的 ccxt，它的 `_FILLED_ORDER` 是我们**自己编的**字段名。真交易所
回的是什么、ccxt 怎么映射、字段在不在 —— 只有真打一次才知道。本脚本验证：

  1. ccxt 实际在打哪个域名            -> BINANCE_DEMO_BASE_URL 这个开关到底有没有用
  2. 只读面：balance / ticker / markets
  3. 提交一笔市价单，dump **原始回执**  -> filled / average / cost / status 的真实填充
  4. 用同一个 clientOrderId 再提交一次  -> 交易所到底认不认这个幂等键
  5. 对该单号 fetch_order              -> 对账轮询读到的东西与回执是否一致

默认**只读**，不下单。要下单必须显式加 --place（花的是 Demo 账户的假钱）。

    python scripts/verify_execution_live.py            # 1、2
    python scripts/verify_execution_live.py --place    # 全部

代理没开时整脚本会失败 —— 这是预期的，见 .env 的 BINANCE_PROXY。
诊断工件；结论折进设计文档后可删。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from typing import Any

sys.path.insert(0, ".")

# 必须在任何 get_settings() 之前设好 —— database 的 engine 是模块级单例，
# e2e 用临时库，免得把线上 data/bianca.db 搅进去。
if "--e2e" in sys.argv:
    _tmp = tempfile.mkdtemp(prefix="bianca-live-e2e-")
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/bianca.db"

from agent.config import get_settings  # noqa: E402
from agent.exchange.spot_demo import SpotDemoExchange  # noqa: E402
from agent.graph.execute_agent import run_execute_agent  # noqa: E402
from agent.storage import database  # noqa: E402
from agent.storage.repository import OrderRepository, TradeRepository  # noqa: E402
from agent.trading.orders import new_client_order_id, normalize_order  # noqa: E402

SYMBOL = "BTCUSDT"
# Demo 账户买 BTC 用计价币名义，够过 minNotional(5 USDT) 即可，不铺张
QUOTE_QTY = 10.0
# 幂等验证用固定 seed，重复跑脚本仍打同一笔 —— 这本身就是验证的一部分
IDEMPOTENCY_SEED = "verify-execution-live-2026-10-11"

OK = "[ OK ]"
WARN = "[WARN]"
BAD = "[FAIL]"


def section(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def show(label: str, value: Any) -> None:
    print(f"  {label:<28} {value}")


def dump(label: str, obj: Any) -> None:
    print(f"\n  --- {label} ---")
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


# --------------------------------------------------------------------------
# 1. 这个开关到底有没有用
# --------------------------------------------------------------------------
def check_base_url(settings, exchange) -> None:
    section("1. 实际请求的域名 vs BINANCE_DEMO_BASE_URL")

    api = exchange.urls.get("api", {})
    spot = api.get("spot") or api.get("public") or "?"
    show("BINANCE_DEMO_BASE_URL (.env)", settings.binance_demo_base_url)
    show("ccxt 实际 spot 端点", spot)

    # 实际端点是 ccxt 内置的 demo 域名（enable_demo_trading(True) 没得选），
    # .env 那个值只是**碰巧**与它相同 —— 它根本没有被传给 ccxt。
    # 两者相等不能证明开关生效，只能证明默认值撞对了。
    if isinstance(spot, str) and spot.startswith(settings.binance_demo_base_url.rstrip("/")):
        print(f"  {OK} 端点是 demo-api.binance.com")
        print(
            f"  {WARN} 但 .env 的 BINANCE_DEMO_BASE_URL 未被任何代码读取\n"
            f"         （grep 只在 config.py 里有定义，_client.py 从不引用）。\n"
            f"         端点由 ccxt 的 enable_demo_trading(True) 决定，与 .env 无关。\n"
            f"         改这一项不会改变任何行为；值相同只是撞对了默认值。"
        )
    else:
        print(
            f"  {WARN} .env 写的是 {settings.binance_demo_base_url}，实际却打 {spot}。\n"
            f"         该设置从未被传给 ccxt —— 一个没有作用的旋钮。"
        )


# --------------------------------------------------------------------------
# 2. 只读面
# --------------------------------------------------------------------------
async def read_only(demo: SpotDemoExchange) -> None:
    section("2. 只读面：markets / ticker / balance")

    markets = demo.exchange.markets or {}
    btc = markets.get(SYMBOL)
    show("markets 数量", len(markets))
    if btc:
        lim = btc.get("limits", {})
        show(f"{SYMBOL} minNotional", lim.get("cost", {}).get("min"))
        show(f"{SYMBOL} amount 精度", btc.get("precision", {}).get("amount"))
        show(f"{SYMBOL} price 精度", btc.get("precision", {}).get("price"))
        show(f"{SYMBOL} 可交易", btc.get("active"))

    ticker = await demo.fetch_ticker(SYMBOL)
    show("ticker.last", ticker.get("last"))

    balance = await demo.fetch_balance()
    free = {k: v for k, v in (balance.get("free") or {}).items() if v}
    show("非零余额", json.dumps(free, ensure_ascii=False))
    usdt = float((balance.get("free") or {}).get("USDT") or 0)
    if usdt >= QUOTE_QTY:
        print(f"  {OK} USDT 可用 {usdt}，够下 {QUOTE_QTY} 的买单")
    else:
        print(f"  {BAD} USDT 可用 {usdt}，不足以下 {QUOTE_QTY} 的买单")


# --------------------------------------------------------------------------
# 3 ~ 5. 真下单
# --------------------------------------------------------------------------
async def place_and_verify(demo: SpotDemoExchange) -> None:
    cid = new_client_order_id(IDEMPOTENCY_SEED)
    section(f"3. 提交市价买单 quoteOrderQty={QUOTE_QTY} newClientOrderId={cid}")

    raw: dict[str, Any]
    try:
        raw = await demo.exchange.create_order(
            SYMBOL, "market", "buy", None, None,
            {"newClientOrderId": cid, "quoteOrderQty": QUOTE_QTY},
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  {BAD} 提交失败: {type(exc).__name__}: {exc}")
        return

    print(f"  {OK} 交易所接受了这笔单")
    dump("create_order 原始回执", raw)

    update = normalize_order(raw)
    section("3b. 我们的 normalize_order() 从回执里读出了什么")
    show("status", update.status)
    show("exchange_order_id", update.exchange_order_id)
    show("filled_qty", update.filled_qty)
    show("avg_price", update.avg_price)
    show("cost_quote", update.cost_quote)
    show("fee", update.fee)

    # 关键：回执里到底有没有这些键，而不是我们以为有没有
    present = [k for k in ("filled", "average", "cost", "price", "amount", "status", "id") if k in raw]
    missing = [k for k in ("filled", "average", "cost", "id") if k not in raw]
    show("回执中存在的键", ", ".join(present) or "(无)")
    if missing:
        print(f"  {WARN} 回执里没有这些键: {', '.join(missing)} —— 离线测试的假回执假设它们存在")

    # ---------------------------------------------------------------- 4. 幂等
    section("4. 幂等：同一个 clientOrderId 再提交一次")
    try:
        dup = await demo.exchange.create_order(
            SYMBOL, "market", "buy", None, None,
            {"newClientOrderId": cid, "quoteOrderQty": QUOTE_QTY},
        )
        print(f"  {BAD} 交易所**接受了第二笔** —— 幂等键没起到去重作用！")
        dump("第二笔的回执", dup)
    except Exception as exc:  # noqa: BLE001
        print(f"  {OK} 交易所拒绝了重复提交: {type(exc).__name__}: {exc}")
        print("       这正是我们依赖的行为：重放不会变成第二笔单。")

    # ---------------------------------------------------------------- 5. 对账
    section("5. 对账：fetch_order 读到的与回执是否一致")
    if not update.exchange_order_id:
        print(f"  {BAD} 回执里没有单号，无从对账")
        return
    try:
        fetched = await demo.exchange.fetch_order(update.exchange_order_id, SYMBOL)
    except Exception as exc:  # noqa: BLE001
        print(f"  {BAD} fetch_order 失败: {type(exc).__name__}: {exc}")
        return

    fetched_update = normalize_order(fetched)
    show("回执 status", update.status)
    show("对账 status", fetched_update.status)
    show("回执 filled_qty", update.filled_qty)
    show("对账 filled_qty", fetched_update.filled_qty)
    show("回执 avg_price", update.avg_price)
    show("对账 avg_price", fetched_update.avg_price)
    if update.status == fetched_update.status and update.filled_qty == fetched_update.filled_qty:
        print(f"  {OK} 两条路径给出同一答案")
    else:
        print(f"  {WARN} 回执与对账不一致 —— execute_agent 会以对账结果为准，需确认这是对的")
    dump("fetch_order 原始回执", fetched)


async def end_to_end(settings) -> None:
    """整条链对着真交易所跑一遍，并且**连跑两次**。

    这一节才是 A-1 的验收：回查 → 落 pending → 提交 → 对账 → 落终态 → 联动
    trade_logs。第二次用同一个 trade_id 再跑，必须一笔都不提交 —— 那是"重放
    不会变第二笔"的**线上**证据，不是假 ccxt 里的断言。
    """
    section("6. 端到端：真下单 + 重放")
    await database.init_db()

    run_id = str(int(time.time()))
    trade_id = f"live-e2e-{run_id}"
    cid = new_client_order_id(trade_id)

    await TradeRepository().save_signal(
        trade_id=trade_id,
        signal={"action": "BUY", "symbol": SYMBOL, "amount": QUOTE_QTY,
                "confidence": 1.0, "reason": "线上验证"},
        market_data={"symbol": SYMBOL},
        status="submitted",
        risk_decision="approved",
        decision_id=f"live-e2e-d-{run_id}",
    )
    state = {
        "llm_signal": {"action": "BUY", "symbol": SYMBOL, "amount": QUOTE_QTY,
                       "confidence": 1.0, "reason": "线上验证"},
        "trade_log_id": trade_id,
        "decision_id": f"live-e2e-d-{run_id}",
    }

    show("trade_id", trade_id)
    show("client_order_id", cid)

    first = await run_execute_agent(state, settings=settings)
    show("第一次 status", first["status"])
    show("第一次 message", first.get("message"))

    row = await OrderRepository().find_by_client_id(cid)
    if row is None:
        print(f"  {BAD} 订单账本里没有这一行")
        return
    show("账本 status", row.status)
    show("交易所单号", row.exchange_order_id)
    show("成交数量", row.filled_qty)
    show("成交均价", row.avg_price)
    show("实际花费", row.cost_quote)
    show("量纲", row.amount_kind)

    trade = await TradeRepository().get_by_id(trade_id)
    show("trade_logs.status", trade.status if trade else "(没有)")
    show("trade_logs.quantity", trade.quantity if trade else "-")

    # ---- 重放：同一 trade_id 再跑一次 ----
    second = await run_execute_agent(state, settings=settings)
    show("第二次 status", second["status"])
    show("第二次 replayed", (second.get("order_result") or {}).get("replayed"))

    # 交易所那边这个 cid 到底有几笔 —— 只读，直接问
    async with SpotDemoExchange(settings) as demo:
        hist = await demo.exchange.fetch_orders(SYMBOL, limit=50)
    n = sum(1 for o in hist if o.get("clientOrderId") == cid)

    print()
    if n == 1 and second.get("order_result", {}).get("replayed") is True:
        print(f"  {OK} 同一决策在交易所只留下 1 笔订单，重放没有变成第二笔")
    elif n > 1:
        print(f"  {BAD} 交易所里有 {n} 笔同 cid 的订单 —— 重放**真的重复下单了**")
    else:
        print(f"  {WARN} 交易所里查到 {n} 笔（预期 1），请人工看一眼")


async def main() -> int:
    place = "--place" in sys.argv
    e2e = "--e2e" in sys.argv
    settings = get_settings()

    section("0. 环境")
    show("BINANCE_PROXY", settings.binance_proxy or "(未设置)")
    show("TRADE_SYMBOL", settings.trade_symbol)
    show("MAX_TRADE_AMOUNT", settings.max_trade_amount)
    if not settings.binance_configured:
        print(f"  {BAD} BINANCE_API_KEY/SECRET 未配置")
        return 2

    try:
        async with SpotDemoExchange(settings) as demo:
            check_base_url(settings, demo.exchange)
            await read_only(demo)
            if place:
                await place_and_verify(demo)
            elif not e2e:
                section("跳过下单（未加 --place）")
                print("  重跑加 --place 才会提交真实订单（Demo 账户）。")

        if e2e:
            await end_to_end(settings)
            await database.close_db()
    except Exception as exc:  # noqa: BLE001
        print(f"\n{BAD} 无法建连: {type(exc).__name__}: {exc}")
        print("     代理没开时就是这个结果（.env 的 BINANCE_PROXY）。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
