from __future__ import annotations

import logging
from typing import Any

from agent.config import Settings, get_settings
from agent.exchange.spot_demo import SpotDemoExchange
from agent.llm.prompts import base_asset_for_symbol
from agent.storage.constants import DEFAULT_AGENT_STRATEGY_ID
from agent.storage.database import schema_mode
from agent.storage.repository import PositionRepository

logger = logging.getLogger(__name__)


async def resolve_session_positions(
    settings: Settings | None = None,
) -> tuple[dict[str, Any], float | None]:
    """汇总快照用的持仓口径：MVP 栈读 positions 表，PoC 栈读交易所余额。"""
    cfg = settings or get_settings()
    base = base_asset_for_symbol(cfg.trade_symbol)

    if schema_mode() == "mvp":
        try:
            from agent.positions.sync import sync_positions_from_exchange

            if cfg.binance_configured:
                await sync_positions_from_exchange(settings=cfg)
        except Exception:  # noqa: BLE001
            logger.debug("positions sync skipped", exc_info=True)

        rows = await PositionRepository().list_by_strategy(DEFAULT_AGENT_STRATEGY_ID)
        if rows:
            base_free = usdt_free = 0.0
            mark: float | None = None
            for row in rows:
                if row.symbol == base:
                    base_free = float(row.quantity)
                    mark = float(row.current_price) if row.current_price else None
                elif row.symbol == "USDT":
                    usdt_free = float(row.quantity)
            return {"base_free": base_free, "usdt_free": usdt_free, "mark_price": mark}, mark

    if cfg.binance_configured:
        try:
            async with SpotDemoExchange(cfg) as demo:
                balance = await demo.fetch_balance()
                ticker = await demo.fetch_ticker(cfg.trade_symbol)
            free = balance.get("free") or {}
            mark = float(ticker.get("last") or 0) or None
            return {
                "base_free": float(free.get(base) or 0),
                "usdt_free": float(free.get("USDT") or 0),
                "mark_price": mark,
            }, mark
        except Exception:  # noqa: BLE001
            logger.debug("exchange balance fetch failed", exc_info=True)

    return {"base_free": 0.0, "usdt_free": 0.0, "mark_price": None}, None
