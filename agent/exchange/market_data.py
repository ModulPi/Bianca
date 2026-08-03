from __future__ import annotations

from typing import Any, Literal

from agent.config import Settings, get_settings
from agent.exchange.futures_coin_demo import FuturesCoinDemoExchange, _coin_symbol
from agent.exchange.futures_demo import FuturesDemoExchange
from agent.exchange.spot_demo import SpotDemoExchange, resolve_market_symbol
from agent.llm.prompts import normalize_symbol
from agent.trading.executor import resolve_trade_market

StrategyMarket = Literal["spot", "futures_u", "futures_coin"]


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
        "symbol": normalize_symbol(ticker.get("symbol", raw_symbol)),
        "last": ticker.get("last"),
        "bid": ticker.get("bid"),
        "ask": ticker.get("ask"),
        "timestamp": ticker.get("timestamp"),
        "market": resolved,
        "balance": {"free": free},
    }
