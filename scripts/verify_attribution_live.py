"""A-4 线上核对：可归因记录 R1–R3 真的答得出那三个问题吗。

要证明的是一条链，不是"某几列存在"：

    R1 为什么          <- prompt 列里是**实际发出**的完整 messages（含重试用的哪一份）
    R2 当时看到了什么   <- market_data / context 列里是**整份**上下文，不是摘要
    R3 当时手里有什么   <- equity / positions 是**决策那一刻**的，不是事后重读的

默认**只读**：用 `file:...?mode=ro` 打开真实库，一个字节都不写。库里的旧行
（A-4 之前写的）本来就是空的 —— 脚本会分开统计，不会拿旧行冒充失败，也不会
拿它们冒充成功。

`--synthetic` 在临时库上跑完整两轮 tick（假 LLM + 假交易所，完全离线）：
一轮 HOLD、一轮回撤破线强制平仓，然后把两张表的记录逐项读回来核对。

    PYTHONIOENCODING=utf-8 python scripts/verify_attribution_live.py
    PYTHONIOENCODING=utf-8 python scripts/verify_attribution_live.py --synthetic
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, ".")

if "--synthetic" in sys.argv:
    # 必须在第一次 get_settings() 之前设好：engine/session 是模块级单例。
    _tmp = tempfile.mkdtemp(prefix="bianca-attr-verify-")
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/verify.db"

from agent.config import Settings, get_settings  # noqa: E402
from agent.storage import database  # noqa: E402
from agent.storage.database import _ensure_columns  # noqa: E402

UTC_NOW = datetime.now(UTC)

# 归因新增的列。**只列 A-4 加的那些**，别把老列也混进来充数。
TRADE_COLS = ("context", "equity", "positions", "goal_id", "plan_id")
DECISION_COLS = ("prompt", "prompt_variant", "market_data", "goal_id", "plan_id")

# 合成场景的编造数字。
FAKE_HIGH = 100000.0
FAKE_PRICE = 50000.0
SYN_EQUITY = 1000.0

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--synthetic", action="store_true",
                    help="临时库 + 假 LLM/假交易所，跑完两轮 tick 再核对记录")
parser.add_argument("--rows", type=int, default=8, help="真实库模式下查最近几行（默认 8）")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def show(label: str, value: object) -> None:
    print(f"  {label:<26} {value}")


def line(ok: bool, text: str) -> None:
    print(f"  [{'OK ' if ok else 'FAIL'}] {text}")


def note(text: str) -> None:
    print(f"  [  ] {text}")


def _sqlite_path(url: str) -> str:
    """`sqlite+aiosqlite:///./data/bianca.db` -> `./data/bianca.db`。"""
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if url.startswith(prefix):
            return url[len(prefix):]
    raise SystemExit(f"看不懂的 DATABASE_URL：{url}")


def _open_ro(path: str) -> sqlite3.Connection:
    """**只读**打开。这个进程不该往真实库里写哪怕一列。"""
    return sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def _filled(amount: float) -> dict[str, Any]:
    """假回执。`filled` 跟着请求量走，否则对账会认为只成交了一部分。"""
    return {"id": 91001, "status": "closed", "filled": amount, "average": FAKE_PRICE,
            "cost": amount * FAKE_PRICE, "fee": None, "clientOrderId": "cid"}


class _FakeCCXT:
    async def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self.created.append({"symbol": symbol, "side": side, "amount": float(amount)})
        return _filled(float(amount))

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self._last = _filled(0.0)

    async def fetch_order(self, order_id, symbol=None, params=None):
        import ccxt

        if (params or {}).get("origClientOrderId"):
            raise ccxt.OrderNotFound("unknown cid")  # 交易所不认得 -> 可安全提交
        return self._last

    async def close(self):
        return None


async def _run_synthetic(rows_wanted: int) -> int:  # noqa: ARG001
    """临时库里跑两轮真 tick，再读回来核对。全程不出网。"""
    from agent.graph import analysis_agent, execute_agent, supervisor
    from agent.graph.supervisor import run_agent_tick
    from agent.llm.analyzer import AnalysisRun
    from agent.llm.schemas import TradeSignal
    from agent.portfolio.valuation import Position
    from agent.risk import close as close_module
    from agent.risk import engine as engine_mod
    from agent.storage.repository import (
        DecisionRepository,
        EquityRepository,
        TradeRepository,
    )

    settings = get_settings()
    await database.init_db()

    workdir = Path(tempfile.mkdtemp(prefix="bianca-attr-tick-"))
    # 检查点别写进仓库的 data/ —— 那一份是跑着的东西，核对脚本不该弄脏它
    type(settings).data_dir = property(lambda self: workdir)  # type: ignore[misc]

    equity_repo = EquityRepository()
    phase = {"n": 0}

    async def _snap() -> None:
        """代替真实的权益快照采集：真实的那条要连交易所。

        时间戳**必须逐张递增且互不相同**。两轮 tick 在同一秒内跑完，若都用
        `now()`，`latest()` 取回的是哪一张就全凭 SQLite 的返回顺序 —— 第一版正是
        如此：第二轮拿回了第一轮那张权益 1000 的快照，回撤算成 0，清仓没触发，
        整段核对静默失效。
        """
        if phase["n"] == 0:
            # 第一轮**不能破线** —— 破了的话巡检会先把仓平掉，这一轮就不再是干净的
            # HOLD 样本（第一版合成就是这么写的，日志里冒出两笔平仓，HOLD 那轮
            # 记的持仓其实已经被平了）。
            await equity_repo.save(
                cash_quote=SYN_EQUITY - 99.0, holdings_value=99.0, equity=SYN_EQUITY,
                positions=[{"asset": "BTC", "quantity": 0.001, "price": 99000.0,
                            "value_quote": 99.0}],
                unpriced=[], gaps=[], notes=["合成：正常"],
                captured_at=(UTC_NOW - timedelta(minutes=30)).isoformat(),
            )
            return
        # 第二轮：先一张高权益峰值，再一张跌了 6% 的当前值 —— 回撤由此成立。
        # 数字必须真的对得上：峰 1000、当前 940 才是 6%，超过 5% 上限。
        # （第一版把两张都写成 940，算出来 drawdown=0，日志里却写着"回撤破线"。）
        await equity_repo.save(
            cash_quote=SYN_EQUITY, holdings_value=0.0, equity=SYN_EQUITY, positions=[],
            unpriced=[], gaps=[], notes=["合成：峰值"],
            captured_at=(UTC_NOW - timedelta(minutes=20)).isoformat(),
        )
        await equity_repo.save(
            cash_quote=SYN_EQUITY - 260.0, holdings_value=200.0, equity=SYN_EQUITY - 60.0,
            positions=[{"asset": "BTC", "quantity": 0.004, "price": FAKE_PRICE,
                        "value_quote": 200.0}],
            unpriced=[], gaps=[], notes=["合成：当前"],
            captured_at=(UTC_NOW - timedelta(minutes=10)).isoformat(),
        )

    supervisor._capture_equity_snapshot = _snap  # type: ignore[assignment]

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

    fake = _FakeCCXT()
    execute_agent.SpotDemoExchange = _FakeDemo  # type: ignore[assignment]

    class _StubAnalyzer:
        def __init__(self, cfg):
            pass

        async def analyze(self, market_data):
            return AnalysisRun(
                signal=TradeSignal(action="HOLD", symbol="BTCUSDT", amount=None,
                                   confidence=0.0, reason="合成：观望"),
                raw_output='{"action":"HOLD"}', prompt_summary="BTCUSDT last=50000",
                messages=[{"role": "system", "content": "你是交易分析器"},
                          {"role": "user", "content": f"行情 {market_data.get('symbol')}"}],
                variant="normal",
            )

    analysis_agent.MarketAnalyzer = _StubAnalyzer  # type: ignore[assignment]

    async def _fake_highs(assets, settings=None):
        return {a: FAKE_HIGH for a in assets}

    engine_mod.fetch_recent_highs = _fake_highs  # type: ignore[assignment]

    market = {"symbol": "BTCUSDT", "last": FAKE_PRICE, "bid": FAKE_PRICE,
              "ask": FAKE_PRICE, "candles_1h": [{"t": i, "c": FAKE_PRICE} for i in range(25)]}

    section("0. 跑第一轮 tick（HOLD —— 记的是「没动手」那一类）")
    phase["n"] = 0
    tick_a = await run_agent_tick(market_data=market, settings=settings,
                                  goal_id="G-SYN", plan_id="P-SYN")
    show("status", tick_a.get("status"))
    show("trade_log_id", tick_a.get("trade_log_id"))
    show("decision_id", tick_a.get("decision_id"))

    section("1. 跑第二轮 tick（回撤 6% 破线 -> 清仓）")
    phase["n"] = 1
    tick_b = await run_agent_tick(market_data=market, settings=settings,
                                  goal_id="G-SYN", plan_id="P-SYN")
    forced = tick_b.get("force_close") or []
    show("status", tick_b.get("status"))
    show("强制平仓笔数", len(forced))
    for r in forced:
        show(f"  {r['asset']}", f"rule={r['rule']} qty={r['quantity']} status={r['status']}")

    ok = True
    if not forced:
        line(False, "第二轮没有触发强制平仓 —— 合成场景没造成功，下面的核对不成立")
        return 1
    line(all(r["rule"] == "drawdown" for r in forced),
         f"回撤破线 -> 清仓 {len(forced)} 笔，rule=drawdown（账户级，不是单标的止损）")
    line(forced[0]["quantity"] * FAKE_PRICE > settings.max_trade_amount,
         f"平仓额 {forced[0]['quantity'] * FAKE_PRICE:.0f} USDT 超过单笔上限 "
         f"{settings.max_trade_amount} 仍然平掉了 —— 豁免生效（ADR-027）")

    # ------------------------------------------------------------ 读回来核对
    section("2. R1 —— 为什么：完整 prompt 存住了吗")
    decision = await DecisionRepository().get_by_id(tick_a["decision_id"])
    prompt = json.loads(decision.prompt or "null")
    show("prompt_summary", decision.prompt_summary)
    show("prompt 条数", None if prompt is None else len(prompt))
    ok &= bool(prompt)
    line(bool(prompt), "prompt 列里是实际发出的完整 messages，不是只留一行摘要")
    if prompt:
        show("  system 长度", len(prompt[0]["content"]))
        show("  user 长度", len(prompt[-1]["content"]))
    line(decision.prompt_variant == "normal", f"prompt_variant = {decision.prompt_variant}")
    md = json.loads(decision.market_data or "null")
    line(md == market, f"decision_logs.market_data 与传入的行情一字不差（K 线 {len(md.get('candles_1h', []))} 根）")
    line(decision.goal_id == "G-SYN" and decision.plan_id == "P-SYN",
         f"链头留位：goal_id={decision.goal_id} plan_id={decision.plan_id}")

    section("3. R2/R3 —— HOLD 那一轮：当时看到了什么、手里有什么")
    hold = await TradeRepository().get_by_id(tick_a["trade_log_id"])
    ctx = json.loads(hold.context or "null")
    show("status / risk_decision", f"{hold.status} / {hold.risk_decision}")
    show("equity", hold.equity)
    show("positions", hold.positions)
    line(ctx is not None and ctx.get("market_data") == market,
         "context.market_data 是整份行情，不是摘要")
    line(hold.equity == SYN_EQUITY, f"记的是决策那一刻的权益 {hold.equity}")
    line(hold.positions is not None and json.loads(hold.positions)[0]["asset"] == "BTC",
         "持仓是决策那一刻的，不是事后重读的")
    line(hold.goal_id == "G-SYN" and hold.plan_id == "P-SYN",
         f"链头留位：goal_id={hold.goal_id} plan_id={hold.plan_id}")

    section("4. R2/R3 —— 强制平仓那一轮：不看 K 线，但同样要答得出")
    fc_row = await TradeRepository().get_by_id(forced[0]["trade_log_id"])
    fc_ctx = json.loads(fc_row.context or "null")
    show("risk_decision", fc_row.risk_decision)
    show("equity", fc_row.equity)
    show("context.portfolio", json.dumps(fc_ctx.get("portfolio"), ensure_ascii=False))
    line(fc_row.risk_decision == "force_close", "记录说得出这是一笔强制平仓")
    line(fc_ctx.get("portfolio", {}).get("highs", {}).get("BTC") == FAKE_HIGH,
         "当时的观测高点记下来了 —— 止损正是拿它判的")
    line(fc_ctx.get("portfolio", {}).get("drawdown_pct") is not None,
         "当时的回撤记下来了 —— 这是它动手的理由")
    line(fc_ctx.get("market_data") == {"symbol": "BTCUSDT"},
         "没有 K 线就如实为空，不编一份")

    section("5. 归因链能不能串起来")
    for label, row_id in (("HOLD", tick_a["trade_log_id"]),
                          ("强制平仓", forced[0]["trade_log_id"])):
        row = await TradeRepository().get_by_id(row_id)
        chain = (f"goal={row.goal_id} plan={row.plan_id} "
                 f"decision={row.decision_id} trade={row.id}")
        show(label, chain)
    hold_row = await TradeRepository().get_by_id(tick_a["trade_log_id"])
    line(hold_row.decision_id is not None,
         "trade_log -> decision_log 的指针在（判断与成交对得上）")

    print()
    note("**强制平仓那行没有 goal / decision 指针 —— 这是现状，不是漏记。**")
    note("  清仓巡检是账户级的（ADR-027），它不属于任何目标、也没有 LLM 判断，")
    note("  所以那里本来就是 None。等阶段 C 有了目标对象，需要决定一件事：")
    note("  某目标持仓被止损平掉时，这条记录该不该认领到那个目标名下。")
    note("  现在不装作已解决 —— 那会让 §6.3 的 R1 看着比实际完整。")

    print("\n结论：见上面每一行的 [ OK ] / [FAIL]。")
    return 0 if ok else 1


def _migration_check(real_path: str) -> None:
    """在**副本**上跑真实的 `_ensure_columns`，报告列会不会补上、数据有没有动。"""
    from sqlalchemy import create_engine

    section("2. 幂等迁移（在**副本**上跑，真实库不碰）")
    if not Path(real_path).exists():
        note(f"真实库还不存在（{real_path}）—— 应用首次启动时会 create_all 建全")
        return

    copy_path = Path(tempfile.mkdtemp(prefix="bianca-attr-copy-")) / "copy.db"
    shutil.copy2(real_path, copy_path)

    before_con = _open_ro(str(copy_path))
    before_cols = {"trade_logs": _columns(before_con, "trade_logs"),
                   "decision_logs": _columns(before_con, "decision_logs")}
    before_rows = before_con.execute("SELECT COUNT(*) FROM trade_logs").fetchone()[0]
    before_con.close()

    missing_before = sorted(
        (set(TRADE_COLS) - before_cols["trade_logs"])
        | (set(DECISION_COLS) - before_cols["decision_logs"])
    )
    show("副本里已有的行数", before_rows)
    show("尚缺的列", ", ".join(missing_before) or "（一个都不缺）")

    engine = create_engine(f"sqlite:///{copy_path.as_posix()}")
    with engine.begin() as conn:
        _ensure_columns(conn)
    engine.dispose()

    after_con = _open_ro(str(copy_path))
    after_cols = {"trade_logs": _columns(after_con, "trade_logs"),
                  "decision_logs": _columns(after_con, "decision_logs")}
    after_rows = after_con.execute("SELECT COUNT(*) FROM trade_logs").fetchone()[0]
    after_con.close()

    line(not (set(TRADE_COLS) - after_cols["trade_logs"]), "trade_logs 补齐了 " + ", ".join(TRADE_COLS))
    line(not (set(DECISION_COLS) - after_cols["decision_logs"]), "decision_logs 补齐了 " + ", ".join(DECISION_COLS))
    line(after_rows == before_rows, f"行数没变（{before_rows} -> {after_rows}）—— 迁移不动数据")

    if missing_before:
        note("真实库会在**应用下次启动**时自动补上这些列（init_db 每次启动都跑）")
    shutil.rmtree(copy_path.parent, ignore_errors=True)


# 判定"这行是不是 A-4 之后写的"用哪一列。
#
# **不能用 `context`。** 2026-10-10 的观察脚本就往 `context` 里写过东西，但那些是
# `{"market_data": {"last": 100.0}}` 这种琐碎值，且 `equity` / `positions` 全空 ——
# 拿 `context IS NOT NULL` 当判据，会把 9 行旧记录读成"A-4 已生效"，给一个假的 OK。
# `equity` / `prompt` 不一样：A-4 之前**没有任何代码**写过它们，非空即证明新代码跑过。
MARKER = {"trade_logs": "equity", "decision_logs": "prompt"}
# `context` 单独统计：非空但 equity 空 = 旧脚本写的残缺行。
SOFT_MARKER = {"trade_logs": "context", "decision_logs": "market_data"}


def _scan_real(real_path: str, limit: int) -> int:
    """只读扫最近几行，把"A-4 之前写的"与"A-4 之后写的"分开统计。"""
    section("3. 真实库里最近的行（**只读**，一个字节没写）")
    if not Path(real_path).exists():
        note(f"真实库还不存在（{real_path}）")
        return 0

    con = _open_ro(real_path)
    have = {"trade_logs": _columns(con, "trade_logs"),
            "decision_logs": _columns(con, "decision_logs")}
    show("trade_logs 已有归因列", ", ".join(sorted(set(TRADE_COLS) & have["trade_logs"])) or "（无）")
    show("decision_logs 已有归因列", ", ".join(sorted(set(DECISION_COLS) & have["decision_logs"])) or "（无）")

    for table, cols in (("trade_logs", TRADE_COLS), ("decision_logs", DECISION_COLS)):
        missing = [c for c in cols if c not in have[table]]
        if missing:
            note(f"{table} 还缺 {', '.join(missing)} —— 应用下次启动会补；在那之前这张表"
                 f"没有可核对的归因字段")
            continue

        rows = con.execute(
            f"SELECT id, created_at, {', '.join(cols)} FROM {table} "
            f"ORDER BY created_at DESC LIMIT {limit}"
        ).fetchall()
        if not rows:
            note(f"{table} 里还没有行")
            continue

        marker, soft = MARKER[table], SOFT_MARKER[table]
        total, hard, soft_n = con.execute(
            f"SELECT COUNT(*), "
            f"SUM(CASE WHEN {marker} IS NOT NULL THEN 1 ELSE 0 END), "
            f"SUM(CASE WHEN {soft} IS NOT NULL THEN 1 ELSE 0 END) FROM {table}"
        ).fetchone()
        show(f"{table} 总行数", total)
        show(f"  其中 {marker} 非空", f"{hard}（判据：只有 A-4 之后的代码写过它）")
        show(f"  其中 {soft} 非空", f"{soft_n}（含旧观察脚本写的残缺行，不能当判据）")

        print(f"  {'id':<38} {'created_at':<26} 归因字段")
        for r in rows:
            cells = []
            for i, c in enumerate(cols):
                v = r[i + 2]
                if v is None:
                    cells.append(f"{c}=—")
                elif c in ("context", "prompt", "market_data", "positions"):
                    cells.append(f"{c}={len(str(v))}B")
                else:
                    cells.append(f"{c}={v}")
            print(f"  {r[0]:<38} {r[1]:<26} {', '.join(cells)}")

        if hard:
            line(True, f"{table} 有 {hard} 行是 A-4 之后写的，归因字段有值")
        elif soft_n:
            line(False, f"{table} 的 {soft_n} 行 `{soft}` 非空但 `{marker}` 全空 —— "
                        f"它们是**旧代码**写的，不能算作 A-4 已生效")
        else:
            note(f"{table} 里还没有 A-4 之后写的行 —— 这些列要从下一次 tick 起才会有值。"
                 f"这不是失败，但也还没被现实验证过；`--synthetic` 能立刻验一遍。")

    con.close()
    return 0


async def main() -> int:
    args = parser.parse_args()
    settings = get_settings()
    real_path = _sqlite_path(settings.database_url)

    print("A-4 线上核对 —— 可归因记录 R1–R3")
    print(f"UTC 现在   : {UTC_NOW.isoformat()}")
    print(f"数据库     : {settings.database_url}")
    print(f"模式       : {'合成（临时库，数字是编的）' if args.synthetic else '只读（真实库，不写）'}")

    if args.synthetic:
        return await _run_synthetic(args.rows)

    section("1. 环境")
    show("库文件", real_path)
    show("存在", Path(real_path).exists())
    show("库文件大小", f"{Path(real_path).stat().st_size / 1024:.1f} KB" if Path(real_path).exists() else "—")
    line(True, "以 mode=ro 打开 —— 这个进程写不进真实库")

    _migration_check(real_path)
    _scan_real(real_path, args.rows)

    print("\n结论：见上面每一行的 [ OK ] / [FAIL]。")
    print("只读模式不会替真实库补列；--synthetic 才能在离线环境里把整条链验一遍。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
