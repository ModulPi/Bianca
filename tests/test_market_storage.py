"""行情库读写测试：幂等、缺口检测取数、断点续传基点。全程不出网。

用临时库文件而非 :memory: —— storage 的 engine/session 是模块级单例，
且 WAL 只在文件库上生效，内存库测不出真实的并发语义。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent.config import Settings
from agent.market import collector, storage
from agent.market.bars import interval_ms
from agent.market.collector import (
    CollectorSnapshot,
    build_ws_url,
    market_health,
    market_status_detail,
)
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
#
# 判定只看两条跨进程读得到的信道：库里的数据新不新鲜，以及内核锁在不在别人手里。
# 这正是它以前做不到的事 —— 那时它问的是"本进程有没有采集器"，而采集器独立成
# 进程（ADR-017）之后那个问题的答案恒为"没有"，于是告警被静默掉了。


def _settings(tmp_path, **over) -> Settings:
    return Settings(
        market_database_url=f"sqlite+aiosqlite:///{(tmp_path / 'market.db').as_posix()}",
        **over,
    )


@pytest.fixture
def lock_free(monkeypatch):
    monkeypatch.setattr(collector, "probe_lock_state", lambda *a, **k: "free")


@pytest.fixture
def lock_held(monkeypatch):
    monkeypatch.setattr(collector, "probe_lock_state", lambda *a, **k: "held")


def _now_min_bar(offset_min: int = 0) -> dict:
    """以当前最近的整分钟为基准造一根 bar（探活看的是与「现在」的距离）。"""
    from datetime import UTC, datetime

    now_min = int(datetime.now(UTC).timestamp() * 1000) // MIN * MIN
    return _row_at(now_min + offset_min * MIN)


async def test_market_health_ok_with_recent_bar(repo, tmp_path, lock_free):
    await repo.insert_rows([_now_min_bar()])
    status, detail = await market_health(_settings(tmp_path))
    assert status == "ok", detail
    assert detail is not None and detail.startswith("lag ")


async def test_market_health_error_when_lag_exceeds_threshold(repo, tmp_path, lock_free):
    """采集器静默死亡（风险 #1）必须能从 /health 看出来。"""
    await repo.insert_rows([_now_min_bar(offset_min=-10)])  # 落后 10 分钟
    status, detail = await market_health(_settings(tmp_path))
    assert status == "error"
    assert detail is not None and detail.startswith("stale:")


async def test_market_health_ignores_the_autostart_switch(repo, tmp_path, lock_free):
    """**回归测试**：这个开关以前会让 /health 永远报 disabled，从而静默掉风险 #1。

    采集器现在住在独立进程里，`MARKET_COLLECTOR_AUTOSTART=false` 是正常部署状态，
    而数据在流 —— 它必须报 ok。
    """
    await repo.insert_rows([_now_min_bar()])
    status, _ = await market_health(
        _settings(tmp_path, market_collector_autostart=False)
    )
    assert status == "ok"


async def test_market_health_warming_up_when_db_empty_and_someone_holds_the_lock(
    repo, tmp_path, lock_held
):
    """冷启动历史回补尚未写入第一根时不应报 degraded。"""
    status, detail = await market_health(_settings(tmp_path))
    assert status == "warming_up"
    assert detail is not None


async def test_market_health_error_when_db_empty_and_nobody_is_collecting(
    repo, tmp_path, lock_free
):
    """既没有数据、也没有采集器 —— 这是故障，不是冷启动。

    锁探针给出的正面证据（锁空闲）就是这条判据的界限。
    """
    status, detail = await market_health(_settings(tmp_path))
    assert status == "error"
    assert detail is not None and "没有采集器" in detail


async def test_market_health_unknown_lock_state_is_not_a_failure(
    repo, tmp_path, monkeypatch
):
    """探不出锁状态时（如 POSIX 的劝告锁）按冷启动对待 —— 报故障应当要求证据。"""
    monkeypatch.setattr(collector, "probe_lock_state", lambda *a, **k: "unknown")
    status, _ = await market_health(_settings(tmp_path))
    assert status == "warming_up"


