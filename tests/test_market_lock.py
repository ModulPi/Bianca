"""采集器单实例锁测试（ADR-017 缺口 1 的配套护栏）。

数据面独立成进程后，"API 内的采集器"和"独立进程的采集器"可能同时存在。
这些测试锁住的是：同一个库上不允许出现第二个采集器。

全程不出网 —— 采集器的 `_loop` 被换成一个只等停机事件的空转函数。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from agent.config import Settings
from agent.market.collector import MarketCollector
from agent.market.lock import CollectorLock, CollectorLockError, collector_lock_path

# ------------------------------------------------------------------ 路径推导


def test_lock_path_sits_next_to_db():
    path = collector_lock_path("sqlite+aiosqlite:///./data/market.db")
    assert path == Path("data/market.db.collector.lock")


def test_lock_path_handles_absolute_db():
    """Windows 盘符路径在 URL 里是三斜杠（SQLAlchemy 的约定），不是四斜杠。"""
    path = collector_lock_path("sqlite+aiosqlite:///D:/data/market.db")
    assert path == Path("D:/data/market.db.collector.lock")


def test_memory_db_has_no_lock():
    """内存库没有可保护的共享状态，不该产生锁文件。"""
    assert collector_lock_path("sqlite+aiosqlite:///:memory:") is None


# ------------------------------------------------------------------ 互斥语义


def test_second_acquire_fails_and_names_the_lock_file(tmp_path):
    path = tmp_path / "market.db.collector.lock"
    first = CollectorLock(path)
    second = CollectorLock(path)
    first.acquire()
    try:
        assert first.held
        with pytest.raises(CollectorLockError) as exc:
            second.acquire()
        assert str(path) in str(exc.value)  # 报错要指出是哪个锁文件
        assert not second.held
    finally:
        first.release()


def test_release_makes_lock_available_again(tmp_path):
    path = tmp_path / "market.db.collector.lock"
    first = CollectorLock(path)
    first.acquire()
    first.release()
    assert not first.held
    assert path.exists()  # 锁文件本身留着，权威状态是内核锁不是文件存在与否

    second = CollectorLock(path)
    second.acquire()
    try:
        assert second.held
    finally:
        second.release()


def test_lock_is_reentrant_for_same_instance(tmp_path):
    """同实例重复 acquire 不该自己把自己挡住（start 重入是允许的）。"""
    lock = CollectorLock(tmp_path / "market.db.collector.lock")
    with lock:
        lock.acquire()
        assert lock.held


def test_holder_hint_is_written(tmp_path):
    """锁文件里写的持有者只是排障线索，不参与判断 —— 但要真的写进去。"""
    lock = CollectorLock(tmp_path / "market.db.collector.lock")
    with lock:
        assert f"pid={os.getpid()}" in lock.holder


def test_bystander_can_read_holder_hint(tmp_path):
    """线索必须能被旁观者读到 —— 这就是它写在**第 1 字节**而不是第 0 字节的原因。

    写在第 0 字节（和锁同一个字节）在 Windows 上谁都读不到，连持有者自己
    新开个句柄读都会吃 PermissionError（强制锁）。而"谁占着锁"恰恰是被挡住
    的那个人最需要知道的信息。
    """
    path = tmp_path / "market.db.collector.lock"
    holder = CollectorLock(path)
    holder.acquire()
    try:
        bystander = CollectorLock(path)
        assert f"pid={os.getpid()}" in bystander.holder

        with pytest.raises(CollectorLockError) as exc:
            bystander.acquire()
        assert f"pid={os.getpid()}" in str(exc.value)  # 报错里要带上，别只说"有人占着"
    finally:
        holder.release()


@pytest.mark.skipif(os.name != "nt", reason="Windows 强制锁的专有行为")
def test_lock_file_cannot_be_deleted_while_held(tmp_path):
    """持锁期间删不掉锁文件 —— 报错信息因此不叫人去删文件。

    这条断言的价值在于**钉住那句提示的前提**：如果哪天 Windows 行为变了、
    或者我们改用别的方式开源码句柄，这里会红，提醒回去改报错文案。
    """
    path = tmp_path / "market.db.collector.lock"
    lock = CollectorLock(path)
    lock.acquire()
    try:
        with pytest.raises(PermissionError):
            path.unlink()
    finally:
        lock.release()


def test_lock_is_skipped_when_path_is_none():
    lock = CollectorLock(None)
    lock.acquire()  # 不该抛
    assert not lock.held
    lock.release()


def test_lock_released_when_holding_process_dies(tmp_path):
    """内核锁的意义：进程死了锁自动没了，不存在"陈旧锁"要人工清理。

    用一个真的子进程去抢锁然后被杀掉，验证父进程随后能拿到。
    """
    path = tmp_path / "market.db.collector.lock"
    script = (
        "import sys, time;"
        f"sys.path.insert(0, {str(Path.cwd())!r});"
        "from agent.market.lock import CollectorLock;"
        f"lock = CollectorLock(__import__('pathlib').Path({str(path)!r}));"
        "lock.acquire();"
        "print('locked', flush=True);"
        "time.sleep(60)"
    )
    import subprocess

    proc = subprocess.Popen(
        [os.sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "locked"

        blocked = CollectorLock(path)
        with pytest.raises(CollectorLockError):
            blocked.acquire()

        proc.kill()
        proc.wait(timeout=30)

        # 子进程已死 → 锁由内核释放，父进程可直接拿到，无需删文件
        after = CollectorLock(path)
        after.acquire()
        try:
            assert after.held
        finally:
            after.release()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=30)


# ------------------------------------------------------------------ 采集器接线


async def _idle_loop(self) -> None:
    """替身 `_loop`：不出网，只等停机事件。"""
    await self._stop_event.wait()


def _settings(tmp_path) -> Settings:
    db = tmp_path / "market.db"
    return Settings(
        market_database_url=f"sqlite+aiosqlite:///{db.as_posix()}",
        market_backfill_on_start=False,
    )


async def test_collector_start_refuses_when_lock_is_held(tmp_path):
    """已有采集器在跑时，start() 必须拒绝而不是默默起第二条订阅。"""
    settings = _settings(tmp_path)
    holder = CollectorLock(collector_lock_path(settings.market_database_url))
    holder.acquire()
    try:
        collector = MarketCollector(settings=settings)
        with pytest.raises(CollectorLockError):
            await collector.start()
        assert not collector.running  # 没起来就别留 running=True 的假象
        assert collector._task is None
    finally:
        holder.release()


async def test_collector_start_stop_toggles_lock(tmp_path, monkeypatch):
    """start() 拿锁、stop() 还锁 —— 这样 API 停掉后独立进程才起得来。"""
    monkeypatch.setattr(MarketCollector, "_loop", _idle_loop)
    settings = _settings(tmp_path)
    path = collector_lock_path(settings.market_database_url)

    collector = MarketCollector(settings=settings)
    await collector.start()
    assert collector.running
    assert collector.lock.held

    outsider = CollectorLock(path)
    with pytest.raises(CollectorLockError):  # 运行期间别人拿不到
        outsider.acquire()

    await collector.stop()
    assert not collector.running
    assert not collector.lock.held

    outsider.acquire()  # 停完就交还了
    try:
        assert outsider.held
    finally:
        outsider.release()


async def test_collector_start_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(MarketCollector, "_loop", _idle_loop)
    collector = MarketCollector(settings=_settings(tmp_path))
    await collector.start()
    try:
        await collector.start()  # 重复 start 是 no-op，不该自己把自己锁住
        assert collector.running
    finally:
        await collector.stop()


async def test_stop_releases_lock_even_if_loop_task_is_stuck(tmp_path, monkeypatch):
    """停机路径不能被卡住的任务拖死 —— 锁必须还回去。"""

    async def never_ends(self) -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            raise

    monkeypatch.setattr(MarketCollector, "_loop", never_ends)
    settings = _settings(tmp_path)
    collector = MarketCollector(settings=settings)
    await collector.start()
    await asyncio.wait_for(collector.stop(), timeout=10)

    assert not collector.lock.held
    revived = CollectorLock(collector_lock_path(settings.market_database_url))
    revived.acquire()
    try:
        assert revived.held
    finally:
        revived.release()
