"""历史回补：REST 分页拉取 → 幂等落库。

三条设计约束（设计文档 §2.3 / ADR-014）：
1. **幂等**：靠 KlineRepository 的 ON CONFLICT DO NOTHING，回补可随意重放
2. **断点续传**：不维护进度表 —— 续传点由库里已有的 max(time) 直接得出
3. **必须重试**：实测代理存在偶发读超时，单次失败不能中断整轮回补
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from agent.config import Settings, get_settings
from agent.market.bars import (
    chunk_ranges,
    count_bars,
    find_gaps,
    interval_ms,
    last_closed_bar_open,
    parse_rest_row,
)
from agent.market.repository import KlineRepository

logger = logging.getLogger(__name__)

_PATH = "/api/v3/klines"
_CHUNK_BARS = 1000  # Binance 单次上限
_MAX_RETRIES = 4


@dataclass
class BackfillStats:
    requests: int = 0
    rows_received: int = 0
    rows_inserted: int = 0
    failed_chunks: int = 0
    ranges: int = 0
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: str | None = None

    @property
    def duration_s(self) -> float:
        end = datetime.fromisoformat(self.finished_at) if self.finished_at else datetime.now(UTC)
        return (end - datetime.fromisoformat(self.started_at)).total_seconds()

    def as_dict(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "rows_received": self.rows_received,
            "rows_inserted": self.rows_inserted,
            "failed_chunks": self.failed_chunks,
            "ranges": self.ranges,
            "duration_s": round(self.duration_s, 1),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def parse_backfill_start(value: str) -> int:
    """'2017-08-17' → epoch ms（UTC）。"""
    dt = datetime.strptime(value.strip(), "%Y-%m-%d").replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def _build_client(settings: Settings) -> httpx.AsyncClient:
    proxy = settings.market_proxy or None
    return httpx.AsyncClient(
        base_url=settings.market_rest_base_url,
        proxy=proxy,
        timeout=httpx.Timeout(30.0, connect=15.0),
        headers={"User-Agent": "bianca-market/0.1"},
    )


async def _fetch_chunk(
    client: httpx.AsyncClient,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    stats: BackfillStats,
) -> list[dict[str, Any]]:
    """拉一个分片，带指数退避重试。失败返回空列表（计入 failed_chunks）。"""
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": _CHUNK_BARS,
    }
    delay = 1.0
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = await client.get(_PATH, params=params)
            if resp.status_code == 200:
                stats.requests += 1
                raw = resp.json()
                rows = [r for r in (parse_rest_row(x, symbol, interval) for x in raw) if r]
                stats.rows_received += len(rows)
                return rows
            if resp.status_code in (418, 429):
                # 429 限流可退避重试；418 是 IP 封禁，继续重试只会加重
                if resp.status_code == 418:
                    logger.error("Binance 418 IP banned, aborting chunk %s-%s", start_ms, end_ms)
                    break
                retry_after = float(resp.headers.get("Retry-After", delay))
                logger.warning("rate limited (429), sleeping %.1fs", retry_after)
                await asyncio.sleep(retry_after)
            else:
                logger.warning("chunk %s-%s HTTP %s: %s", start_ms, end_ms, resp.status_code, resp.text[:120])
                await asyncio.sleep(delay)
        except Exception as exc:  # noqa: BLE001 — 代理抖动是常态，重试而非中断
            logger.debug("chunk %s-%s attempt %s failed: %s", start_ms, end_ms, attempt, exc)
            await asyncio.sleep(delay)
        delay *= 2

    stats.failed_chunks += 1
    return []


async def _run(
    client: httpx.AsyncClient,
    symbol: str,
    interval: str,
    ranges: list[tuple[int, int]],
    *,
    concurrency: int,
    stats: BackfillStats,
    on_progress: Any = None,
) -> BackfillStats:
    """并发跑完所有分片，每个分片完成即落库（内存占用与并发度同阶，不随跨度增长）。"""
    stats.ranges += len(ranges)
    if not ranges:
        stats.finished_at = datetime.now(UTC).isoformat()
        return stats

    repo = KlineRepository()
    semaphore = asyncio.Semaphore(concurrency)
    done = 0

    async def worker(start_ms: int, end_ms: int) -> None:
        nonlocal done
        async with semaphore:
            rows = await _fetch_chunk(client, symbol, interval, start_ms, end_ms, stats)
        if rows:
            # 不要写成 `stats.rows_inserted += await repo.insert_rows(rows)`：
            # 增强赋值会**先读左值、再 await**，并发 worker 于是都读到同一个旧值、
            # 各自加完再写回，互相覆盖。实测全量 478 万根少报 61.8 万（库内数据正确，
            # 只是统计口径失真）。先 await 拿到增量，再同步累加。
            inserted = await repo.insert_rows(rows)
            stats.rows_inserted += inserted
        done += 1
        if on_progress is not None:
            on_progress(done, len(ranges), stats)

    await asyncio.gather(*(worker(a, b) for a, b in ranges))
    stats.finished_at = datetime.now(UTC).isoformat()
    return stats


async def backfill_range(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    *,
    settings: Settings | None = None,
    concurrency: int | None = None,
    on_progress: Any = None,
) -> BackfillStats:
    """回补闭区间 [start_ms, end_ms]。已存在的 bar 会被跳过（幂等）。"""
    cfg = settings or get_settings()
    stats = BackfillStats()
    ranges = chunk_ranges(start_ms, end_ms, interval, bars_per_chunk=_CHUNK_BARS)
    async with _build_client(cfg) as client:
        return await _run(
            client,
            symbol,
            interval,
            ranges,
            concurrency=concurrency or cfg.market_backfill_concurrency,
            stats=stats,
            on_progress=on_progress,
        )


async def backfill_history(
    symbol: str,
    interval: str,
    *,
    settings: Settings | None = None,
    concurrency: int | None = None,
    on_progress: Any = None,
) -> BackfillStats:
    """冷启动回补：从配置起点（默认 2017-08-17）补到最近一根已完成 bar。

    断点续传：起点取 max(配置起点, 库里已有的 max(time) + 1 个周期)，
    因此中途中断后重跑会从断点继续，不会从头再来。
    """
    cfg = settings or get_settings()
    interval_ms(interval)  # 早失败：周期非法就别开始拉

    start_ms = parse_backfill_start(cfg.market_backfill_start)
    end_ms = last_closed_bar_open(int(datetime.now(UTC).timestamp() * 1000), interval)

    repo = KlineRepository()
    last = await repo.last_bar_time(symbol, interval)
    if last is not None:
        resume = last + interval_ms(interval)
        if resume > start_ms:
            logger.info("resuming backfill for %s from stored max(time)", symbol)
            start_ms = resume

    if start_ms > end_ms:
        stats = BackfillStats(finished_at=datetime.now(UTC).isoformat())
        return stats

    return await backfill_range(
        symbol,
        interval,
        start_ms,
        end_ms,
        settings=cfg,
        concurrency=concurrency,
        on_progress=on_progress,
    )


async def backfill_gaps(
    symbol: str,
    interval: str,
    *,
    lookback_ms: int,
    settings: Settings | None = None,
    concurrency: int | None = None,
) -> BackfillStats:
    """检测并补齐最近一段窗口内的缺口（日常巡检用）。

    只扫最近窗口而非全历史：全量空白由 backfill_history 负责，日常巡检
    只需覆盖「可能漏采」的范围，避免每次读取数百万行已存时间。
    """
    cfg = settings or get_settings()
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    end_ms = last_closed_bar_open(now_ms, interval)
    start_ms = end_ms - lookback_ms

    repo = KlineRepository()
    stored = await repo.times_in_range(symbol, interval, start_ms, end_ms)
    gaps = find_gaps(stored, start_ms=start_ms, end_ms=end_ms, interval=interval)

    stats = BackfillStats()
    if not gaps:
        stats.finished_at = datetime.now(UTC).isoformat()
        return stats

    missing = sum(count_bars(a, b, interval) for a, b in gaps)
    logger.info("found %s gap(s) covering %s bars for %s", len(gaps), missing, symbol)

    ranges: list[tuple[int, int]] = []
    for gap_start, gap_end in gaps:
        ranges.extend(chunk_ranges(gap_start, gap_end, interval, bars_per_chunk=_CHUNK_BARS))

    async with _build_client(cfg) as client:
        return await _run(
            client,
            symbol,
            interval,
            ranges,
            concurrency=concurrency or cfg.market_backfill_concurrency,
            stats=stats,
        )
