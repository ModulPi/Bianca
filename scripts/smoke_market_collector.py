"""采集器端到端冒烟：真连 Binance WS（走代理），验证「重连先补缺口 + 收盘 bar 落库」。

用法：python scripts/smoke_market_collector.py [等待秒数]

验收点：
1. 连上后先补齐 lookback 窗口内的缺口（不依赖 WS 收到任何消息）
2. WS 推送中只有 k.x == true 的 bar 落库，且落在正确的分钟边界
3. 停机干净，不因 websockets 16.0 的 teardown bug 抛异常
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import Settings  # noqa: E402
from agent.market.bars import interval_ms, last_closed_bar_open  # noqa: E402
from agent.market.collector import MarketCollector, market_status_detail  # noqa: E402
from agent.market.repository import KlineRepository  # noqa: E402
from agent.market.storage import close_market_db, init_market_db  # noqa: E402


async def main() -> int:
    wait_s = int(sys.argv[1]) if len(sys.argv) > 1 else 100

    settings = Settings(
        market_backfill_on_start=False,  # 冒烟不跑 15 分钟的全量回补
        market_collector_autostart=False,
        log_level="INFO",
    )
    symbol, interval = settings.market_symbol_list[0], settings.market_interval

    await init_market_db()
    repo = KlineRepository()

    before = await repo.count_all(symbol, interval)
    before_last = await repo.last_bar_time(symbol, interval)
    print(f"symbol={symbol} interval={interval} proxy={settings.market_proxy or '(none)'}")
    print(f"db rows before = {before}, last_bar = {before_last}")

    collector = MarketCollector(settings=settings)
    await collector.start()
    print(f"collector started, waiting {wait_s}s ...\n")

    try:
        for elapsed in range(10, wait_s + 1, 10):
            await asyncio.sleep(10)
            snap = await collector.get_snapshot()
            print(
                f"  t+{elapsed:>3}s connected={snap.connected} "
                f"written={snap.bars_written} gaps_filled={snap.gaps_filled} "
                f"reconnects={snap.reconnects} err={snap.last_error}"
            )
    finally:
        stop_request_ms = int(datetime.now(UTC).timestamp() * 1000)
        stop_t0 = asyncio.get_running_loop().time()
        await collector.stop()
        stop_ms = (asyncio.get_running_loop().time() - stop_t0) * 1000

    detail = await market_status_detail(collector, settings)
    after = await repo.count_all(symbol, interval)
    after_last = await repo.last_bar_time(symbol, interval)

    status_ms = int(datetime.now(UTC).timestamp() * 1000)
    # 逐时刻对照：停机那一刻 vs 查状态那一刻，最近已收盘 bar 分别是哪根
    expected_at_stop = last_closed_bar_open(stop_request_ms, interval)
    expected_at_status = last_closed_bar_open(status_ms, interval)
    step = interval_ms(interval)

    print(f"\n--- timing ---")
    print(f"  stop requested at {_iso(stop_request_ms)} ({stop_request_ms})")
    print(f"  stop() took {stop_ms:.0f} ms")
    print(f"  status queried at {_iso(status_ms)} ({status_ms})")

    print("\n--- status ---")
    for key in (
        "last_bar_open_time", "last_closed_bar_open_time", "lag_seconds",
        "bars_written_session", "bars_count_24h", "gap_count_24h",
        "reconnects_session", "last_error", "database",
    ):
        print(f"  {key} = {detail[key]}")

    print("\n--- checks ---")
    rows_added = after - before
    print(f"rows added = {rows_added}")
    print(f"last_bar   = {after_last} ({_iso(after_last)})")
    print(f"  expected @stop   = {expected_at_stop} ({_iso(expected_at_stop)})")
    print(f"  expected @status = {expected_at_status} ({_iso(expected_at_status)})")

    ok = True
    # 硬性不变量：停机时最近的已收盘 bar 必须已落库。只允许落后的
    # 「边界那根」——收盘瞬间推送与停机可能相撞，缺口留给下次巡检补。
    if after_last is None or after_last < expected_at_stop - step:
        print(f"FAIL: last_bar 落后停机时应有的 {expected_at_stop} 超过一个周期")
        ok = False
    else:
        lag_bars = (expected_at_stop - after_last) // step
        print(f"OK   last_bar 相对停机时刻滞后 {lag_bars} 根（容忍 <=1 根边界竞态）")

    if after_last is not None and after_last % step != 0:
        print(f"FAIL: last_bar 未对齐 {interval} 边界")
        ok = False
    else:
        print(f"OK   last_bar 对齐 {interval} 边界")

    if detail["reconnects_session"]:
        print(f"NOTE 本次发生 {detail['reconnects_session']} 次重连（退避已生效，不算失败）")
    if detail["last_error"]:
        print(f"NOTE last_error = {detail['last_error']}")

    await close_market_db()
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def _iso(ms: int | None) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat() if ms else "-"


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
