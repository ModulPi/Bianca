"""采集器单实例锁：同一个行情库同时只允许一个采集器写。

**为什么需要**：数据面独立成进程后（ADR-017），"API 内的采集器"和"独立运行的
采集器"可能同时存在。两个采集器会开两条 WS 订阅、跑两遍回补、双倍消耗请求配额。
因为落库幂等（ON CONFLICT DO NOTHING），数据不会错，但纯属浪费，而且会**掩盖
部署错误** —— 看起来一切正常，实际多了一个没人知道的采集器。

**为什么不用 PID 文件判活**：Windows 上 `os.kill(pid, 0)` 不是探活，它**会真的
终止目标进程**。改用操作系统管理的文件锁（fcntl / msvcrt）：进程无论怎么死
（正常退出、被 kill、崩溃），锁都由内核自动释放。因此不存在"陈旧锁"这个概念，
也就不需要"锁过期时间"这类容易出错的补救机制。

**锁文件格局：第 0 字节是锁，第 1 字节起是排障线索。** 这不是为了整齐，是被
Windows 强制锁逼出来的：Windows 的字节范围锁对**任何**其他句柄生效，包括本进程
新开的句柄 —— 所以把 pid 写在第 0 字节，谁都读不到（连自己都读不到，实测
`PermissionError`）。锁定位在稳定的第 0 字节，线索挪到它后面，旁观者才读得着。
副作用是删锁文件也删不掉（别的进程持着句柄，`unlink` 报 PermissionError），
这点写进了报错信息里 —— 别叫人去做一件做不到的事。

线索本身不是权威状态。权威状态是"谁持有这个内核锁"，文件内容只是给人看的，
读它不构成任何判断依据（进程可能在读到之前就退出，也可能还没把内容写进去）。

一台机器上一个采集器只服务一个库；跨机器共享同一个 SQLite 文件本来就不成立
（SQLite 不支持网络文件系统），所以本锁的粒度是"库文件"而非"机器"。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from agent.config import get_settings

logger = logging.getLogger(__name__)


class CollectorLockError(RuntimeError):
    """同一个行情库上已有采集器在运行。"""


def collector_lock_path(database_url: str | None = None) -> Path | None:
    """由库 URL 推导锁文件路径。内存库返回 None（无可保护的共享状态）。

    路径解析交给 SQLAlchemy 的 URL 解析器，不自己切字符串 —— `sqlite:///x.db`
    是相对路径而 `sqlite:////x.db` 是绝对路径，这个区别只有它认得准。
    """
    url = database_url or get_settings().market_database_url
    database = make_url(url).database
    if not database or database == ":memory:":
        return None
    db = Path(database)
    return db.with_name(db.name + ".collector.lock")


# 第 0 字节用来加锁，线索从第 1 字节开始写（见模块 docstring）
_LOCK_AT = 0
_HINT_AT = 1
# Windows 上 os.open 默认文本模式，会把 \n 变成 \r\n。锁文件是二进制用途，
# 别让运行时悄悄改字节。
_OPEN_FLAGS = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)


def _lock_fd(fd: int) -> None:
    """对 fd 的第 0 字节加非阻塞排他锁。已被占用则抛 OSError。"""
    os.lseek(fd, _LOCK_AT, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _read_holder(path: Path) -> str:
    """读锁文件里的持有者线索。纯排障用，失败一律当空。

    必须从 `_HINT_AT` 起读 —— 从 0 读会把被锁的那一字节也框进来，Windows 的
    强制锁会直接拒绝整个读操作。
    """
    try:
        with path.open("rb") as f:
            f.seek(_HINT_AT)
            return f.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def _write_holder(fd: int) -> None:
    """把持有者信息写进锁文件。拿不到锁的进程也会读它，所以只在持锁后写。"""
    payload = f"pid={os.getpid()} since={datetime.now(UTC).isoformat()}".encode()
    try:
        os.lseek(fd, _HINT_AT, os.SEEK_SET)
        os.ftruncate(fd, _HINT_AT)  # 冲掉上一次的尾巴，避免读到混合内容
        os.write(fd, payload)
    except OSError:
        # 诊断信息写不进去不影响锁的有效性，不该因此让采集器起不来
        logger.debug("could not write lock holder info", exc_info=True)


@dataclass
class CollectorLock:
    path: Path | None
    _fd: int | None = field(default=None, repr=False)

    @property
    def held(self) -> bool:
        return self._fd is not None

    @property
    def holder(self) -> str:
        """当前锁文件里记录的持有者（可能为空）。"""
        return _read_holder(self.path) if self.path is not None else ""

    def acquire(self) -> None:
        """取得锁。已被占用则抛 CollectorLockError。可重入（同实例重复调用无副作用）。"""
        if self._fd is not None or self.path is None:
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, _OPEN_FLAGS)
        try:
            _lock_fd(fd)
        except OSError as exc:
            holder = _read_holder(self.path)
            os.close(fd)
            detail = f"，对方是 {holder}" if holder else ""
            raise CollectorLockError(
                f"另一个采集器正在写这个行情库（锁文件 {self.path}{detail}）。"
                f"同一时间只允许一个采集器，现在启动会导致重复订阅与重复回补。"
                f"请先确认那个进程是谁：它可能正是另一个 API 实例（检查 "
                f"MARKET_COLLECTOR_AUTOSTART），也可能是上一个采集器残留的进程。"
                f"结束它的进程即可，锁由操作系统随进程退出释放 —— "
                f"注意删锁文件是没用的，Windows 下对方持着句柄，删也删不掉。"
            ) from exc

        self._fd = fd
        _write_holder(fd)
        logger.debug("acquired collector lock %s (%s)", self.path, self.holder)

    def release(self) -> None:
        """释放锁。关闭 fd 即可 —— 内核在 close / 进程退出时都会自动释放。"""
        if self._fd is None:
            return
        path = self.path
        try:
            os.close(self._fd)
        finally:
            self._fd = None
        logger.debug("released collector lock %s", path)

    def __enter__(self) -> CollectorLock:
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()
