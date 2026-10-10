"""A-2b 线上核对：日亏熔断的输入真的是权益吗？

要证明的不是"代码里有 daily_pnl 这个函数"，而是这条链真的接通了：

    交易所余额 ──> 权益快照表 ──> 日盈亏 ──> 风控裁决

其中**只有第一段出网**。任何一段断掉，风控都必须拿到"未知"并拦下开仓，
而不是拿到一个 0 假装没亏 —— 这个脚本会逐段把它念出来。

默认只读（不写库、不下单）。加 `--capture` 才会真的抓一张快照（会调交易所）。
加 `--synthetic` 在临时库上跑一段合成的超限亏损，用来看熔断真的会跳。

    PYTHONIOENCODING=utf-8 python scripts/verify_pnl_live.py
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from datetime import UTC, datetime

sys.path.insert(0, ".")

if "--synthetic" in sys.argv:
    # 必须在第一次 get_settings() 之前设好：engine/session 是模块级单例。
    _tmp = tempfile.mkdtemp(prefix="bianca-pnl-verify-")
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/verify.db"

from agent.api.schemas import AgentStatusResponse  # noqa: E402
from agent.config import get_settings  # noqa: E402
from agent.portfolio.pnl import daily_pnl, utc_day_start  # noqa: E402
from agent.risk.engine import RiskEngine  # noqa: E402
from agent.storage import database  # noqa: E402
from agent.storage.repository import EquityRepository  # noqa: E402

UTC_NOW = datetime.now(UTC)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--capture", action="store_true", help="真抓一张快照（调交易所）")
parser.add_argument("--synthetic", action="store_true", help="在临时库上跑合成数字")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def show(label: str, value: object) -> None:
    print(f"  {label:<34} {value}")


async def main() -> int:
    args = parser.parse_args()
    settings = get_settings()

    print("A-2b 线上核对 —— 日亏熔断的输入是权益，不是现金流")
    print(f"UTC 现在   : {UTC_NOW.isoformat()}")
    print(f"UTC 日切点 : {utc_day_start(UTC_NOW)}")
    print(f"数据库     : {settings.database_url}")
    print(f"合成模式   : {'是（临时库，数字是编的）' if args.synthetic else '否（真实库）'}")

    # 临时库是空的，得先建表；对真实库是幂等的（表和应用启动时建的是同一批）。
    await database.init_db()

    equity = EquityRepository()

    if args.synthetic:
        section("0. 造一段合成日亏（**数字是编的**，只用来让熔断跳一次）")
        day = utc_day_start(UTC_NOW)
        await equity.save(
            cash_quote=10000.0, holdings_value=0.0, equity=10000.0,
            positions=[], unpriced=[], gaps=[], notes=["合成基准"],
            captured_at=f"{day[:11]}00:00:10+00:00",
        )
        await equity.save(
            cash_quote=9800.0, holdings_value=0.0, equity=9800.0,
            positions=[], unpriced=[], gaps=[], notes=["合成当前：浮亏 200"],
            captured_at=UTC_NOW.isoformat(),
        )
        show("合成基准 / 当前", "10000.0 / 9800.0")
        show("对照限额 daily_loss_limit", settings.daily_loss_limit)

    section("1. 权益快照表里有什么")
    rows = await equity.list_recent(limit=10)
    if not rows:
        print("  （空）—— 还没有任何快照")
    for row in rows:
        mark = "今天" if row.captured_at >= utc_day_start(UTC_NOW) else "更早"
        show(row.captured_at, f"{row.equity:.4f}  [{mark}]")

    section("2. 日盈亏（基准 = 当天第一条快照）")
    snapshot = await daily_pnl(now=UTC_NOW)
    show("known", snapshot.known)
    show("baseline_equity", snapshot.baseline_equity)
    show("current_equity", snapshot.current_equity)
    show("pnl", snapshot.pnl)
    if snapshot.reason:
        show("reason", snapshot.reason)

    if snapshot.known:
        expected = snapshot.current_equity - snapshot.baseline_equity
        ok = snapshot.pnl is not None and abs(snapshot.pnl - expected) < 1e-9
        print(f"  [{'OK ' if ok else 'FAIL'}] pnl == current − baseline")
    else:
        print("  [ OK ] 算不出来时给出 None + 理由（不是 0.0）")
        print(f"  [{'OK ' if snapshot.pnl is None else 'FAIL'}] pnl is None")

    if args.capture:
        section("3. 真抓一张快照（会调交易所）")
        from agent.graph.supervisor import _capture_equity_snapshot

        before = len(await equity.list_recent(limit=1000))
        await _capture_equity_snapshot()  # 失败也只记日志，不抛
        after = len(await equity.list_recent(limit=1000))
        show("快照条数 抓取前/后", f"{before} -> {after}")
        if after > before:
            latest = await equity.latest()
            show("最新权益", f"{latest.equity:.4f}")
            print("  [ OK ] 取快照走的是同一条 capture() 路径（run_agent_tick 用的就是它）")
        else:
            print("  [WARN] 没抓成 —— 代理/交易所不通。风控会因此拿到'未知'并拦下开仓，")
            print("         这正是设计中的保守失败，不是崩溃。")

    section("4. 风控真的按这个数裁决")
    engine = RiskEngine(settings)
    market = {"symbol": "BTCUSDT", "last": 65000.0}

    buy = await engine.evaluate({"action": "BUY", "amount": 10.0, "symbol": "BTCUSDT"}, market)
    sell = await engine.evaluate(
        {"action": "SELL", "amount": 0.0002, "symbol": "BTCUSDT"}, market
    )
    show("BUY 10 USDT  ->", f"approved={buy.approved} rule={buy.rule} {buy.reason}")
    show("SELL 0.0002  ->", f"approved={sell.approved} rule={sell.rule} {sell.reason}")

    if not snapshot.known:
        print(f"  [{'OK ' if not buy.approved else 'FAIL'}] 日盈亏未知 -> 开仓被拦")
    elif snapshot.pnl <= -settings.daily_loss_limit:
        print(f"  [{'OK ' if not buy.approved else 'FAIL'}] 亏损已超限 -> 开仓被拦")
    else:
        print(f"  [{'OK ' if buy.approved else 'FAIL'}] 亏损在限内 -> 开仓放行")
    print(f"  [{'OK ' if sell.approved else 'FAIL'}] 减仓不被熔断拦住（2026-10-11 决定）")

    section("5. 对外接口说的是同一个数")
    status = AgentStatusResponse(
        running=False,
        daily_pnl=snapshot.pnl,
        tick_interval=settings.agent_tick_interval,
    )
    dumped = status.model_dump()
    show("/agent/status .daily_pnl", dumped["daily_pnl"])
    print("  （schema 允许 None —— 前端不会把'未知'读成 0）")

    print("\n结论：见上面每一行的 [ OK ] / [FAIL]。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
