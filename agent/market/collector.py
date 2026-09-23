"""行情采集器：WS 主通道 + REST 回补。

与 AgentRunner 并列的独立 asyncio 任务，各自生命周期与快照（ADR-010）。

核心不变量：
- 只在 bar 关闭（k.x == true）后落库
- 重连后**先补缺口再恢复订阅**
- 断线是常态（经代理的长连接会频繁被重置），指数退避而非崩溃
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import websockets

from agent.config import Settings, get_settings
from agent.market.backfill import backfill_gaps, backfill_history
from agent.market.bars import interval_ms, last_closed_bar_open, parse_ws_kline
from agent.market.repository import KlineRepository

logger = logging.getLogger(__name__)

# 日常巡检只看最近这么久的缺口：全量空白由 backfill_history 负责，
# 避免每次巡检都读取数百万行已存时间戳
_GAP_LOOKBACK_MS = 2 * 24 * 3_600_000

_BACKOFF_START_S = 1.0
_BACKOFF_MAX_S = 60.0
# 连续收到这么多条消息才算「连稳了」，据此重置退避
_STABLE_MESSAGES = 3


def build_ws_url(base: str, symbols: list[str], interval: str) -> str:
    """单标点对点流，多标的合并流（ADR-016：按订阅集合建模）。"""
    streams = "/".join(f"{s.lower()}@kline_{interval}" for s in symbols)
    if len(symbols) == 1:
        return f"{base.rstrip('/')}/{streams}"
    root = base.rstrip("/")
    if root.endswith("/ws"):
        root = root[: -len("/ws")]
    return f"{root}/stream?streams={streams}"


def install_ws_noise_filter() -> None:
    """抑制 websockets 16.0 的 teardown bug 噪声（设计文档 §6 库级问题 1）。

    连接被重置时，websockets 自身的 connection_lost 清理路径会抛
    AttributeError: 'ClientConnection' object has no attribute 'recv_messages'。
    经代理的长连接会频繁遇到重置，不处理会淹没日志。
    """

    def handler(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exc = context.get("exception")
        if isinstance(exc, AttributeError) and "recv_messages" in str(exc):
            logger.debug("suppressed websockets teardown bug: %s", exc)
            return
        loop.default_exception_handler(context)

    try:
        asyncio.get_running_loop().set_exception_handler(handler)
    except RuntimeError:
        pass


@dataclass
class CollectorSnapshot:
    running: bool = False
    connected: bool = False
    symbols: list[str] = field(default_factory=list)
    interval: str = "1m"
    started_at: str | None = None
    connected_at: str | None = None
    last_write_at: str | None = None
    bars_written: int = 0  # 本次进程生命周期内写入
    reconnects: int = 0
    gaps_filled: int = 0
    last_gap_check_at: str | None = None
    backfill_running: bool = False
    backfill_last: dict[str, Any] | None = None
    backfill_error: str | None = None
    last_error: str | None = None
    last_error_at: str | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat()


class MarketCollector:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._task: asyncio.Task[None] | None = None
        # 后台回补把 BackfillStats 转成 dict 返回（供 POST /market/backfill 复用）
        self._backfill_task: asyncio.Task[Any] | None = None
        self._stop_event = asyncio.Event()
        self._snapshot = CollectorSnapshot(
            symbols=self._settings.market_symbol_list,
            interval=self._settings.market_interval,
        )
        self._repo = KlineRepository()

    @property
    def running(self) -> bool:
        return self._snapshot.running

    async def start(self) -> None:
        if self._snapshot.running:
            return
        cfg = self._settings
        if not cfg.market_symbol_list:
            raise RuntimeError("MARKET_SYMBOLS is empty")
        interval_ms(cfg.market_interval)  # 早失败：周期非法就别启动

        self._stop_event.clear()
        self._snapshot.running = True
        self._snapshot.started_at = _now()
        self._task = asyncio.create_task(self._loop(), name="bianca-market-collector")
        logger.info(
            "Market collector started (symbols=%s interval=%s)",
            cfg.market_symbol_list,
            cfg.market_interval,
        )

    async def stop(self) -> None:
        self._stop_event.set()
        for task in (self._task, self._backfill_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001 — 停机不抛
                    pass
        self._task = None
        self._backfill_task = None
        self._snapshot.running = False
        self._snapshot.connected = False
        logger.info("Market collector stopped")

    async def get_snapshot(self) -> CollectorSnapshot:
        return self._snapshot

    # ------------------------------------------------------------------ 回补

    async def backfill_history_now(self) -> dict[str, Any]:
        """触发一次全量历史回补（幂等、可重入）。"""
        if self._snapshot.backfill_running:
            return {"status": "already_running"}
        self._snapshot.backfill_error = None
        self._snapshot.backfill_running = True
        try:
            stats = await backfill_history(
                symbol=self._settings.market_symbol_list[0],
                interval=self._settings.market_interval,
                settings=self._settings,
            )
            self._snapshot.backfill_last = stats.as_dict()
            return {"status": "ok", **stats.as_dict()}
        except Exception as exc:  # noqa: BLE001
            self._snapshot.backfill_error = str(exc)
            logger.exception("history backfill failed")
            return {"status": "error", "detail": str(exc)}
        finally:
            self._snapshot.backfill_running = False

    # ------------------------------------------------------------------ 主循环

    async def _loop(self) -> None:
        install_ws_noise_filter()
        if self._settings.market_backfill_on_start:
            # 全量回补要十几分钟，不能阻塞启动 —— 放后台跑，与实时采集并行
            self._backfill_task = asyncio.create_task(
                self.backfill_history_now(), name="bianca-market-backfill"
            )

        backoff = _BACKOFF_START_S
        while not self._stop_event.is_set():
            try:
                await self._stream_once()
                backoff = _BACKOFF_START_S
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — 采集器必须活着
                self._snapshot.connected = False
                self._snapshot.last_error = f"{type(exc).__name__}: {exc}"
                self._snapshot.last_error_at = _now()
                self._snapshot.reconnects += 1
                logger.warning("market stream dropped: %s (retry in %.1fs)", exc, backoff)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                    break
                except TimeoutError:
                    pass
                backoff = min(backoff * 2, _BACKOFF_MAX_S)

    async def _stream_once(self) -> None:
        cfg = self._settings
        url = build_ws_url(cfg.market_ws_base_url, cfg.market_symbol_list, cfg.market_interval)
        proxy = cfg.market_proxy or None

        async with websockets.connect(url, proxy=proxy, open_timeout=20, ping_interval=20) as ws:
            self._snapshot.connected = True
            self._snapshot.connected_at = _now()
            logger.info("market stream connected: %s", url)

            # 重连后先补缺口，再恢复订阅 —— 顺序不能反（设计文档 §2.3）
            await self._fill_gaps()

            seen = 0
            async for raw in ws:
                if self._stop_event.is_set():
                    return
                row = self._parse(raw)
                if row is None:
                    continue
                written = await self._repo.insert_rows([row])
                if written:
                    self._snapshot.bars_written += written
                    self._snapshot.last_write_at = _now()
                seen += 1
                if seen == _STABLE_MESSAGES:
                    self._snapshot.last_error = None

    def _parse(self, raw: str | bytes) -> dict[str, Any] | None:
        try:
            event = json.loads(raw)
        except (ValueError, TypeError):
            return None
        return parse_ws_kline(event)

    async def _fill_gaps(self) -> None:
        cfg = self._settings
        symbol = cfg.market_symbol_list[0]
        interval = cfg.market_interval
        try:
            stats = await backfill_gaps(
                symbol,
                interval,
                lookback_ms=_GAP_LOOKBACK_MS,
                settings=cfg,
            )
            self._snapshot.last_gap_check_at = _now()
            if stats.rows_inserted:
                self._snapshot.gaps_filled += stats.rows_inserted
                logger.info("gap check filled %s bars", stats.rows_inserted)
        except Exception as exc:  # noqa: BLE001 — 补洞失败不应中断订阅
            logger.warning("gap check failed: %s", exc)
            self._snapshot.last_gap_check_at = _now()


async def market_status_detail(
    collector: MarketCollector, settings: Settings | None = None
) -> dict[str, Any]:
    """供 API 用的状态聚合：采集器快照 + 库内事实。"""
    cfg = settings or get_settings()
    snap = await collector.get_snapshot()
    symbol = cfg.market_symbol_list[0] if cfg.market_symbol_list else ""
    interval = cfg.market_interval

    repo = KlineRepository()
    last_bar = await repo.last_bar_time(symbol, interval)
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    lag_seconds: int | None = None
    if last_bar is not None:
        lag_seconds = max((now_ms - last_bar) // 1000 - interval_ms(interval) // 1000, 0)

    day_ago = now_ms - 24 * 3_600_000
    return {
        "collector_running": snap.running,
        "connected": snap.connected,
        "symbols": snap.symbols,
        "interval": snap.interval,
        "last_bar_open_time": last_bar,
        "last_closed_bar_open_time": last_closed_bar_open(now_ms, interval),
        "lag_seconds": lag_seconds,
        "bars_written_session": snap.bars_written,
        "bars_count_24h": await repo.count_since(symbol, interval, day_ago),
        "gap_count_24h": snap.gaps_filled,
        "reconnects_session": snap.reconnects,
        "last_gap_check_at": snap.last_gap_check_at,
        "backfill_running": snap.backfill_running,
        "backfill_last": snap.backfill_last,
        "backfill_error": snap.backfill_error,
        "last_error": snap.last_error,
        "last_error_at": snap.last_error_at,
        "last_write_at": snap.last_write_at,
        "started_at": snap.started_at,
        "database": _database_label(cfg.market_database_url),
    }


def _database_label(url: str) -> str:
    return url.rsplit("/", 1)[-1] if "/" in url else url


# 采集器静默死亡是风险 #1：数据断流但无人知。超过这么多个周期没新 bar 即报 degraded。
_STALE_LAG_S = 180


async def market_health(
    collector: MarketCollector | None = None, settings: Settings | None = None
) -> tuple[str, str | None]:
    """给 /health 用的轻量探活（不跑 count_since，避免把健康检查变重）。

    返回 (status, detail)。status ∈ ok / warming_up / disabled / error。
    """
    cfg = settings or get_settings()
    if not cfg.market_collector_autostart:
        return "disabled", None

    collector = collector or get_collector()
    snap = await collector.get_snapshot()
    if not snap.running:
        return "error", "collector not running"

    symbol = cfg.market_symbol_list[0] if cfg.market_symbol_list else ""
    last_bar = await KlineRepository().last_bar_time(symbol, cfg.market_interval)
    if last_bar is None:
        # 冷启动：历史回补还没写入第一根，不算故障
        return "warming_up", "no bars yet (initial backfill in progress)"

    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    lag_s = max((now_ms - last_bar) // 1000 - interval_ms(cfg.market_interval) // 1000, 0)
    if lag_s > _STALE_LAG_S:
        return "error", f"stale: lag {lag_s}s > {_STALE_LAG_S}s"
    return "ok", f"lag {lag_s}s"


_collector: MarketCollector | None = None


def get_collector() -> MarketCollector:
    global _collector
    if _collector is None:
        _collector = MarketCollector()
    return _collector
