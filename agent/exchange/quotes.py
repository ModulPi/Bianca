from __future__ import annotations

from agent.config import Settings
from agent.exchange._client import format_binance_error
from agent.exchange.spot_demo import SpotDemoExchange


async def fetch_exchange_balance(settings: Settings) -> dict:
    async with SpotDemoExchange(settings) as demo:
        return await demo.fetch_balance()


async def fetch_exchange_ticker(settings: Settings, symbol: str) -> dict:
    async with SpotDemoExchange(settings) as demo:
        return await demo.fetch_ticker(symbol)


async def fetch_exchange_tickers(settings: Settings, symbols: list[str]) -> list[dict]:
    if not symbols:
        symbols = [settings.trade_symbol]
    async with SpotDemoExchange(settings) as demo:
        return [await demo.fetch_ticker(sym) for sym in symbols]


def balance_to_response(balance: dict) -> dict:
    return {
        "total": {k: float(v) for k, v in balance.get("total", {}).items() if v},
        "free": {k: float(v) for k, v in balance.get("free", {}).items() if v},
        "used": {k: float(v) for k, v in balance.get("used", {}).items() if v},
    }


def ticker_to_response(ticker: dict) -> dict:
    return {
        "symbol": ticker.get("symbol"),
        "last": ticker.get("last"),
        "bid": ticker.get("bid"),
        "ask": ticker.get("ask"),
        "timestamp": ticker.get("timestamp"),
    }


def format_exchange_error(exc: Exception) -> str:
    return format_binance_error(exc)
