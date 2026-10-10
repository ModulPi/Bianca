"""风控的命令行入口：让"松开回撤线"这件事在 API 进程之外也能做（ADR-030）。

    python -m agent.risk drawdown                                # 看一眼现在的回撤
    python -m agent.risk reset-drawdown --reason "已人工复盘，重新开始"

**为什么除了 HTTP 还要这个。** ADR-029 把"停下来"定成需要人确认的动作，那就得有一条
能确认的路。HTTP 是无人值守（阶段 E）与对话层（阶段 C）的挂载点；但 API 进程本身挂了、
或者根本没起（本地排障）的时候，HTTP 就指望不上 —— 那时这是唯一能把回撤线松开的入口。

两个入口调的是同一个 `reset_drawdown()`，语义必须完全一致。这里**不复写**任何判定逻辑：
读数是只读的 `drawdown()`，重置走 `reset_drawdown()`，与路由逐字相同。

退出码：0 成功 / 1 参数或执行失败 / 2 没给理由（重置被拒）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from agent.config import get_settings
from agent.portfolio.pnl import DrawdownReset, drawdown, reset_drawdown
from agent.storage import database

logger = logging.getLogger("bianca.risk")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 2


def _pct(value: float | None) -> str:
    return "未知" if value is None else f"{value:.4%}"


def _show_drawdown(state) -> None:
    print(f"回撤       : {_pct(state.pct)}{'' if state.known else '（算不出来）'}")
    print(f"峰值权益   : {state.peak_equity}")
    print(f"当前权益   : {state.current_equity}")
    print(f"重置点     : {state.since or '无（峰值看全历史）'}")
    if not state.known and state.reason:
        # 算不出来不是错误、也不是"没回撤"：风控据此拦住开仓
        print(f"说明       : {state.reason}", file=sys.stderr)


def _show_reset(result: DrawdownReset, reason: str) -> None:
    print("已重置回撤基准。")
    print(f"  重置时刻 : {result.reset_at}")
    print(f"  理由     : {reason}")
    print(f"  重置前   : 峰值 {result.peak_before} / 当前 {result.current_equity} "
          f"/ 回撤 {_pct(result.drawdown_before)}")
    print(f"  上一次重置: {result.previous_reset_at or '无'}")
    print("  历史快照一条未动 —— 只是峰值改从这一刻往后看。", file=sys.stderr)
    print("  这次重置已写进 risk_events（event_type=drawdown_reset）。", file=sys.stderr)


async def _cmd_drawdown() -> int:
    state = await drawdown()
    _show_drawdown(state)
    return EXIT_OK


async def _cmd_reset(reason: str) -> int:
    try:
        result = await reset_drawdown(reason=reason)
    except ValueError as exc:
        logger.error("%s", exc)
        return EXIT_REFUSED
    _show_reset(result, reason.strip())
    return EXIT_OK


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m agent.risk",
        description="Bianca 风控状态与回撤重置",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("drawdown", help="打印当前回撤（只读，不写任何东西）")
    show.add_argument("--json", action="store_true", help="以 JSON 输出（便于管道）")

    reset = sub.add_parser(
        "reset-drawdown",
        help="把回撤峰值挪到当下（**会写库**：agent_config + risk_events）",
    )
    reset.add_argument(
        "--reason",
        required=True,
        help="为什么要重置。必填 —— 这是唯一能让回撤线松开的操作，不留痕等于静默解除风控",
    )
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    await database.init_db()
    try:
        if args.command == "reset-drawdown":
            return await _cmd_reset(args.reason)
        if getattr(args, "json", False):
            state = await drawdown()
            print(json.dumps(
                {
                    "known": state.known,
                    "pct": state.pct,
                    "peak_equity": state.peak_equity,
                    "current_equity": state.current_equity,
                    "since": state.since,
                    "reason": state.reason,
                },
                ensure_ascii=False, indent=2,
            ))
            return EXIT_OK
        return await _cmd_drawdown()
    finally:
        await database.close_db()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    settings = get_settings()
    logger.debug("数据库 %s", settings.database_url)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
