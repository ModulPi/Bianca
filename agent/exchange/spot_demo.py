from __future__ import annotations

import asyncio
import logging
from typing import Any

import ccxt.async_support as ccxt

from agent.config import Settings, get_settings
from agent.exchange._client import build_binance_config, format_binance_error
from agent.exchange.indicators import summarize_candles

logger = logging.getLogger(__name__)


class SpotDemoExchange:
    """ccxt wrapper for Binance Demo spot trading."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._exchange: ccxt.binance | None = None

    def _build_exchange(self) -> ccxt.binance:
        exchange = ccxt.binance(build_binance_config(self._settings))
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

    async def fetch_market_context(self, symbol: str | None = None) -> dict[str, Any]:
        """拉取行情上下文：ticker(24h) + 1h K 线 + 技术指标，供 LLM 决策使用。

        OHLCV 失败时降级为仅 ticker（indicators 为空），不中断闭环。
        """
        sym = symbol or self._settings.trade_symbol
        ticker = await self.fetch_ticker(sym)

        context: dict[str, Any] = {
            "symbol": ticker.get("symbol", sym),
            "last": ticker.get("last"),
            "bid": ticker.get("bid"),
            "ask": ticker.get("ask"),
            "timestamp": ticker.get("timestamp"),
            "high_24h": ticker.get("high"),
            "low_24h": ticker.get("low"),
            "change_24h_pct": ticker.get("percentage"),
            "volume_24h_quote_usdt": ticker.get("quoteVolume"),
            "candles": [],
            "indicators": {},
        }

        candles_1h: list[list] = []
        candles_5m: list[list] = []
        try:
            candles_1h = await self.exchange.fetch_ohlcv(sym, "1h", 25)
        except Exception as exc:  # noqa: BLE001 — 降级处理
            logger.warning("fetch_ohlcv 1h failed, degraded to ticker-only: %s", exc)
        try:
            candles_5m = await self.exchange.fetch_ohlcv(sym, "5m", 13)
        except Exception as exc:  # noqa: BLE001
            logger.warning("fetch_ohlcv 5m failed: %s", exc)

        def _to_dict(raw: list) -> dict:
            ts, o, h, l, c, v = raw
            return {"t": ts, "o": o, "h": h, "l": l, "c": c, "v": v}

        if candles_1h:
            # 丢弃最新一根未完成 K 线
            completed = candles_1h[:-1] if len(candles_1h) > 1 else candles_1h
            context["candles"] = [_to_dict(c) for c in completed]

        indicators = summarize_candles(context["candles"])
        if len(candles_5m) >= 2:
            first_close = float(candles_5m[0][4])
            if first_close:
                indicators["momentum_5m_pct"] = round(
                    (float(candles_5m[-1][4]) - first_close) / first_close * 100.0, 4
                )
        context["indicators"] = indicators
        return context

    async def create_market_order(
        self,
        side: str,
        amount: float,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        sym = symbol or self._settings.trade_symbol
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
        async with SpotDemoExchange(settings) as demo:
            result = await demo.ping()
        return {
            "status": "ok",
            "detail": f"{result['symbol']} last={result['last']}",
        }
    except Exception as exc:  # noqa: BLE001 — health probe
        return {"status": "error", "detail": format_binance_error(exc)}


def check_binance_demo_sync() -> dict[str, str]:
    return asyncio.run(check_binance_demo())
