"""行情库读写。

写入一律幂等（主键 (time, symbol, interval) + ON CONFLICT DO NOTHING）——
这是回补可以随意重放、断点续传的前提（设计文档 §2.3）。
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from agent.market.models import IndicatorSnapshot, Kline
from agent.market.storage import get_market_session_factory

_WRITE_BATCH = 2000


def _group_by_series(
    rows: Sequence[dict[str, Any]],
) -> Iterator[tuple[str, str, list[dict[str, Any]]]]:
    """按 (symbol, interval) 分组 —— 一次插入可能跨多标的。"""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row["symbol"], row["interval"]), []).append(row)
    for (symbol, interval), group in groups.items():
        yield symbol, interval, group


class KlineRepository:
    async def insert_rows(self, rows: Sequence[dict[str, Any]]) -> int:
        """幂等批量写入，返回**实际新增**行数（已存在的行不计）。

        计数不靠 rowcount —— 实测在 executemany + ON CONFLICT DO NOTHING 下
        Python 的 sqlite3 驱动始终返回 0。改为先查该范围内已存时间戳，
        只写缺失的行：一次索引区间查询换精确计数，且重放退化为纯读不写。

        ON CONFLICT DO NOTHING 仍然保留，作为并发写入时的兜底。
        """
        if not rows:
            return 0

        factory = get_market_session_factory()
        inserted = 0
        async with factory() as db:
            for start in range(0, len(rows), _WRITE_BATCH):
                batch = rows[start : start + _WRITE_BATCH]
                inserted += await self._insert_batch(db, batch)
            await db.commit()
        return inserted

    async def _insert_batch(self, db: AsyncSession, batch: Sequence[dict[str, Any]]) -> int:
        fresh: list[dict[str, Any]] = []
        for symbol, interval, group in _group_by_series(batch):
            times = [r["time"] for r in group]
            result = await db.execute(
                select(Kline.time).where(
                    Kline.symbol == symbol,
                    Kline.interval == interval,
                    Kline.time >= min(times),
                    Kline.time <= max(times),
                )
            )
            existing = set(result.scalars().all())
            fresh.extend(r for r in group if r["time"] not in existing)

        if not fresh:
            return 0
        await db.execute(sqlite_insert(Kline).on_conflict_do_nothing(), fresh)
        return len(fresh)

    async def times_in_range(
        self, symbol: str, interval: str, start_ms: int, end_ms: int
    ) -> list[int]:
        """区间内已存 bar 的开始时间（升序）。用于缺口检测。"""
        factory = get_market_session_factory()
        async with factory() as db:
            result = await db.execute(
                select(Kline.time)
                .where(
                    Kline.symbol == symbol,
                    Kline.interval == interval,
                    Kline.time >= start_ms,
                    Kline.time <= end_ms,
                )
                .order_by(Kline.time.asc())
            )
            return [int(t) for t in result.scalars().all()]

    async def last_bar_time(self, symbol: str, interval: str) -> int | None:
        """已落库的最新 bar 开始时间；空库返回 None。"""
        factory = get_market_session_factory()
        async with factory() as db:
            result = await db.execute(
                select(Kline.time)
                .where(Kline.symbol == symbol, Kline.interval == interval)
                .order_by(Kline.time.desc())
                .limit(1)
            )
            return result.scalar_one_or_none()

    async def first_bar_time(self, symbol: str, interval: str) -> int | None:
        factory = get_market_session_factory()
        async with factory() as db:
            result = await db.execute(
                select(Kline.time)
                .where(Kline.symbol == symbol, Kline.interval == interval)
                .order_by(Kline.time.asc())
                .limit(1)
            )
            return result.scalar_one_or_none()

    async def count_since(self, symbol: str, interval: str, since_ms: int) -> int:
        factory = get_market_session_factory()
        async with factory() as db:
            result = await db.execute(
                select(func.count(Kline.time)).where(
                    Kline.symbol == symbol,
                    Kline.interval == interval,
                    Kline.time >= since_ms,
                )
            )
            return int(result.scalar_one() or 0)

    async def count_all(self, symbol: str, interval: str) -> int:
        factory = get_market_session_factory()
        async with factory() as db:
            result = await db.execute(
                select(func.count(Kline.time)).where(
                    Kline.symbol == symbol, Kline.interval == interval
                )
            )
            return int(result.scalar_one() or 0)

    async def recent_closes(
        self, symbol: str, interval: str, *, limit: int
    ) -> list[dict[str, Any]]:
        """最近 limit 根**已完成** bar，按时间升序返回。

        供供数层加工指标使用（阶段二）。最后一根由调用方按需丢弃。
        """
        factory = get_market_session_factory()
        async with factory() as db:
            result = await db.execute(
                select(Kline)
                .where(Kline.symbol == symbol, Kline.interval == interval)
                .order_by(Kline.time.desc())
                .limit(limit)
            )
            rows = list(result.scalars().all())

        return [
            {
                "t": r.time,
                "o": r.open,
                "h": r.high,
                "l": r.low,
                "c": r.close,
                "v": r.volume,
                "q": r.quote_volume,
                "n": r.trades,
                "taker_buy_base": r.taker_buy_base,
            }
            for r in reversed(rows)
        ]


class SnapshotRepository:
    async def save(
        self,
        *,
        snapshot_id: str,
        symbol: str,
        as_of: int,
        window: str,
        bar_count: int,
        metrics: str,
        context_digest: str,
        created_at: str,
    ) -> None:
        factory = get_market_session_factory()
        async with factory() as db:
            db.add(
                IndicatorSnapshot(
                    id=snapshot_id,
                    symbol=symbol,
                    as_of=as_of,
                    window=window,
                    bar_count=bar_count,
                    metrics=metrics,
                    context_digest=context_digest,
                    created_at=created_at,
                )
            )
            await db.commit()
