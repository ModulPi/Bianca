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

**跨进程探活**：`probe_lock_path()` 让旁观者在不加锁的前提下问"现在有没有采集器"。
这是"卡住但不死"这一类故障唯一能自动分辨的信号 —— 配合数据新鲜度，
lag 在涨而锁被持有 = 进程活着但停滞（要人介入），lag 在涨而锁空闲 = 进程没了
（计划任务会把它拉回来）。详见该函数的注释。

一台机器上一个采集器只服务一个库；跨机器共享同一个 SQLite 文件本来就不成立
（SQLite 不支持网络文件系统），所以本锁的粒度是"库文件"而非"机器"。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

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


# 锁的跨进程状态。unknown 不是"没查到"，是"这台机器上问不出答案"（见 probe_lock_path）
LockState = Literal["held", "free", "unknown"]


def _probe_fd(fd: int) -> LockState:
    """在已打开的 fd 上问"第 0 字节被锁了吗"，只读，不涉及加锁。"""
    try:
        os.lseek(fd, _LOCK_AT, os.SEEK_SET)
        os.read(fd, 1)
    except PermissionError:
        # 读第 0 字节被拒。但**不能就此断定是锁** —— ACL、杀软、只读介质都会
        # 给出同样的 PermissionError（CPython 经 CRT 拿到的只有 errno 13，
        # winerror 是 None，区分不了）。第 1 字节是线索区，持锁时读得到，
        # 用它把"被锁"和"整个文件读不了"分开。
        try:
            os.lseek(fd, _HINT_AT, os.SEEK_SET)
            os.read(fd, 1)
        except OSError:
            return "unknown"
        return "held"
    except OSError:
        return "unknown"
    # 读成功即未被锁。空文件也走这里：os.read 返回 b"" 而不报错 ——
    # 那是 os.open 与 _lock_fd 之间的一个极短窗口，算 free（真报 held 反而更坏）。
    return "free"


def probe_lock_path(path: Path | None) -> LockState:
    """旁观者视角：现在有没有别的采集器持着这个库的锁。

    与 `_read_holder()` 是**两件事**，别混用：`_read_holder` 从第 1 字节读线索，
    持锁时永远成功，拿它当探针只会 100% 报"空闲"。本函数读的是第 0 字节。

    只回答"有没有"，不回答"是谁"；而且只有 Windows 问得出答案 ——
    POSIX 的 flock 是劝告锁，别人持锁时读照样成功，探不出来就是探不出来，
    返回 unknown 而不是猜一个 free。本进程自己持锁时也别问：应当直接看
    `CollectorLock.held`，同进程第二个句柄的读行为没有验证过，不必赌。
    """
    if path is None:
        # 内存库没有可保护的共享状态，也就没有锁可探
        return "unknown"
    if os.name != "nt":
        return "unknown"

    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except FileNotFoundError:
        # 从没有采集器启动过，所以也没人建过锁文件
        return "free"
    except OSError:
        return "unknown"
    try:
        return _probe_fd(fd)
    finally:
        # 漏一个 fd 就少一个句柄。注意异常是在 read 上抛的、不在 open 上，
        # 所以 close 必须在 finally 里而不是跟在前一行后面。
        os.close(fd)


def probe_lock_state(database_url: str | None = None) -> LockState:
    """`probe_lock_path` 的便捷入口：自己从库 URL 推锁文件路径。"""
    try:
        path = collector_lock_path(database_url)
    except Exception:  # noqa: BLE001 — 探活失败不该带崩调用方
        logger.debug("could not derive lock path", exc_info=True)
        return "unknown"
    return probe_lock_path(path)


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
        except OSError:
            # 已经关过了（或 fd 被别处抢走）。释放的语义是"之后不该再持有"，
            # 这句失败不影响它。
            logger.debug("closing lock fd failed", exc_info=True)
        finally:
            self._fd = None
        logger.debug("released collector lock %s", path)

    def __del__(self) -> None:
        """对象被回收时关掉句柄。

        没有这一条时，`CollectorLock(path).acquire()` 这种不接住返回值的一次性写法
        会留下一个**幽灵持有者**：句柄不关、锁不放，而对象已经不可达，谁都释放不了。
        表现出来就是"有个采集器在跑"，而其实没有 —— 真正的采集器会被它挡在门外。
        与这个模块其它地方同一个主题：静默的假状态比报错难查得多。
        """
        self.release()

    def __enter__(self) -> CollectorLock:
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()
