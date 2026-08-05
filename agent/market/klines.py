from __future__ import annotations

import logging
from datetime import UTC, datetime

from agent.config import Settings, get_settings
from agent.exchange.spot_demo import SpotDemoExchange, resolve_market_symbol
from agent.storage.database import is_postgres_url
from agent.storage.repository import KlineRepository

logger = logging.getLogger(__name__)


def _candles_to_items(candles: list[tuple], *, symbol: str, interval: str) -> list[dict]:
    items: list[dict] = []
    for candle in candles:
        ts, o, h, l, c, v = candle[:6]
        items.append(
            {
                "time": datetime.fromtimestamp(ts / 1000, tz=UTC).isoformat(),
                "symbol": symbol,
                "interval": interval,
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": float(v or 0),
            }
        )
    return items


async def _fetch_ohlcv_from_exchange(
    symbol: str,
    interval: str,
    *,
    limit: int,
    settings: Settings,
) -> list[tuple]:
    from agent.trading.mode import get_trading_mode

    live = await get_trading_mode() == "live"
    async with SpotDemoExchange(settings, live=live) as demo:
        ccxt_sym = resolve_market_symbol(demo.exchange, symbol)
        return await demo.exchange.fetch_ohlcv(ccxt_sym, interval, limit=limit)


async def fetch_klines(
    symbol: str | None = None,
    *,
    interval: str | None = None,
    limit: int = 120,
    settings: Settings | None = None,
) -> tuple[list[dict], str]:
    """
    获取 K 线（时间升序）。
    优先读 PG 持久化；不足或 SQLite 时直连交易所。
    返回 (items, source)，source 为 db | live | empty。
    """
    cfg = settings or get_settings()
    sym = (symbol or cfg.trade_symbol).upper()
    iv = interval or cfg.klines_interval
    limit = max(10, min(limit, 500))

    repo = KlineRepository()
    db_rows = await repo.list_recent(symbol=sym, interval=iv, limit=limit)
    if len(db_rows) >= min(limit // 2, 30):
        return list(reversed(db_rows)), "db"

    if not cfg.binance_configured:
        return list(reversed(db_rows)), "db" if db_rows else "empty"

    try:
        candles = await _fetch_ohlcv_from_exchange(sym, iv, limit=limit, settings=cfg)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to fetch OHLCV for %s", sym)
        return list(reversed(db_rows)), "db" if db_rows else "empty"

    items = _candles_to_items(candles, symbol=sym, interval=iv)
    if cfg.klines_enabled and is_postgres_url(cfg.database_url) and candles:
        await repo.insert_candles(symbol=sym, interval=iv, candles=candles)
    return items, "live"


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

    sym = (symbol or cfg.trade_symbol).upper()
    try:
        candles = await _fetch_ohlcv_from_exchange(
            sym, cfg.klines_interval, limit=limit, settings=cfg
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to fetch OHLCV for klines persistence")
        return 0

    repo = KlineRepository()
    return await repo.insert_candles(symbol=sym, interval=cfg.klines_interval, candles=candles)
