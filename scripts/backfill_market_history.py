"""全量历史回补：显式区间，落库后可自检覆盖率与残余缺口。

**为什么不用 `backfill_history()`**：它的续传起点是库内 `max(time) + 1 周期`，
只要库里已有最新 bar 就会判定"已补到最新"并直接短路返回。实测库内是
「2017 一段 + 近期一段」两座孤岛，`max(time)` = 今天，于是 `requests=0` ——
中间 478 万根的洞一个都不会补。**全量回补必须显式给区间**，不能走增量续传。

反过来说，`backfill_history()` 的设计对它的目标（冷启动后持续跟随 + 断点续传
尾部）是正确的，只是它不覆盖"补中间的洞"这个场景。本脚本补这个位。

区间如何取：默认从 `MARKET_BACKFILL_START`（2017-08-17）补到最近一根已完成 bar。
起点那 3000 根已存在也没关系 —— 落库幂等（主键 + ON CONFLICT DO NOTHING），
重复拉取只浪费 3 个分片。

用法：
    python -u scripts/backfill_market_history.py                # 全量，并发取配置值
    python -u scripts/backfill_market_history.py --concurrency 8
    python -u scripts/backfill_market_history.py --start 2024-01-01 --end 2024-03-01
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from agent.config import get_settings  # noqa: E402
from agent.market.backfill import (  # noqa: E402
    BackfillStats,
    backfill_range,
    parse_backfill_start,
)
from agent.market.bars import interval_ms, last_closed_bar_open  # noqa: E402
from agent.market.models import Kline  # noqa: E402
from agent.market.storage import (  # noqa: E402
    close_market_db,
    get_market_session_factory,
    init_market_db,
)


def _iso(ms: int | None) -> str:
    if ms is None:
        return "-"
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M")


def _fmt_dur(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s" if m else f"{s}s"


async def _coverage(symbol: str, interval: str) -> tuple[int, int | None, int | None]:
    factory = get_market_session_factory()
    async with factory() as db:
        row = (
            await db.execute(
                select(
                    func.count(Kline.time), func.min(Kline.time), func.max(Kline.time)
                ).where(Kline.symbol == symbol, Kline.interval == interval)
            )
        ).one()
    total, first, last = row
    return int(total or 0), first, last


async def _largest_gaps(
    symbol: str, interval: str, *, limit: int = 5
) -> list[tuple[int, int, int]]:
    """最大的残余缺口（按缺口大小降序）。用 SQL 窗口函数在库内算，
    不把时间戳拉进 Python —— 全量后是 480 万行级别。"""
    step = interval_ms(interval)
    factory = get_market_session_factory()
    async with factory() as db:
        prev = func.lag(Kline.time).over(order_by=Kline.time.asc()).label("prev")
        windowed = (
            select(Kline.time.label("cur"), prev)
            .where(Kline.symbol == symbol, Kline.interval == interval)
            .subquery()
        )
        gap_ms = windowed.c.cur - windowed.c.prev
        stmt = (
            select(windowed.c.prev, windowed.c.cur, gap_ms.label("gap_ms"))
            .where(windowed.c.prev.is_not(None), gap_ms > step)
            .order_by(gap_ms.desc())
            .limit(limit)
        )
        rows = (await db.execute(stmt)).all()
    return [(int(p), int(c), int(g)) for p, c, g in rows]


async def _report(symbol: str, interval: str) -> bool:
    """打印覆盖率与残余缺口。返回 True 表示无缺口（连续）。"""
    step = interval_ms(interval)
    total, first, last = await _coverage(symbol, interval)
    print("\n--- 落库自检 ---")
    print(f"总行数      : {total:,}")
    print(f"首根 / 末根 : {_iso(first)}  →  {_iso(last)}")

    expected = coverage_pct = None
    if first is not None and last is not None:
        expected = (last - first) // step + 1
        coverage_pct = total / expected * 100
        print(f"应有行数    : {expected:,}")
        print(f"覆盖率      : {coverage_pct:.4f}%")

    gaps = await _largest_gaps(symbol, interval)
    if not gaps:
        print("残余缺口    : 无（区间连续）")
        return True

    print(f"残余缺口    : 最大 {len(gaps)} 个如下")
    for prev_t, cur_t, gap in gaps:
        missing = gap // step - 1
        print(f"  {_iso(prev_t)} → {_iso(cur_t)}  缺 {missing:,} 根")
    return False


async def main() -> int:
    parser = argparse.ArgumentParser(description="Bianca 行情全量历史回补")
    parser.add_argument("--symbol", default=None, help="默认取配置里的第一个标的")
    parser.add_argument("--interval", default=None, help="默认取配置 MARKET_INTERVAL")
    parser.add_argument("--start", default=None, help="YYYY-MM-DD，默认 MARKET_BACKFILL_START")
    parser.add_argument("--end", default=None, help="YYYY-MM-DD，默认最近一根已完成 bar")
    parser.add_argument("--concurrency", type=int, default=None, help="默认取配置值（5）")
    parser.add_argument("--progress-every", type=int, default=50, help="每 N 个分片打一行进度")
    args = parser.parse_args()

    settings = get_settings()
    symbol = args.symbol or settings.market_symbol_list[0]
    interval = args.interval or settings.market_interval

    if not settings.market_proxy:
        print("警告: BINANCE_PROXY 未配置，大陆网络下直连会超时", file=sys.stderr)

    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    start_ms = (
        parse_backfill_start(args.start) if args.start else parse_backfill_start(settings.market_backfill_start)
    )
    end_ms = (
        parse_backfill_start(args.end) + 24 * 3_600_000 - interval_ms(interval)
        if args.end
        else last_closed_bar_open(now_ms, interval)
    )
    concurrency = args.concurrency or settings.market_backfill_concurrency

    if start_ms > end_ms:
        print(f"区间为空（start {_iso(start_ms)} > end {_iso(end_ms)}），无事可做")
        return 0

    total_bars = (end_ms - start_ms) // interval_ms(interval) + 1
    print(f"标的 / 周期 : {symbol} {interval}")
    print(f"回补区间    : {_iso(start_ms)}  →  {_iso(end_ms)}")
    print(f"应有 bar 数 : {total_bars:,}")
    print(f"并发        : {concurrency}")
    print(f"代理        : {settings.market_proxy or '(直连)'}")
    print("-" * 60, flush=True)

    await init_market_db()
    try:
        started = asyncio.get_running_loop().time()
        stats = BackfillStats()
        total_ranges = [0]

        def on_progress(done: int, total: int, st: BackfillStats) -> None:
            total_ranges[0] = total
            if done % args.progress_every and done != total:
                return
            elapsed = asyncio.get_running_loop().time() - started
            rate = done / elapsed if elapsed else 0
            eta = (total - done) / rate if rate else 0
            print(
                f"[{done:>5}/{total}] {done / total * 100:5.1f}%  "
                f"新增 {st.rows_inserted:>9,} 行  失败 {st.failed_chunks}  "
                f"已用 {_fmt_dur(elapsed)}  预计剩余 {_fmt_dur(eta)}",
                flush=True,
            )

        stats = await backfill_range(
            symbol,
            interval,
            start_ms,
            end_ms,
            settings=settings,
            concurrency=concurrency,
            on_progress=on_progress,
        )
        elapsed = asyncio.get_running_loop().time() - started
    finally:
        await close_market_db()

    print("-" * 60)
    print("--- 回补统计 ---")
    for k, v in stats.as_dict().items():
        print(f"{k:<15}: {v}")
    print(f"{'wall_clock_s':<15}: {elapsed:.1f}")

    continuous = await _report_after_close(symbol, interval)
    if stats.failed_chunks:
        print(f"\n注意: {stats.failed_chunks} 个分片最终失败 —— 缺口自检见上，可重跑本脚本补齐")
        return 1
    return 0 if continuous else 2


async def _report_after_close(symbol: str, interval: str) -> bool:
    """自检要重新开库 —— 上面 finally 里已经 close 了。"""
    await init_market_db()
    try:
        return await _report(symbol, interval)
    finally:
        await close_market_db()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
