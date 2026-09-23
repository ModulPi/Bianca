"""行情库读写测试：幂等、缺口检测取数、断点续传基点。全程不出网。

用临时库文件而非 :memory: —— storage 的 engine/session 是模块级单例，
且 WAL 只在文件库上生效，内存库测不出真实的并发语义。
"""

from __future__ import annotations

import pytest

from agent.config import Settings
from agent.market import storage
from agent.market.bars import interval_ms
from agent.market.collector import build_ws_url, market_health
from agent.market.repository import KlineRepository, SnapshotRepository

MIN = 60_000
T0 = 1_792_000_000_000 - (1_792_000_000_000 % MIN)  # 对齐到 1m 边界


def _row_at(time_ms: int, *, symbol: str = "BTCUSDT", interval: str = "1m", index: int = 0) -> dict:
    price = 100.0 + index
    return {
        "time": time_ms,
        "symbol": symbol,
        "interval": interval,
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


def _row(index: int, *, symbol: str = "BTCUSDT", interval: str = "1m") -> dict:
    """第 index 根 bar，价格随 index 变化以便校验取值正确。"""
    return _row_at(
        T0 + index * interval_ms(interval), symbol=symbol, interval=interval, index=index
    )


@pytest.fixture
async def repo(tmp_path, monkeypatch):
    db_path = tmp_path / "market.db"
    monkeypatch.setattr(
        storage,
        "get_settings",
        lambda: Settings(market_database_url=f"sqlite+aiosqlite:///{db_path.as_posix()}"),
    )
    await storage.close_market_db()  # 清掉上一个测试留下的单例
    await storage.init_market_db()
    yield KlineRepository()
    await storage.close_market_db()


async def test_insert_rows_returns_exact_new_count(repo):
    assert await repo.insert_rows([_row(i) for i in range(10)]) == 10
    assert await repo.count_all("BTCUSDT", "1m") == 10


async def test_insert_rows_is_idempotent_on_replay(repo):
    rows = [_row(i) for i in range(10)]
    assert await repo.insert_rows(rows) == 10
    # 重放：一行都不该新增，且不覆盖已存值
    assert await repo.insert_rows(rows) == 0
    assert await repo.count_all("BTCUSDT", "1m") == 10


async def test_insert_rows_partial_overlap_counts_only_missing(repo):
    await repo.insert_rows([_row(i) for i in [0, 1, 2, 3, 4]])  # 已存 0..4
    # 再写 [3..8]：2 根重叠（3、4）+ 4 根新增（5..8）——只计新增
    assert await repo.insert_rows([_row(i) for i in [3, 4, 5, 6, 7, 8]]) == 4
    assert await repo.count_all("BTCUSDT", "1m") == 9


async def test_insert_rows_accepts_unsorted_input(repo):
    # 乱序 + 跨内部批次边界，仍须逐根判断而非整批跳过
    assert await repo.insert_rows([_row(4), _row(0), _row(2)]) == 3
    assert await repo.insert_rows([_row(2), _row(3), _row(4)]) == 1
    assert await repo.count_all("BTCUSDT", "1m") == 4


async def test_insert_rows_empty_and_multiple_series(repo):
    assert await repo.insert_rows([]) == 0
    mixed = [_row(0), _row(0, symbol="ETHUSDT"), _row(0, interval="5m")]
    assert await repo.insert_rows(mixed) == 3
    assert await repo.count_all("BTCUSDT", "1m") == 1
    assert await repo.count_all("ETHUSDT", "1m") == 1
    assert await repo.count_all("BTCUSDT", "5m") == 1
    # 各序列独立幂等：重放全部命中
    assert await repo.insert_rows(mixed) == 0


async def test_insert_rows_large_batch_spans_write_chunks(repo):
    """超过内部 _WRITE_BATCH 的批次要整体正确落库，不能只写最后一个分片。"""
    n = 5000
    assert await repo.insert_rows([_row(i) for i in range(n)]) == n
    assert await repo.count_all("BTCUSDT", "1m") == n
    assert await repo.insert_rows([_row(i) for i in range(n)]) == 0


async def test_bar_bounds_and_count_since(repo):
    await repo.insert_rows([_row(i) for i in range(20)])
    assert await repo.first_bar_time("BTCUSDT", "1m") == T0
    assert await repo.last_bar_time("BTCUSDT", "1m") == T0 + 19 * MIN
    assert await repo.count_since("BTCUSDT", "1m", T0 + 10 * MIN) == 10


async def test_bounds_are_none_on_empty_db(repo):
    assert await repo.first_bar_time("BTCUSDT", "1m") is None
    assert await repo.last_bar_time("BTCUSDT", "1m") is None
    assert await repo.count_all("BTCUSDT", "1m") == 0
    assert await repo.recent_closes("BTCUSDT", "1m", limit=5) == []


async def test_times_in_range_feeds_gap_detection(repo):
    """times_in_range 是 find_gaps 的输入：必须升序、且严格落在闭区间内。"""
    await repo.insert_rows([_row(i) for i in range(10)])
    times = await repo.times_in_range("BTCUSDT", "1m", T0 + 2 * MIN, T0 + 4 * MIN)
    assert times == [T0 + 2 * MIN, T0 + 3 * MIN, T0 + 4 * MIN]


async def test_recent_closes_returns_oldest_first(repo):
    await repo.insert_rows([_row(i) for i in range(30)])
    closes = await repo.recent_closes("BTCUSDT", "1m", limit=5)
    assert len(closes) == 5
    # 取的是最近 5 根（index 25..29），但按时间升序返回
    assert [c["t"] for c in closes] == [T0 + i * MIN for i in range(25, 30)]
    assert closes[-1]["c"] == 100.0 + 29 + 0.5


async def test_resume_point_is_last_bar_plus_one_interval(repo):
    """断点续传基点由库里 max(time) 直接得出，不维护进度表（ADR-014）。"""
    await repo.insert_rows([_row(i) for i in range(7)])
    last = await repo.last_bar_time("BTCUSDT", "1m")
    assert last + interval_ms("1m") == T0 + 7 * MIN


async def test_snapshot_repository_persists(repo):
    from agent.market.storage import get_market_session_factory
    from agent.market.models import IndicatorSnapshot
    from sqlalchemy import select

    await SnapshotRepository().save(
        snapshot_id="snap-1",
        symbol="BTCUSDT",
        as_of=T0,
        window="1m",
        bar_count=200,
        metrics='{"rsi14": 55.2}',
        context_digest="deadbeef",
        created_at="2026-09-23T00:00:00+00:00",
    )
    factory = get_market_session_factory()
    async with factory() as db:
        row = (await db.execute(select(IndicatorSnapshot))).scalar_one()
    assert row.id == "snap-1"
    assert row.bar_count == 200
    assert row.metrics == '{"rsi14": 55.2}'


def test_build_ws_url_single_and_combined():
    base = "wss://stream.binance.com:9443/ws"
    assert build_ws_url(base, ["BTCUSDT"], "1m") == f"{base}/btcusdt@kline_1m"
    # 多标点必须走合并流端点，且 base 的 /ws 后缀要去掉（ADR-016）
    assert (
        build_ws_url(base, ["BTCUSDT", "ETHUSDT"], "1m")
        == "wss://stream.binance.com:9443/stream?streams=btcusdt@kline_1m/ethusdt@kline_1m"
    )
    assert build_ws_url(base + "/", ["BTCUSDT"], "5m") == f"{base}/btcusdt@kline_5m"


# --------------------------------------------------------------- /health 探活


class _FakeCollector:
    """只实现 market_health 用到的 get_snapshot()。"""

    def __init__(self, running: bool) -> None:
        self._running = running

    async def get_snapshot(self):
        from agent.market.collector import CollectorSnapshot

        return CollectorSnapshot(running=self._running)


def _now_min_bar(offset_min: int = 0) -> dict:
    """以当前最近的整分钟为基准造一根 bar（探活看的是与「现在」的距离）。"""
    from datetime import UTC, datetime

    now_min = int(datetime.now(UTC).timestamp() * 1000) // MIN * MIN
    return _row_at(now_min + offset_min * MIN)


async def test_market_health_disabled_when_autostart_off(repo):
    status, detail = await market_health(
        _FakeCollector(True), Settings(market_collector_autostart=False)
    )
    assert status == "disabled"
    assert detail is None


async def test_market_health_error_when_collector_not_running(repo):
    status, detail = await market_health(_FakeCollector(False), Settings())
    assert status == "error"
    assert detail == "collector not running"


async def test_market_health_warming_up_when_db_empty(repo):
    """冷启动历史回补尚未写入第一根时不应报 degraded。"""
    status, detail = await market_health(_FakeCollector(True), Settings())
    assert status == "warming_up"
    assert detail is not None


async def test_market_health_ok_with_recent_bar(repo):
    await repo.insert_rows([_now_min_bar()])
    status, detail = await market_health(_FakeCollector(True), Settings())
    assert status == "ok", detail
    assert detail is not None and detail.startswith("lag ")


async def test_market_health_error_when_lag_exceeds_threshold(repo):
    """采集器静默死亡（风险 #1）必须能从 /health 看出来。"""
    await repo.insert_rows([_now_min_bar(offset_min=-10)])  # 落后 10 分钟
    status, detail = await market_health(_FakeCollector(True), Settings())
    assert status == "error"
    assert detail is not None and detail.startswith("stale:")
