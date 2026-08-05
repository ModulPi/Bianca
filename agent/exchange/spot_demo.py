from __future__ import annotations

import asyncio
from typing import Any

import ccxt.async_support as ccxt

from agent.config import Settings, get_settings
from agent.exchange._client import build_binance_config, format_binance_error


def resolve_market_symbol(exchange: ccxt.binance, symbol: str) -> str:
    """BTCUSDT → ccxt unified BTC/USDT (required for create_order)."""
    if symbol in exchange.markets:
        return symbol
    compact = symbol.upper().replace("/", "")
    for market in exchange.markets.values():
        if market.get("id") == compact or market.get("symbol", "").replace("/", "") == compact:
            return market["symbol"]
    if compact.endswith("USDT"):
        return f"{compact[:-4]}/USDT"
    return symbol


class SpotDemoExchange:
    """ccxt wrapper for Binance spot（Demo 或 Live）。"""

    def __init__(self, settings: Settings | None = None, *, live: bool = False) -> None:
        self._settings = settings or get_settings()
        self._live = live
        self._exchange: ccxt.binance | None = None

    def _build_exchange(self) -> ccxt.binance:
        exchange = ccxt.binance(build_binance_config(self._settings))
        if not self._live:
            exchange.enable_demo_trading(True)
        return exchange

    async def __aenter__(self) -> SpotDemoExchange:
        self._exchange = self._build_exchange()
        await self._exchange.load_markets()
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._exchange is not None:
            await self._exchange.close()
            self._exchange = None

    @property
    def exchange(self) -> ccxt.binance:
        if self._exchange is None:
            raise RuntimeError("SpotDemoExchange not opened; use async with")
        return self._exchange

    async def fetch_balance(self) -> dict[str, Any]:
        return await self.exchange.fetch_balance()

    async def fetch_ticker(self, symbol: str | None = None) -> dict[str, Any]:
        sym = symbol or self._settings.trade_symbol
        return await self.exchange.fetch_ticker(sym)

    async def fetch_ohlcv(
        self,
        symbol: str | None = None,
        *,
        timeframe: str = "1m",
        since: int | None = None,
        limit: int = 100,
    ) -> list[list[Any]]:
        raw = symbol or self._settings.trade_symbol
        sym = resolve_market_symbol(self.exchange, raw)
        return await self.exchange.fetch_ohlcv(sym, timeframe, since=since, limit=limit)

    async def create_market_order(
        self,
        side: str,
        amount: float,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        raw = symbol or self._settings.trade_symbol
        sym = resolve_market_symbol(self.exchange, raw)
        return await self.exchange.create_order(sym, "market", side, amount)

    async def create_limit_order(
        self,
        side: str,
        amount: float,
        price: float,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        sym = symbol or self._settings.trade_symbol
        return await self.exchange.create_order(sym, "limit", side, amount, price)

    async def fetch_order(self, order_id: str, symbol: str | None = None) -> dict[str, Any]:
        sym = symbol or self._settings.trade_symbol
        return await self.exchange.fetch_order(order_id, sym)

    async def cancel_order(self, order_id: str, symbol: str | None = None) -> dict[str, Any]:
        sym = symbol or self._settings.trade_symbol
        return await self.exchange.cancel_order(order_id, sym)

    async def ping(self) -> dict[str, Any]:
        """Lightweight connectivity check."""
        ticker = await self.fetch_ticker()
        return {
            "symbol": ticker.get("symbol"),
            "last": ticker.get("last"),
        }


async def check_binance_demo() -> dict[str, str]:
    settings = get_settings()
    if not settings.binance_configured:
        return {"status": "not_configured", "detail": "BINANCE_API_KEY/SECRET missing"}

    try:
        async with SpotDemoExchange(settings, live=False) as demo:
            result = await demo.ping()
        return {
            "status": "ok",
            "detail": f"demo {result['symbol']} last={result['last']}",
        }
    except Exception as exc:  # noqa: BLE001 — health probe
        return {"status": "error", "detail": format_binance_error(exc)}


async def check_binance_live() -> dict[str, str]:
    settings = get_settings()
    if not settings.binance_configured:
        return {"status": "not_configured", "detail": "BINANCE_API_KEY/SECRET missing"}
    if not settings.live_trading_confirmed:
        return {"status": "disabled", "detail": "LIVE_TRADING_CONFIRMED=false"}

    try:
        async with SpotDemoExchange(settings, live=True) as live:
            result = await live.ping()
        return {
            "status": "ok",
            "detail": f"live {result['symbol']} last={result['last']}",
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": format_binance_error(exc)}


def check_binance_demo_sync() -> dict[str, str]:
    return asyncio.run(check_binance_demo())
