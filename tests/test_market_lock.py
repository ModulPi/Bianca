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
from agent.market.lock import (
    CollectorLock,
    CollectorLockError,
    _read_holder,
    collector_lock_path,
    probe_lock_path,
)

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


def _hold_in_subprocess(path):
    """让一个**真子进程**持锁，返回进程对象（已确认持上）。

    用自己的进程测不出"旁观者"的视角：Windows 的字节范围锁对同进程的其他句柄
    行为没有验证过，所以凡是要模拟"另一个进程在跑"的地方都用这个。
    """
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
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "locked"
    return proc


def _kill(proc) -> None:
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=30)


def test_lock_released_when_holding_process_dies(tmp_path):
    """内核锁的意义：进程死了锁自动没了，不存在"陈旧锁"要人工清理。

    用一个真的子进程去抢锁然后被杀掉，验证父进程随后能拿到。
    """
    path = tmp_path / "market.db.collector.lock"
    proc = _hold_in_subprocess(path)
    try:
        blocked = CollectorLock(path)
        with pytest.raises(CollectorLockError):
            blocked.acquire()

        _kill(proc)

        # 子进程已死 → 锁由内核释放，父进程可直接拿到，无需删文件
        after = CollectorLock(path)
        after.acquire()
        try:
            assert after.held
        finally:
            after.release()
    finally:
        _kill(proc)


# ------------------------------------------------------------------ 跨进程探活
#
# probe_lock_path 是给旁观者用的只读探针：它回答"现在有没有采集器"，但不加锁。
# 这是"卡住但不死"这类故障唯一能自动分辨的信号 —— 配合数据新鲜度，
# lag 涨 + 锁被持有 = 进程活着但停滞（要人介入），lag 涨 + 锁空闲 = 进程没了
# （计划任务会拉回来）。


def test_probe_reports_free_when_no_lock_file_exists(tmp_path):
    assert probe_lock_path(tmp_path / "never-created.lock") == "free"


def test_probe_reports_free_for_an_empty_lock_file(tmp_path):
    """空文件是 `os.open` 与 `_lock_fd` 之间的那个窗口，且读它不报错。

    这种时候报 free 而不是 held —— 把"读到了但没内容"当成"被锁着"会凭空造出
    一个不存在于文件系统的持有者。
    """
    path = tmp_path / "market.db.collector.lock"
    path.write_bytes(b"")
    assert probe_lock_path(path) == "free"


def test_probe_reports_unknown_for_a_path_that_cannot_be_read(tmp_path):
    """目录读不了 —— 但**不能**因此报 held。ACL、杀软、只读介质给的也是同一种
    PermissionError，所以探针靠"第 1 字节读得到"来确认是锁，否则只能说不知道。"""
    d = tmp_path / "adir"
    d.mkdir()
    assert probe_lock_path(d) == "unknown"


def test_probe_reports_unknown_when_there_is_no_lock_at_all():
    """内存库没有可保护的共享状态，也就没有锁可探。"""
    assert probe_lock_path(None) == "unknown"


@pytest.mark.skipif(os.name != "nt", reason="Windows 强制锁的专有行为")
def test_probe_reports_held_while_another_process_holds_it(tmp_path):
    path = tmp_path / "market.db.collector.lock"
    proc = _hold_in_subprocess(path)
    try:
        assert probe_lock_path(path) == "held"
    finally:
        _kill(proc)
    assert probe_lock_path(path) == "free"


def test_probe_reads_the_same_held_file_that_defeats_read_holder(tmp_path):
    """**别把 `_read_holder()` 当探针用**：它从第 1 字节读线索，持锁时永远成功，
    拿它判活会 100% 报"空闲"。这条断言把两者的分工钉死：同一个持锁文件，
    线索读得到，而探针说的是 held。"""
    path = tmp_path / "market.db.collector.lock"
    proc = _hold_in_subprocess(path)
    try:
        assert _read_holder(path).startswith("pid=")  # 线索读得到（这正是它的用途）
        assert probe_lock_path(path) == "held"  # 而锁的状态是另一回事
    finally:
        _kill(proc)


def test_probe_does_not_acquire_the_lock(tmp_path):
    """探针必须是只读的 —— 它每次 /health 都跑。若它顺手加了锁，就会有把采集器
    关在门外的窗口（采集器启动时拿不到锁 → 报"已有采集器在运行"）。"""
    path = tmp_path / "market.db.collector.lock"
    holder = CollectorLock(path)
    holder.acquire()
    holder.release()
    path.write_bytes(b"")  # release 只关句柄，文件留着

    assert probe_lock_path(path) == "free"
    assert probe_lock_path(path) == "free"  # 探两次也不该把自己锁上

    after_probe = CollectorLock(path)
    after_probe.acquire()  # 探针跑完照样能拿到
    try:
        assert after_probe.held
    finally:
        after_probe.release()


def test_probe_sees_a_lock_held_by_this_same_process(tmp_path):
    """同进程持有的锁，旁观句柄也读不到第 0 字节 —— 探针因此报 held。

    这本身是个"别自己探自己"的理由：`market_status_detail` 判断"是不是我在采"
    用的是 `CollectorLock.held`，不走探针。
    """
    path = tmp_path / "market.db.collector.lock"
    lock = CollectorLock(path)
    lock.acquire()
    try:
        assert probe_lock_path(path) == "held"
    finally:
        lock.release()
    assert probe_lock_path(path) == "free"


def test_dropping_a_lock_object_closes_it(tmp_path):
    """丢掉锁对象必须把句柄也放掉 —— 否则会留下一个"幽灵持有者"。

    实测过的坑：`CollectorLock(path).acquire()` 这种一次性写法（没有变量接住）
    在只有内核锁、没有 `__del__` 的版本里，句柄既不会关、锁也不会释放，
    循环引用回收之前谁都拿不到锁 —— 看起来就是"有个采集器在跑"，而其实没有。
    """
    import gc

    path = tmp_path / "market.db.collector.lock"
    CollectorLock(path).acquire()  # 刻意不接住
    gc.collect()
    assert probe_lock_path(path) == "free"
    fresh = CollectorLock(path)
    fresh.acquire()  # 拿得到，说明幽灵已经放掉了
    fresh.release()


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
