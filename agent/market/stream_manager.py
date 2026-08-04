from __future__ import annotations

import asyncio
import logging

from agent.config import Settings, get_settings
from agent.exchange.market_stream import MarketStream
from agent.exchange.quotes import ticker_to_response
from agent.market.ticker_cache import set_ticker

logger = logging.getLogger(__name__)


class MarketStreamManager:
    """后台 WebSocket ticker 订阅，写入内存缓存供 Agent / 看板优先读取。"""

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task[None]] = []
        self._stop_event = asyncio.Event()
        self._stream: MarketStream | None = None

    @property
    def running(self) -> bool:
        return bool(self._tasks) and not self._stop_event.is_set()

    async def start(self, symbols: list[str], settings: Settings | None = None) -> None:
        cfg = settings or get_settings()
        if not cfg.market_stream_enabled or not cfg.binance_configured:
            return
        if not symbols:
            return

        await self.stop()
        self._stop_event.clear()
        self._stream = MarketStream(cfg)
        await self._stream.__aenter__()
        for sym in symbols:
            task = asyncio.create_task(self._watch(sym), name=f"market-stream-{sym}")
            self._tasks.append(task)
        logger.info("Market stream started symbols=%s", symbols)

    async def stop(self) -> None:
        self._stop_event.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        if self._stream is not None:
            await self._stream.__aexit__(None, None, None)
            self._stream = None
        logger.info("Market stream stopped")

    async def _watch(self, symbol: str) -> None:
        stream = self._stream
        if stream is None:
            return
        try:
            async for ticker in stream.watch_ticker(symbol):
                if self._stop_event.is_set():
                    break
                set_ticker(symbol, ticker_to_response(ticker))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Market stream watch failed symbol=%s", symbol)


_manager: MarketStreamManager | None = None


def get_stream_manager() -> MarketStreamManager:
    global _manager
    if _manager is None:
        _manager = MarketStreamManager()
    return _manager