async def test_market_health_error_when_no_symbols_configured(repo, tmp_path, lock_held):
    """空标的列表会让库永远是空的，从而一直判"冷启动" —— 必须显式判死。"""
    status, detail = await market_health(_settings(tmp_path, market_symbols=""))
    assert status == "error"
    assert detail is not None and "MARKET_SYMBOLS" in detail


async def test_stale_threshold_scales_with_the_interval(repo, tmp_path, lock_free):
    """阈值必须随周期缩放，否则长周期下这个判据会天天喊狼来了。

    2 小时前的数据在 1h 周期下是正常的（阈值 3 个周期 = 3h），但按写死的 180s
    必然被误判为故障 —— 而那个常数正是这次改掉的东西。
    """
    hour_ago = _now_min_bar(offset_min=-120)
    hour_ago["interval"] = "1h"
    await repo.insert_rows([hour_ago])

    status, detail = await market_health(_settings(tmp_path, market_interval="1h"))
    assert status == "ok", detail

    # 同样"落后 2 小时"的数据，按 1m 周期看就是故障（阈值 180s）
    await repo.insert_rows([_now_min_bar(offset_min=-120)])
    stale, _ = await market_health(_settings(tmp_path))
    assert stale == "error"


# ------------------------------------------------- /market/status 的进程内/跨进程分区
#
# 顶层只放跨进程可信的事实，进程内快照一律收进 session。这套测试守的就是那条界：
# 采集器独立成进程（ADR-017）后，进程内字段在 API 进程里必然是 false/null，和
# 跨进程字段平铺在一起就会被读成"采集器没在跑"（实测：数据在流、lag 9s，而
# collector_running=false）。

#: 顶层允许出现的 key。**加新字段必须想清楚它属于哪一侧** ——
#: 如果它读的是本进程的 snapshot，它就该进 session。
_CROSS_PROCESS_KEYS = {
    "database",
    "lock_file",
    "symbols",
    "interval",
    "interval_error",
    "last_bar_open_time",
    "last_closed_bar_open_time",
    "lag_seconds",
    "bars_count_24h",
    "bars_expected_24h",
    "gap_count_24h",
    "collector_owner",
    "data_flowing",
    "session",
}


class _FakeCollector:
    """只实现 market_status_detail 用到的 get_snapshot()（以及可选的 lock）。"""

    def __init__(self, snap: CollectorSnapshot | None = None, lock=None) -> None:
        self._snap = snap or CollectorSnapshot()
        if lock is not None:
            self.lock = lock

    async def get_snapshot(self) -> CollectorSnapshot:
        return self._snap


class _FakeLock:
    def __init__(self, held: bool) -> None:
        self.held = held


async def test_status_top_level_carries_no_in_process_fields(repo, tmp_path, lock_free):
    """按 key 集合钉住分区 —— 防止谎报换一个新名字回到顶层。"""
    detail = await market_status_detail(_FakeCollector(), _settings(tmp_path))
    assert set(detail) == _CROSS_PROCESS_KEYS


async def test_status_session_is_null_when_this_process_is_not_the_collector(
    repo, tmp_path, lock_free
):
    """API 进程没托管采集器时，`session` 是 null —— 说的是"我没有这个视角"。

    这个 null 与"采集器没在跑"是两回事：顶层 `data_flowing` / `collector_owner`
    才是回答后者的地方。
    """
    await repo.insert_rows([_now_min_bar()])
    detail = await market_status_detail(_FakeCollector(), _settings(tmp_path))
    assert detail["session"] is None
    assert detail["data_flowing"] is True
    assert detail["collector_owner"] == "none"


async def test_status_session_present_once_the_collector_has_run(repo, tmp_path, lock_free):
    """跑过就该有自述 —— 哪怕此刻已停：停机之后"这一程写了几根"正是最该看的时候。"""
    snap = CollectorSnapshot(started_at="2026-09-24T00:00:00+00:00", connected=True)
    detail = await market_status_detail(_FakeCollector(snap), _settings(tmp_path))
    session = detail["session"]
    assert session is not None
    assert session["connected"] is True


