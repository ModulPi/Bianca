"""把数据面采集注册成 Windows 计划任务（ADR-017 缺口 2：进程守护）。

解决的问题：独立进程不等于无人值守。独立进程只是"可以不由 API 拉起"，但谁来在
崩溃后拉起它、谁来在重启后启动它？没有守护，关机后就不会自动回来。

为什么用计划任务而不是 Windows 服务：服务更彻底（开机即跑、无需登录），但要装
NSSM 之类的包装器，或者把 Python 进程包成服务。计划任务是系统自带的，零依赖。
代价是"只在用户登录后运行"（见下）。

**要真正开机即采集（无需登录），需要两处改动**，都得你自己做（涉及密码）：

1. 换用存储凭据：`schtasks /Create /TN <名字> /XML task.xml /RU <域\\用户名> /RP <密码>`
   —— 带 `/RP` 会把凭据交给任务计划程序保存，这一步不该经过我。
2. 加 `--boot-trigger` 生成开机触发。实测：带 `<BootTrigger>` 的任务在**未提权**
   会话里注册会被直接拒绝（"拒绝访问"），去掉就能成功；而只加 BootTrigger 而不换
   凭据也没用 —— 交互式令牌下登录前跑不起来，注册本身就失败。

用法：
    python scripts/install_market_task.py --dry-run          # 只打印将注册的描述符
    python scripts/install_market_task.py --install          # 注册（已存在则拒绝）
    python scripts/install_market_task.py --install --force  # 覆盖同名任务
    python scripts/install_market_task.py --install --start   # 注册后立即启动
    python scripts/install_market_task.py --status           # 看任务当前状态
    python scripts/install_market_task.py --uninstall         # 注销
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.market.task import MarketTaskSpec  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG = ROOT / "logs" / "market-collector.log"


def _current_user_id() -> str:
    domain = os.environ.get("USERDOMAIN", "")
    user = os.environ.get("USERNAME", "")
    return f"{domain}\\{user}" if domain and user else (user or "UNKNOWN")


def _schtasks(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["schtasks", *args],
        capture_output=True,
        text=True,
        errors="replace",  # 中文 Windows 下 schtasks 输出是 cp936
    )


def _task_exists(name: str) -> bool:
    return _schtasks(["/Query", "/TN", name]).returncode == 0


def _build_spec(args: argparse.Namespace) -> MarketTaskSpec:
    return MarketTaskSpec(
        python_exe=sys.executable,
        repo_root=str(ROOT),
        user_id=args.user or _current_user_id(),
        log_file=str(Path(args.log_file) if args.log_file else DEFAULT_LOG),
        status_interval_s=args.status_interval,
        name=args.name,
        supervise_interval_min=args.supervise_interval,
        boot_trigger=args.boot_trigger,
    )


def _cmd_install(args: argparse.Namespace, spec: MarketTaskSpec) -> int:
    if _task_exists(spec.name) and not args.force:
        print(
            f"任务 {spec.name!r} 已存在，拒绝覆盖。先 `--status` 看它是什么，"
            f"确认要替换再加 --force。",
            file=sys.stderr,
        )
        return 2

    xml = spec.to_xml()
    # 计划任务要求 XML 是 Unicode（UTF-16）；写成 UTF-8 不报格式错，
    # 但中文描述会变成乱码
    with tempfile.NamedTemporaryFile(
        "w", suffix=".xml", encoding="utf-16", delete=False
    ) as fh:
        fh.write(xml)
        xml_path = fh.name

    try:
        result = _schtasks(["/Create", "/TN", spec.name, "/XML", xml_path, "/F"])
    finally:
        Path(xml_path).unlink(missing_ok=True)

    if result.returncode != 0:
        print(f"注册失败（退出码 {result.returncode}）:", file=sys.stderr)
        print((result.stderr or result.stdout).strip(), file=sys.stderr)
        print(
            "\n提示：非管理员会话通常也能为当前用户注册计划任务；若报权限错误，"
            "改用管理员终端重试。",
            file=sys.stderr,
        )
        return 1

    print(f"已注册计划任务: {spec.name}")
    print(f"  可执行: {spec.python_exe}")
    print(f"  参数  : {spec.arguments}")
    print(f"  工作目录: {spec.repo_root}")
    print(f"  日志  : {spec.log_file}")
    triggers = "用户登录时"
    if spec.boot_trigger:
        triggers += f" + 开机后 {spec.boot_delay_min} 分钟"
    print(f"  触发  : {triggers}（错过的触发会在下次可用时补跑）")
    print(
        f"  守护  : 每 {spec.supervise_interval_min} 分钟唤醒一次 —— 采集器活着时被 "
        f"IgnoreNew 吞掉，死了就由这次唤醒拉起（崩溃恢复最长 {spec.supervise_interval_min} 分钟）"
    )
    if not spec.boot_trigger:
        print(
            "        注意：这是交互式令牌 —— **不登录就不会采集**。"
            "要开机即采集见文件头说明（需存储凭据 + --boot-trigger）"
        )

    if args.start:
        started = _schtasks(["/Run", "/TN", spec.name])
        if started.returncode == 0:
            print("已启动。约 20 秒后用 --status 或看日志确认它真的在采集。")
        else:
            print(f"启动失败: {(started.stderr or started.stdout).strip()}", file=sys.stderr)
            return 1
    else:
        print(f"未自动启动 —— 需要时: schtasks /Run /TN \"{spec.name}\"")
    return 0


def _cmd_uninstall(args: argparse.Namespace, spec: MarketTaskSpec) -> int:
    if not _task_exists(spec.name):
        print(f"任务 {spec.name!r} 不存在，无需卸载。")
        return 0
    result = _schtasks(["/Delete", "/TN", spec.name, "/F"])
    if result.returncode != 0:
        print(f"卸载失败: {(result.stderr or result.stdout).strip()}", file=sys.stderr)
        return 1
    print(f"已注销计划任务: {spec.name}")
    print(
        "注意：注销任务只停止「自动拉起」，不会杀掉正在跑的采集器进程。"
        "要一并停掉，用 --status 看进程 id，或 taskkill。"
    )
    return 0


def _cmd_status(args: argparse.Namespace, spec: MarketTaskSpec) -> int:
    if not _task_exists(spec.name):
        print(f"任务 {spec.name!r} 未注册。")
        print("（这不代表数据面没在跑 —— 它可能是手动或用别的方式启动的，见下文提示。）")
        return 1

    result = _schtasks(["/Query", "/TN", spec.name, "/V", "/FO", "LIST"])
    text = (result.stdout or "").strip()
    print(text)

    # 计划任务只报"上次结果"，不报"现在有没有在写数据"。补一句来自库的实测事实，
    # 否则「上次结果 0x41301（正在运行）」会让你以为一切正常，而采集可能早就断流了
    print("\n--- 库侧实测（计划任务不提供这个视角）---")
    rc = subprocess.run(
        [sys.executable, "-u", "-m", "agent.market", "--status-once"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        errors="replace",
    )
    for line in (rc.stdout or "").splitlines():
        if any(k in line for k in ("lag_seconds", "bars_count_24h", "gap_count_24h", "last_bar_open_time")):
            print("  " + line.strip())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bianca 数据面计划任务（进程守护）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--name", default="BiancaMarketDataPlane", help="计划任务名")
    parser.add_argument("--user", default=None, help="默认 域\\用户名")
    parser.add_argument("--log-file", default=None, help=f"默认 {DEFAULT_LOG}")
    parser.add_argument(
        "--status-interval", type=int, default=300, help="状态行间隔秒数，0 关闭"
    )
    parser.add_argument(
        "--supervise-interval",
        type=int,
        default=5,
        help="守护唤醒间隔（分钟）。采集器活着时唤醒被 IgnoreNew 吞掉，死了就把它拉起来，"
        "所以这也是崩溃恢复的最长延迟（默认 5 分钟）",
    )
    parser.add_argument(
        "--boot-trigger",
        action="store_true",
        help="加开机触发。需要管理员权限，且必须配合存储凭据才有意义（见文件头说明）",
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="只打印将注册的 XML")
    group.add_argument("--install", action="store_true", help="注册计划任务")
    group.add_argument("--uninstall", action="store_true", help="注销计划任务")
    group.add_argument("--status", action="store_true", help="查看任务状态 + 库侧实测")
    parser.add_argument("--force", action="store_true", help="允许覆盖同名任务")
    parser.add_argument("--start", action="store_true", help="注册后立即启动")

    args = parser.parse_args(argv)

    if os.name != "nt":
        print("本脚本只适用于 Windows（计划任务是 Windows 机制）", file=sys.stderr)
        return 1

    spec = _build_spec(args)

    if args.dry_run:
        print(spec.to_xml())
        return 0
    if args.install:
        return _cmd_install(args, spec)
    if args.uninstall:
        return _cmd_uninstall(args, spec)
    return _cmd_status(args, spec)


if __name__ == "__main__":
    raise SystemExit(main())
