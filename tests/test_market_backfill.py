"""回补并发语义测试：分片并发下的统计口径必须与库内实际行数一致。

全程不出网 —— `_fetch_chunk` 被替换成按区间造数。用临时库文件而非 :memory:，
理由同 test_market_storage（storage 的 engine 是模块级单例，WAL 只在文件库生效）。
"""

from __future__ import annotations

import asyncio

import pytest

from agent.config import Settings
from agent.market import backfill, storage
from agent.market.backfill import BackfillStats, _run
from agent.market.repository import KlineRepository

MIN = 60_000
T0 = 1_792_000_000_000 - (1_792_000_000_000 % MIN)  # 对齐到 1m 边界
CHUNK = 50  # 每个分片造 50 根
N_CHUNKS = 5


def _rows_for(start_ms: int, end_ms: int) -> list[dict]:
    """按区间造 bar，价格随 index 变化以便校验取值正确。"""
    count = (end_ms - start_ms) // MIN + 1
    rows = []
    for i in range(count):
        price = 100.0 + i
        rows.append(
            {
                "time": start_ms + i * MIN,
                "symbol": "BTCUSDT",
                "interval": "1m",
                "open": price,
                "high": price + 1,
                "low": price - 1,
                "close": price + 0.5,
                "volume": 1.5,
                "quote_volume": 150.0,
                "trades": 10,
                "taker_buy_base": 0.7,
                "taker_buy_quote": 70.0,
            }
        )
    return rows


def _ranges() -> list[tuple[int, int]]:
    return [
        (T0 + i * CHUNK * MIN, T0 + ((i + 1) * CHUNK - 1) * MIN) for i in range(N_CHUNKS)
    ]


@pytest.fixture
async def market_db(tmp_path, monkeypatch):
    db_path = tmp_path / "market.db"
    monkeypatch.setattr(
        storage,
        "get_settings",
        lambda: Settings(market_database_url=f"sqlite+aiosqlite:///{db_path.as_posix()}"),
    )
    await storage.close_market_db()  # 清掉上一个测试留下的单例
    await storage.init_market_db()
    yield
    await storage.close_market_db()


@pytest.fixture
def offline_fetch(monkeypatch):
    """把 HTTP 拉取换成造数，统计口径与真实分片一致。"""
    calls: list[tuple[int, int]] = []

    async def fake_fetch(client, symbol, interval, start_ms, end_ms, stats):
        calls.append((start_ms, end_ms))
        rows = _rows_for(start_ms, end_ms)
        stats.requests += 1
        stats.rows_received += len(rows)
        return rows

    monkeypatch.setattr(backfill, "_fetch_chunk", fake_fetch)
    return calls


async def test_rows_inserted_matches_db_under_concurrency(market_db, offline_fetch, monkeypatch):
    """并发落库时 rows_inserted 必须等于库内真实行数。

    回归保护：`stats.rows_inserted += await repo.insert_rows(rows)` 会先读左值再
    挂起，5 个 worker 都读到同一个旧值、各自写回，互相覆盖 —— 全量跑时少报了
    61.8 万根。这里让 insert 先让出控制权，把那个交错窗口固定下来。
    """
    real_insert = KlineRepository.insert_rows

    async def slow_insert(self, rows):
        await asyncio.sleep(0.01)  # 真实 I/O 同样会挂起，这里只是把窗口显式化
        return await real_insert(self, rows)

    monkeypatch.setattr(KlineRepository, "insert_rows", slow_insert)

    stats = BackfillStats()
    await _run(None, "BTCUSDT", "1m", _ranges(), concurrency=N_CHUNKS, stats=stats)

    actual = await KlineRepository().count_all("BTCUSDT", "1m")
    assert stats.rows_received == N_CHUNKS * CHUNK
    assert actual == N_CHUNKS * CHUNK
    assert stats.rows_inserted == actual


async def test_rerun_of_same_ranges_inserts_nothing(market_db, offline_fetch):
    """同一组区间重放：第二遍 rows_inserted 必须为 0（幂等，验收项）。"""
    first = BackfillStats()
    await _run(None, "BTCUSDT", "1m", _ranges(), concurrency=N_CHUNKS, stats=first)
    assert first.rows_inserted == N_CHUNKS * CHUNK

    second = BackfillStats()
    await _run(None, "BTCUSDT", "1m", _ranges(), concurrency=N_CHUNKS, stats=second)
    assert second.rows_received == N_CHUNKS * CHUNK  # 拉到了
    assert second.rows_inserted == 0  # 但一行都没新增
    assert await KlineRepository().count_all("BTCUSDT", "1m") == N_CHUNKS * CHUNK


async def test_failed_chunk_is_counted_and_does_not_write(market_db, monkeypatch):
    """分片最终失败要计入 failed_chunks，且不污染库内数据。"""

    async def failing_fetch(client, symbol, interval, start_ms, end_ms, stats):
        stats.requests += 1
        stats.failed_chunks += 1
        return []  # _fetch_chunk 重试耗尽后的约定返回值

    monkeypatch.setattr(backfill, "_fetch_chunk", failing_fetch)

    stats = BackfillStats()
    await _run(None, "BTCUSDT", "1m", _ranges(), concurrency=N_CHUNKS, stats=stats)

    assert stats.failed_chunks == N_CHUNKS
    assert stats.rows_inserted == 0
    assert await KlineRepository().count_all("BTCUSDT", "1m") == 0


async def test_on_progress_fires_once_per_chunk(market_db, offline_fetch):
    """进度回调次数 == 分片数，且 done 单调走到 total。"""
    seen: list[tuple[int, int]] = []
    stats = BackfillStats()
    await _run(
        None,
        "BTCUSDT",
        "1m",
        _ranges(),
        concurrency=N_CHUNKS,
        stats=stats,
        on_progress=lambda done, total, st: seen.append((done, total)),
    )

    assert len(seen) == N_CHUNKS
    assert sorted(d for d, _ in seen) == list(range(1, N_CHUNKS + 1))
    assert {t for _, t in seen} == {N_CHUNKS}
