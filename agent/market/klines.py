from __future__ import annotations

import logging

from agent.config import Settings, get_settings
from agent.exchange.spot_demo import SpotDemoExchange
from agent.storage.database import is_postgres_url
from agent.storage.repository import KlineRepository

logger = logging.getLogger(__name__)


async def persist_recent_klines(
    symbol: str | None = None,
    *,
    settings: Settings | None = None,
    limit: int = 5,
) -> int:
    cfg = settings or get_settings()
    if not cfg.klines_enabled or not is_postgres_url(cfg.database_url):
        return 0
    if not cfg.binance_configured:
        return 0

    sym = symbol or cfg.trade_symbol
    try:
        from agent.trading.mode import get_trading_mode

        live = await get_trading_mode() == "live"
        async with SpotDemoExchange(cfg, live=live) as demo:
            ccxt_sym = sym
            if sym in demo.exchange.markets:
                ccxt_sym = sym
            else:
                from agent.exchange.spot_demo import resolve_market_symbol

                ccxt_sym = resolve_market_symbol(demo.exchange, sym)
            candles = await demo.exchange.fetch_ohlcv(ccxt_sym, cfg.klines_interval, limit=limit)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to fetch OHLCV for klines persistence")
        return 0

    repo = KlineRepository()
    return await repo.insert_candles(symbol=sym, interval=cfg.klines_interval, candles=candles)
