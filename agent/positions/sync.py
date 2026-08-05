from __future__ import annotations

import logging
from datetime import UTC, datetime

from agent.config import Settings, get_settings
from agent.exchange.spot_demo import SpotDemoExchange
from agent.llm.prompts import normalize_symbol
from agent.storage.constants import DEFAULT_AGENT_STRATEGY_ID
from agent.storage.database import schema_mode
from agent.storage.repository import PositionRepository

logger = logging.getLogger(__name__)

# 忽略 dust
_MIN_POSITION_QTY = 1e-8


def _parse_base_quote(symbol: str) -> tuple[str, str]:
    sym = normalize_symbol(symbol)
    if sym.endswith("USDT"):
        return sym[:-4], "USDT"
    if sym.endswith("USD"):
        return sym[:-3], "USD"
    return sym, "USDT"


async def sync_positions_from_balance(
    *,
    balance_free: dict[str, float],
    symbol: str | None = None,
    last_price: float | None = None,
    strategy_id: str = DEFAULT_AGENT_STRATEGY_ID,
    settings: Settings | None = None,
) -> int:
    """将交易所 free 余额同步到 positions 表（仅 MVP / PostgreSQL）。"""
    if schema_mode() != "mvp":
        return 0

    cfg = settings or get_settings()
    sym = normalize_symbol(symbol or cfg.trade_symbol)
    base, quote = _parse_base_quote(sym)
    repo = PositionRepository()
    now = datetime.now(UTC).isoformat()
    updated = 0

    targets = [
        (base, balance_free.get(base, 0.0), last_price if base != quote else 1.0),
        (quote, balance_free.get(quote, 0.0), 1.0),
    ]
    for asset, qty, mark in targets:
        if qty is None or float(qty) < _MIN_POSITION_QTY:
            continue
        price = float(mark) if mark else None
        await repo.upsert(
            strategy_id=strategy_id,
            symbol=asset,
            quantity=float(qty),
            current_price=price,
            market="spot",
            updated_at=now,
        )
        updated += 1

    if updated:
        logger.debug("positions synced strategy=%s symbol=%s count=%d", strategy_id, sym, updated)
    return updated


async def sync_positions_from_exchange(*, settings: Settings | None = None) -> int:
    cfg = settings or get_settings()
    if schema_mode() != "mvp" or not cfg.binance_configured:
        return 0

    async with SpotDemoExchange(cfg) as demo:
        balance = await demo.fetch_balance()
        ticker = await demo.fetch_ticker(cfg.trade_symbol)

    free = {k: float(v) for k, v in balance.get("free", {}).items() if v}
    return await sync_positions_from_balance(
        balance_free=free,
        symbol=cfg.trade_symbol,
        last_price=float(ticker.get("last") or 0) or None,
        settings=cfg,
    )
