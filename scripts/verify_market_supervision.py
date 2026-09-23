"""验收检查：进程守护是否真的能拉起采集器（ADR-017 缺口 2）。

**用临时库 + 一次性任务名，且不需要管理员权限** —— 所以线上采集器在跑的时候也能
安全执行，跑完自动清理。

检查四件事：
1. 计划任务能把采集器拉起来并真的落库
2. 防重复：再唤醒一次不会起第二个实例（IgnoreNew）
3. 崩溃自恢复：硬杀采集器后，由下一次唤醒把它拉回来，且恢复后继续写数据
4. 恢复机制不依赖 RestartOnFailure（实测它在本机不生效，见 agent/market/task.py）

用法：
    python -u scripts/verify_market_supervision.py
    python -u scripts/verify_market_supervision.py --interval 1   # 默认 1 分钟，跑得快
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.market.task import MarketTaskSpec  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TASK = "BiancaMarketDataPlane-SelfTest"
TMP = Path(os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp") / "bianca-supervision"
DB = TMP / "market.db"
LOG = TMP / "selftest.log"
WRAPPER = TMP / "run.cmd"
XML = TMP / "task.xml"
CMD = r"C:\Windows\System32\cmd.exe"

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)
    if not ok:
        _failures.append(name)


def schtasks(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True, errors="replace")


def collector_pids() -> list[int]:
    """找出跑在临时库上的采集器进程（靠命令行里的临时日志路径区分）。"""
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{LOG}*' }} | "
        "Select-Object -ExpandProperty ProcessId"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        errors="replace",
    ).stdout
    return [int(x) for x in re.findall(r"\d+", out)]


def rows_and_lag() -> tuple[int, float | None]:
    if not DB.exists():
        return 0, None
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        count, newest = conn.execute("SELECT COUNT(*), MAX(time) FROM klines").fetchone()
    finally:
        conn.close()
    if newest is None:
        return count or 0, None
    return count or 0, time.time() - newest / 1000


def wait_until(pred, timeout: float, label: str, interval: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(interval)
    print(f"    （等待超时: {label}）", flush=True)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="数据面进程守护验收检查")
    parser.add_argument("--interval", type=int, default=1, help="守护唤醒间隔（分钟）")
    args = parser.parse_args()

    if os.name != "nt":
        print("计划任务是 Windows 机制，本脚本只适用于 Windows", file=sys.stderr)
        return 1

    TMP.mkdir(parents=True, exist_ok=True)
    for stale in (DB, LOG, Path(f"{DB}-wal"), Path(f"{DB}-shm")):
        stale.unlink(missing_ok=True)
    print(f"临时库: {DB}\n", flush=True)

    # 环境注入只在这个包装脚本里；任务的 Settings/Triggers 全部取自真实描述符
    WRAPPER.write_text(
        "@echo off\r\n"
        f"set MARKET_DATABASE_URL=sqlite+aiosqlite:///{DB.as_posix()}\r\n"
        "set MARKET_BACKFILL_ON_START=false\r\n"
        f'"{sys.executable}" -u -m agent.market --status-interval 0 --log-file "{LOG}"\r\n',
        encoding="utf-8",
    )

    spec = MarketTaskSpec(
        python_exe=sys.executable,
        repo_root=str(ROOT),
        user_id=os.environ.get("USERNAME", "unknown"),
        log_file=str(LOG),
        status_interval_s=0,
        name=TASK,
        supervise_interval_min=args.interval,
    )
    # 只换 Action（换成包装脚本以注入临时库），其余原样
    action = (
        '<Actions Context="Author">\n    <Exec>\n'
        f"      <Command>{CMD}</Command>\n"
        f'      <Arguments>/c "{WRAPPER}"</Arguments>\n'
        f"      <WorkingDirectory>{ROOT}</WorkingDirectory>\n"
        "    </Exec>\n  </Actions>"
    )
    xml = re.sub(
        r'<Actions Context="Author">.*?</Actions>', lambda _m: action, spec.to_xml(), flags=re.DOTALL
    )
    XML.write_text(xml, encoding="utf-16")

    print("=== 0. 注册一次性任务（不需要管理员） ===")
    schtasks("/Delete", "/TN", TASK, "/F")
    result = schtasks("/Create", "/TN", TASK, "/XML", str(XML), "/F")
    check("注册成功", result.returncode == 0, (result.stdout or result.stderr).strip()[:100])
    if result.returncode != 0:
        for line in (result.stdout + result.stderr).strip().splitlines():
            print("   |", line, flush=True)
        return 1

    try:
        print("\n=== 1. 拉起来并落库 ===")
        schtasks("/Run", "/TN", TASK)
        started = wait_until(lambda: rows_and_lag()[0] > 0, 150, "等写 bar")
        count, lag = rows_and_lag()
        check("任务拉起的采集器写出了 bar", started, f"rows={count} lag={None if lag is None else round(lag)}s")
        check("有且只有一个采集器进程", len(collector_pids()) == 1, f"pids={collector_pids()}")

        print("\n=== 2. 防重复：再唤醒一次 ===")
        before = collector_pids()
        schtasks("/Run", "/TN", TASK)
        time.sleep(10)
        after = collector_pids()
        check(
            "IgnoreNew 生效，没起第二个",
            len(after) == 1 and set(after) == set(before),
            f"before={before} after={after}",
        )

        print("\n=== 3. 崩溃自恢复：硬杀采集器 ===")
        victim = after[0]
        subprocess.run(["taskkill", "/F", "/PID", str(victim)], capture_output=True)
        killed = wait_until(lambda: victim not in collector_pids(), 30, "等被杀干净")
        check("已硬杀", killed, f"pid={victim}")

        print(f"    等下一次唤醒（间隔 {args.interval} 分钟）把它拉回来…", flush=True)
        revived = wait_until(lambda: len(collector_pids()) == 1, args.interval * 60 + 120, "等自动重启")
        check("被守护自动拉起来了", revived, f"pids={collector_pids()}")
        if revived:
            writing = wait_until(lambda: rows_and_lag()[0] > count, 150, "等恢复后继续写")
            new_count, new_lag = rows_and_lag()
            check(
                "恢复后仍在写数据",
                writing,
                f"rows {count} → {new_count} lag={None if new_lag is None else round(new_lag)}s",
            )
            check("恢复后落库数据新鲜", new_lag is not None and new_lag < 300)
    finally:
        print("\n=== 4. 清理 ===")
        schtasks("/Delete", "/TN", TASK, "/F")
        for pid in collector_pids():
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
        for leftover in (DB, LOG, Path(f"{DB}-wal"), Path(f"{DB}-shm")):
            leftover.unlink(missing_ok=True)
        print("    已注销任务、杀掉残留进程、删除临时文件", flush=True)

    print("\n" + "=" * 50)
    print("失败项:", "、".join(_failures) if _failures else "无")
    return 1 if _failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
