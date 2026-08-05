from __future__ import annotations

from typing import Any

from agent.config import Settings, get_settings
from agent.exchange.futures_coin_demo import FuturesCoinDemoExchange
from agent.exchange.futures_demo import FuturesDemoExchange
from agent.exchange.spot_demo import SpotDemoExchange, resolve_market_symbol


def resolve_trade_market(signal: dict[str, Any], settings: Settings | None = None) -> str:
    cfg = settings or get_settings()
    market = str(signal.get("market") or cfg.default_trade_market)
    if market == "futures_u" and cfg.futures_enabled:
        return "futures_u"
    if market == "futures_coin" and cfg.futures_enabled:
        return "futures_coin"
    return "spot"


async def execute_market_order(
    *,
    side: str,
    amount: float,
    symbol: str,
    market_data: dict[str, Any],
    settings: Settings | None = None,
    market: str | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    from agent.trading.mode import get_trading_mode

    live = await get_trading_mode() == "live"
    resolved = market or "spot"

    if resolved == "futures_u" and cfg.futures_enabled:
        async with FuturesDemoExchange(cfg, live=live) as futures:
            return await futures.create_market_order(side, amount, symbol)
    if resolved == "futures_coin" and cfg.futures_enabled:
        async with FuturesCoinDemoExchange(cfg, live=live) as futures:
            return await futures.create_market_order(side, amount, symbol)

    async with SpotDemoExchange(cfg, live=live) as spot:
        return await _place_spot_market_order(spot, side, amount, symbol, market_data)


async def _place_spot_market_order(
    demo: SpotDemoExchange,
    side: str,
    amount: float,
    symbol: str,
    market_data: dict[str, Any],
) -> dict[str, Any]:
    exchange = demo.exchange
    sym = resolve_market_symbol(exchange, symbol)
    if side == "buy":
        return await exchange.create_order(
            sym,
            "market",
            "buy",
            None,
            None,
            {"quoteOrderQty": amount},
        )
    return await exchange.create_order(sym, "market", "sell", amount)
