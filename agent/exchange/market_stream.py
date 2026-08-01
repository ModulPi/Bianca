from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import ccxt.pro as ccxtpro

from agent.config import Settings, get_settings
from agent.exchange._client import build_binance_config


class MarketStream:
    """WebSocket ticker stream for Binance Demo spot via ccxt.pro."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._exchange: ccxtpro.binance | None = None
        self._running = False

    def _build_exchange(self) -> ccxtpro.binance:
        exchange = ccxtpro.binance(build_binance_config(self._settings))
        exchange.enable_demo_trading(True)
        return exchange

    async def __aenter__(self) -> MarketStream:
        self._exchange = self._build_exchange()
        await self._exchange.load_markets()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def close(self) -> None:
        self._running = False
        if self._exchange is not None:
            await self._exchange.close()
            self._exchange = None

    @property
    def exchange(self) -> ccxtpro.binance:
        if self._exchange is None:
            raise RuntimeError("MarketStream not opened; use async with")
        return self._exchange

    async def watch_ticker(
        self,
        symbol: str | None = None,
        *,
        on_tick: Callable[[dict[str, Any]], None] | None = None,
        max_ticks: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        sym = symbol or self._settings.trade_symbol
        self._running = True
        count = 0
        while self._running:
            ticker = await self.exchange.watch_ticker(sym)
            if on_tick is not None:
                on_tick(ticker)
            yield ticker
            count += 1
            if max_ticks is not None and count >= max_ticks:
                break

    async def fetch_latest_ticker(self, symbol: str | None = None) -> dict[str, Any]:
        """One-shot latest ticker via WebSocket (closes after first tick)."""
        async for ticker in self.watch_ticker(symbol, max_ticks=1):
            return ticker
        raise RuntimeError("No ticker received")


async def sample_ticker(symbol: str | None = None) -> dict[str, Any]:
    settings = get_settings()
    if not settings.binance_configured:
        raise RuntimeError("BINANCE_API_KEY/SECRET not configured")
    async with MarketStream(settings) as stream:
        ticker = await stream.fetch_latest_ticker(symbol)
    return {
        "symbol": ticker.get("symbol"),
        "last": ticker.get("last"),
        "bid": ticker.get("bid"),
        "ask": ticker.get("ask"),
        "timestamp": ticker.get("timestamp"),
    }
