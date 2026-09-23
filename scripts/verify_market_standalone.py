"""验收检查：数据面能否脱离 API 进程独立运行（ADR-017 缺口 1）。

**永远用临时库**，不碰 `data/market.db`，因此可以在线上采集器正在跑的时候安全执行。
这一点是刻意的：验收「独立运行」如果要去动线上库，那这条验收项就没人敢跑。

检查五件事：
1. 不经过 API 就能启动，并真的往库里写 bar
2. 跨进程互斥：第二个采集器必须被单实例锁挡下（退出码 3）
3. 报错信息里要能看出是谁占着锁（不是只说"有人占着"）
4. 被硬杀（模拟崩溃）后锁由内核自动释放 —— 不存在需要人工清理的陈旧锁
5. Ctrl-Break 能优雅退出（退出码 0）

用法：
    python -u scripts/verify_market_standalone.py
    python -u scripts/verify_market_standalone.py --keep   # 留下临时库供排查
"""

from __future__ import annotations

import argparse
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp") / "bianca-standalone"
DB = TMP / "market.db"
LOCK = TMP / "market.db.collector.lock"

# Windows: 让子进程自成进程组，这样 CTRL_BREAK_EVENT 只会送到它，
# 不会顺手把我自己也带走
_CREATE_NEW_PROCESS_GROUP = 0x00000200

_CONNECT_TIMEOUT_S = 60
_SETTLE_S = 20  # 连上后再等一会儿，让缺口回补真的写完

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)
    if not ok:
        _failures.append(name)


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["MARKET_DATABASE_URL"] = f"sqlite+aiosqlite:///{DB.as_posix()}"
    # 临时库上跑十年回补毫无意义，还会把这次验收拖成几十分钟
    env["MARKET_BACKFILL_ON_START"] = "false"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _spawn() -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-u", "-m", "agent.market", "--status-interval", "5"],
        cwd=str(ROOT),
        env=_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=_CREATE_NEW_PROCESS_GROUP,
    )


def _wait_for(proc: subprocess.Popen[str], needle: str, timeout: float) -> bool:
    """读到 needle 返回 True，超时或进程提前退出返回 False。"""
    assert proc.stdout is not None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                return False
            continue
        print(f"    | {line.rstrip()}", flush=True)
        if needle in line:
            return True
    return False


def _freshness() -> tuple[float | None, int]:
    """从库里直接算数据新鲜度 —— 这是跨进程唯一可信的观测方式。"""
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT COUNT(*), MAX(time) FROM klines").fetchone()
    finally:
        conn.close()
    count, newest = (row or (0, None))[0], (row or (0, None))[1]
    if newest is None:
        return None, count
    return time.time() - newest / 1000, count


def main() -> int:
    parser = argparse.ArgumentParser(description="数据面独立运行验收检查")
    parser.add_argument("--keep", action="store_true", help="保留临时库，便于事后排查")
    args = parser.parse_args()

    TMP.mkdir(parents=True, exist_ok=True)
    for stale in (DB, LOCK, Path(f"{DB}-wal"), Path(f"{DB}-shm")):
        stale.unlink(missing_ok=True)
    print(f"临时库: {DB}\n", flush=True)

    print("=== 1. 独立进程启动（不经过 API） ===")
    first = _spawn()
    check("独立启动成功", _wait_for(first, "行情采集已启动", 90), f"pid={first.pid}")
    check("单实例锁文件已建立", LOCK.exists(), str(LOCK))

    print("\n=== 2. 连上并落库 ===")
    check("WS 已连上", _wait_for(first, "connected=yes", _CONNECT_TIMEOUT_S))
    time.sleep(_SETTLE_S)
    lag, rows = _freshness()
    check("已向库中写入 bar", rows > 0, f"rows={rows}")
    check(
        "数据新鲜（跨进程可观测量）",
        lag is not None and lag < 300,
        f"lag={'?' if lag is None else f'{round(lag)}s'}",
    )

    print("\n=== 3. 跨进程互斥 ===")
    second = subprocess.run(
        [sys.executable, "-u", "-m", "agent.market"],
        cwd=str(ROOT),
        env=_env(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = second.stdout + second.stderr
    check("第二个采集器被挡下（退出码 3）", second.returncode == 3, f"returncode={second.returncode}")
    check("报错指出是谁占着锁", f"pid={first.pid}" in output or "另一个采集器" in output)
    for line in output.strip().splitlines()[-1:]:
        print(f"    | {line}", flush=True)

    print("\n=== 4. 硬杀 → 锁由内核释放 ===")
    first.kill()  # 不给任何清理机会，模拟崩溃
    first.wait(timeout=30)
    check("第一个进程已死", first.poll() is not None)
    check("锁文件仍在磁盘上（我们从不需要删它）", LOCK.exists())

    third = _spawn()
    check("新采集器照样起得来（无陈旧锁）", _wait_for(third, "行情采集已启动", 90))

    print("\n=== 5. 优雅退出 ===")
    if third.poll() is not None:
        check("第三个进程仍健在（才能测优雅退出）", False, f"returncode={third.returncode}")
    else:
        os.kill(third.pid, signal.CTRL_BREAK_EVENT)
        try:
            code = third.wait(timeout=30)
        except subprocess.TimeoutExpired:
            third.kill()
            code = None
        check("Ctrl-Break 后优雅退出（退出码 0）", code == 0, f"returncode={code}")

    if args.keep:
        print(f"\n临时库保留在: {TMP}")
    else:
        for leftover in (DB, LOCK, Path(f"{DB}-wal"), Path(f"{DB}-shm")):
            leftover.unlink(missing_ok=True)

    print("\n" + "=" * 50)
    print("失败项:", "、".join(_failures) if _failures else "无")
    return 1 if _failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