async def test_status_reports_self_as_owner_when_this_process_holds_the_lock(
    repo, tmp_path, lock_free  # noqa: ARG001 — 刻意让探针报 free，看 self 是否优先
):
    """本进程持锁时**不走探针**：同进程第二个句柄的读行为没验证过，不必赌。"""
    await repo.insert_rows([_now_min_bar()])
    detail = await market_status_detail(
        _FakeCollector(lock=_FakeLock(True)), _settings(tmp_path)
    )
    assert detail["collector_owner"] == "self"
    assert detail["data_flowing"] is True


async def test_status_reports_other_when_another_process_holds_the_lock(
    repo, tmp_path, lock_held
):
    detail = await market_status_detail(_FakeCollector(), _settings(tmp_path))
    assert detail["collector_owner"] == "other"


async def test_status_reports_unknown_where_the_lock_cannot_be_probed(
    repo, tmp_path, monkeypatch
):
    monkeypatch.setattr(collector, "probe_lock_state", lambda *a, **k: "unknown")
    detail = await market_status_detail(_FakeCollector(), _settings(tmp_path))
    assert detail["collector_owner"] == "unknown"


async def test_status_exposes_the_absolute_lock_path(repo, tmp_path, lock_free):
    """锁路径由进程 CWD 解析 —— 摆出绝对路径，CWD 错配才看得出来。"""
    detail = await market_status_detail(_FakeCollector(), _settings(tmp_path))
    assert detail["lock_file"] is not None
    assert detail["lock_file"].endswith("market.db.collector.lock")


async def test_status_gap_count_is_zero_for_a_contiguous_window(repo, tmp_path, lock_free):
    """铺满窗口时缺口必须是 0 —— 期望值多算一根都会在这里露馅。

    同时这条钉住 24h 窗口是**闭区间**：`[现在-24h, 最后一根]` 在整分钟边界上
    含 1441 个 1m 点。铺 1450 根覆盖得住，不至于因为跑测试的毫秒差而抖动。
    """
    now_ms = int(datetime.now(UTC).timestamp() * 1000) // MIN * MIN
    await repo.insert_rows([_row_at(now_ms - i * MIN) for i in range(1450)])
    detail = await market_status_detail(_FakeCollector(), _settings(tmp_path))
    assert detail["bars_count_24h"] >= 1440
    assert detail["gap_count_24h"] == 0


async def test_status_gap_count_means_missing_not_filled(repo, tmp_path, lock_free):
    """**回归测试**：`gap_count_24h` 以前取的是 `snap.gaps_filled`。

    它既不是 24h 口径也不是跨进程口径，但文档把它列在"DB 派生字段"里，而阶段一
    验收「连续运行 24h，gap_count_24h 补齐至 0」正是读它 —— 于是那条验收可以空洞
    通过（API 进程里它恒为 0）。现在它是"应有而未落库"，由库算出，方向与
    `session.gaps_filled`（本进程补了多少）相反。
    """
    await repo.insert_rows([_now_min_bar(), _now_min_bar(-1), _now_min_bar(-3)])  # 缺 -2
    snap = CollectorSnapshot(
        started_at="2026-09-24T00:00:00+00:00", gaps_filled=999
    )
    detail = await market_status_detail(_FakeCollector(snap), _settings(tmp_path))

    assert detail["bars_count_24h"] == 3
    assert detail["bars_expected_24h"] > detail["bars_count_24h"]
    assert detail["gap_count_24h"] == detail["bars_expected_24h"] - 3
    assert detail["gap_count_24h"] > 0
    # 旧的（错误的）来源不再是这个字段的取值
    assert detail["session"]["gaps_filled"] == 999


async def test_status_never_500s_on_an_unsupported_interval(repo, tmp_path, lock_free):
    """周期配错时给一个说得清的状态，而不是让状态端点自己失联。"""
    detail = await market_status_detail(
        _FakeCollector(), _settings(tmp_path, market_interval="7m")
    )
    assert detail["interval_error"] is not None
    assert detail["lag_seconds"] is None
    assert detail["data_flowing"] is False
    assert detail["last_closed_bar_open_time"] is None
