"""数据面独立入口：让行情采集脱离 API 进程运行（ADR-017 缺口 1）。

    python -m agent.market                      # 前台运行，Ctrl-C 优雅退出
    python -m agent.market --status-interval 60 # 每分钟打一行状态
    python -m agent.market --status-once        # 打一份完整状态就退出（排障/自检）

**与 API 的关系：完全不依赖。** 反过来说，两者不能同时采集同一个库 —— 采集器内
的单实例锁（`agent.market.lock`）会拦住后来者。以独立数据面方式运行时，API 进程
应设 `MARKET_COLLECTOR_AUTOSTART=false`，否则两个进程会抢锁，谁赢是不确定的。

**关于状态行**：`market_health()` 现在也只看数据流动（不再和 `AUTOSTART` 绑），
但这里仍然读 `market_status_detail()` —— 状态行要的是**事实**（连上没有、落后多少、
回补在不在跑），不是一句健康结论；结论留给 `/health`。

退出码：0 正常退出 / 1 配置或启动失败 / 3 已有采集器在跑（单实例锁）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from agent.config import Settings, get_settings
from agent.market.collector import MarketCollector, get_collector, market_status_detail
from agent.market.lock import CollectorLockError
from agent.market.storage import close_market_db, init_market_db

logger = logging.getLogger("bianca.market")

EXIT_OK = 0
EXIT_STARTUP_ERROR = 1
EXIT_ALREADY_RUNNING = 3

_DEFAULT_STATUS_INTERVAL_S = 300


def _configure_logging(level: str, log_file: str | None = None) -> None:
    """日志到 stdout；`--log-file` 时同时（也是主要地）落到文件。

    被计划任务/服务拉起时必须有文件日志：守护进程没有终端，stdout 会被丢掉，
    于是"它到底崩溃过没有、被拉起来几次"就完全不可见了 —— 一个看不见重启的
    守护进程等于没有守护。
    """
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        # 轮转而非追加：守护进程会跑很多年，无界日志最终会填满磁盘
        handlers.append(
            RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        )

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )


def _status_line(detail: dict) -> str:
    """一行状态。字段取事实，不取健康结论。

    跨进程字段在顶层，进程内字段在 `session` 里（见 `market_status_detail` 的分区
    原则）。本进程没在做行情相关的事时 `session` 是 null —— 那时这些字段不是"没有"，
    是"本进程没有这个视角"，所以打 `session=n/a` 而不是把它们当 false 报出去。
    """
    lag = detail.get("lag_seconds")
    lag_s = "-" if lag is None else f"{float(lag):.0f}s"
    parts = [
        f"lag={lag_s}",
        f"bars24h={detail.get('bars_count_24h')}",
        f"gaps24h={detail.get('gap_count_24h')}",
        f"flowing={'yes' if detail.get('data_flowing') else 'no '}",
        f"owner={detail.get('collector_owner')}",
    ]
    session = detail.get("session")
    if session:
        # 这个进程就是采集器：把只有它知道的事插进去
        parts.insert(0, f"connected={'yes' if session.get('connected') else 'no '}")
        parts.append(f"session_bars={session.get('bars_written_session')}")
        parts.append(f"reconnects={session.get('reconnects_session')}")
        parts.append(
            f"backfill={'running' if session.get('backfill_running') else 'idle'}"
        )
        if session.get("last_error"):
            parts.append(f"last_error={session['last_error'][:80]!r}")
    else:
        parts.append("session=n/a")
    return " ".join(parts)


async def _status_loop(
    collector: MarketCollector,
    settings: Settings,
    interval_s: int,
    stop: asyncio.Event,
) -> None:
    """周期性打状态。读状态失败不能拖垮采集 —— 这里绝不能抛。"""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
            return  # stop 被置位
        except TimeoutError:
            pass
        try:
            detail = await market_status_detail(collector, settings)
            logger.info("status: %s", _status_line(detail))
        except Exception:  # noqa: BLE001 — 观测失败不该影响采集
            logger.warning("status probe failed", exc_info=True)


def _install_signal_handlers(stop: asyncio.Event) -> None:
    """信号 → 优雅停机。

    不用 `loop.add_signal_handler` —— Windows 的 ProactorEventLoop 不支持它。
    改成在信号回调里 `call_soon_threadsafe`，这样信号处理本身不做 asyncio 操作。

    连续第二次信号直接硬退出：第一次可能卡在等 WS 关闭握手上，用户需要一条
    "别等了"的路径，否则会以为程序挂了。
    """
    loop = asyncio.get_running_loop()
    seen = 0

    def handler(signum: int, _frame: object) -> None:
        nonlocal seen
        seen += 1
        if seen == 1:
            logger.info("收到信号 %s，优雅停机中（再按一次强制退出）…", signum)
            loop.call_soon_threadsafe(stop.set)
        else:
            logger.warning("收到信号 %s，强制退出", signum)
            os._exit(EXIT_STARTUP_ERROR)

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):
            logger.debug("could not install handler for %s", name, exc_info=True)


async def _print_status_once(settings: Settings) -> int:
    """打印一份完整状态。stdout 是纯 JSON（可管道），说明文字一律走 stderr。"""
    await init_market_db()
    try:
        detail = await market_status_detail(get_collector(), settings)
    finally:
        await close_market_db()
    print(json.dumps(detail, ensure_ascii=False, indent=2, default=str))
    # 这条命令是**独立进程**，所以顶层字段（都是从库和内核锁读出来的）才是这里
    # 该看的东西：`data_flowing`、`lag_seconds`、`collector_owner`。
    # `session` 会是 null —— 那是"本进程没有采集器这个视角"，不是"采集器没在跑"。
    print(
        "\n说明: 本命令是独立进程，因此 session 通常为 null —— 它是**本进程**的自述，"
        "而采集器住在别处。\n"
        "判断数据面是否在跑，看顶层: data_flowing、lag_seconds 是否小、"
        "last_bar_open_time 是否接近现在、collector_owner 是 self/other/none/unknown。\n"
        "其中 collector_owner=other 表示另一个进程正在写（从内核锁只读探出来的）。",
        file=sys.stderr,
    )
    return EXIT_OK


async def _serve(settings: Settings, status_interval_s: int) -> int:
    if settings.market_collector_autostart:
        # 不是错误，但值得警告：两个采集器会抢锁，谁赢不确定
        logger.warning(
            "MARKET_COLLECTOR_AUTOSTART=true —— API 进程也会启动采集器，"
            "两者将争抢同一个库的单实例锁。独立运行数据面时应设为 false。"
        )

    await init_market_db()
    collector = get_collector()
    try:
        await collector.start()  # 抢锁失败会抛 CollectorLockError
    except CollectorLockError as exc:
        logger.error("%s", exc)
        await close_market_db()
        return EXIT_ALREADY_RUNNING

    logger.info(
        "行情采集已启动 symbols=%s interval=%s db=%s",
        settings.market_symbol_list,
        settings.market_interval,
        settings.market_database_url,
    )

    stop = asyncio.Event()
    _install_signal_handlers(stop)
    status_task: asyncio.Task[None] | None = None
    if status_interval_s > 0:
        status_task = asyncio.create_task(
            _status_loop(collector, settings, status_interval_s, stop),
            name="bianca-market-status",
        )

    try:
        await stop.wait()
    finally:
        if status_task is not None:
            status_task.cancel()
            try:
                await status_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 — 停机不抛
                pass
        await collector.stop()
        await close_market_db()
        logger.info("行情采集已停止")
    return EXIT_OK


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m agent.market",
        description="Bianca 行情数据面（独立于 API 进程运行）",
    )
    parser.add_argument(
        "--status-interval",
        type=int,
        default=_DEFAULT_STATUS_INTERVAL_S,
        help=f"每多少秒打一行状态，0 关闭（默认 {_DEFAULT_STATUS_INTERVAL_S}）",
    )
    parser.add_argument(
        "--status-once",
        action="store_true",
        help="打印一份完整状态后立即退出（只读，不启动采集器）。"
        "顶层字段（data_flowing / lag_seconds / collector_owner）跨进程可信；"
        "session 是本进程自述，独立进程里为 null",
    )
    parser.add_argument("--log-level", default=None, help="默认取配置 LOG_LEVEL")
    parser.add_argument(
        "--log-file",
        default=None,
        help="同时把日志写到该文件（5MB 轮转，留 3 份）。被计划任务/服务拉起时必给 —— "
        "守护进程没有终端，不给文件日志就看不到它崩溃过没有",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()
    _configure_logging(args.log_level or settings.log_level, args.log_file)

    if not settings.market_symbol_list:
        logger.error("MARKET_SYMBOLS 为空，无法采集")
        return EXIT_STARTUP_ERROR
    if not settings.market_proxy:
        logger.warning("BINANCE_PROXY 未配置 —— 大陆网络下直连币安会超时")

    try:
        if args.status_once:
            return asyncio.run(_print_status_once(settings))
        return asyncio.run(_serve(settings, args.status_interval))
    except KeyboardInterrupt:
        return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
