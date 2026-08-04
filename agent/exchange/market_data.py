from __future__ import annotations

from typing import Any, Literal

from agent.config import Settings, get_settings
from agent.exchange.futures_coin_demo import FuturesCoinDemoExchange, _coin_symbol
from agent.exchange.futures_demo import FuturesDemoExchange
from agent.exchange.spot_demo import SpotDemoExchange, resolve_market_symbol
from agent.exchange.quotes import ticker_to_response
from agent.llm.prompts import normalize_symbol
from agent.trading.executor import resolve_trade_market

StrategyMarket = Literal["spot", "futures_u", "futures_coin"]


def _ticker_fields(ticker: dict[str, Any]) -> dict[str, Any]:
    parsed = ticker_to_response(ticker)
    return {
        "symbol": parsed.get("symbol"),
        "last": parsed.get("last"),
        "bid": parsed.get("bid"),
        "ask": parsed.get("ask"),
        "timestamp": parsed.get("timestamp"),
        "change_24h": parsed.get("change_24h"),
        "change_24h_pct": parsed.get("change_24h_pct"),
        "high_24h": parsed.get("high_24h"),
        "low_24h": parsed.get("low_24h"),
        "volume_24h": parsed.get("volume_24h"),
    }


def _futures_u_symbol(raw: str) -> str:
    compact = raw.upper().replace("/", "")
    if compact.endswith("USDT"):
        return f"{compact[:-4]}/USDT:USDT"
    return raw


async def fetch_market_snapshot(
    *,
    market: str = "spot",
    settings: Settings | None = None,
    symbol: str | None = None,
) -> dict[str, Any]:
    """按品种拉取 ticker + 余额，供策略评估与风控使用。"""
    cfg = settings or get_settings()
    raw_symbol = symbol or cfg.trade_symbol
    from agent.trading.mode import get_trading_mode

    if cfg.market_stream_enabled and cfg.trade_market == "crypto":
        from agent.market.ticker_cache import get_fresh_ticker

        cached = get_fresh_ticker(raw_symbol, cfg.market_stream_cache_ttl)
        if cached:
            live = await get_trading_mode() == "live"
            async with SpotDemoExchange(cfg, live=live) as spot:
                balance = await spot.fetch_balance()
            free = {k: float(v) for k, v in balance.get("free", {}).items() if v}
            return {
                **cached,
                "symbol": normalize_symbol(cached.get("symbol") or raw_symbol),
                "market": resolve_trade_market({"market": market}, cfg),
                "balance": {"free": free},
            }

    live = await get_trading_mode() == "live"
    resolved = resolve_trade_market({"market": market}, cfg)

    if resolved == "futures_u":
        async with FuturesDemoExchange(cfg, live=live) as ex:
            ccxt_sym = _futures_u_symbol(raw_symbol)
            ticker = await ex.exchange.fetch_ticker(ccxt_sym)
            balance = await ex.fetch_balance()
    elif resolved == "futures_coin":
        async with FuturesCoinDemoExchange(cfg, live=live) as ex:
            ccxt_sym = _coin_symbol(raw_symbol)
            ticker = await ex.exchange.fetch_ticker(ccxt_sym)
            balance = await ex.fetch_balance()
    else:
        async with SpotDemoExchange(cfg, live=live) as spot:
            ticker = await spot.fetch_ticker(raw_symbol)
            balance = await spot.fetch_balance()

    free = {k: float(v) for k, v in balance.get("free", {}).items() if v}
    return {
        **_ticker_fields(ticker),
        "symbol": normalize_symbol(ticker.get("symbol", raw_symbol)),
        "market": resolved,
        "balance": {"free": free},
    }
