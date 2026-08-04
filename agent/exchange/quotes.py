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

    cached_map: dict[str, dict] = {}
    need_rest: list[str] = []

    if settings.market_stream_enabled:
        from agent.market.ticker_cache import get_fresh_ticker

        for sym in symbols:
            cached = get_fresh_ticker(sym, settings.market_stream_cache_ttl)
            if cached:
                cached_map[sym] = cached
            else:
                need_rest.append(sym)
    else:
        need_rest = list(symbols)

    if need_rest:
        async with SpotDemoExchange(settings) as demo:
            for sym in need_rest:
                cached_map[sym] = ticker_to_response(await demo.fetch_ticker(sym))

    return [cached_map[sym] for sym in symbols if sym in cached_map]


def balance_to_response(balance: dict) -> dict:
    return {
        "total": {k: float(v) for k, v in balance.get("total", {}).items() if v},
        "free": {k: float(v) for k, v in balance.get("free", {}).items() if v},
        "used": {k: float(v) for k, v in balance.get("used", {}).items() if v},
    }


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def ticker_to_response(ticker: dict) -> dict:
    volume = ticker.get("baseVolume")
    if volume is None:
        volume = ticker.get("quoteVolume")
    return {
        "symbol": ticker.get("symbol"),
        "last": _float_or_none(ticker.get("last")),
        "bid": _float_or_none(ticker.get("bid")),
        "ask": _float_or_none(ticker.get("ask")),
        "timestamp": ticker.get("timestamp"),
        "change_24h": _float_or_none(ticker.get("change")),
        "change_24h_pct": _float_or_none(ticker.get("percentage")),
        "high_24h": _float_or_none(ticker.get("high")),
        "low_24h": _float_or_none(ticker.get("low")),
        "volume_24h": _float_or_none(volume),
    }


def format_exchange_error(exc: Exception) -> str:
    return format_binance_error(exc)
