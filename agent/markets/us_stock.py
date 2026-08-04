from __future__ import annotations

from typing import Any

from agent.markets.base import MarketKind, TradingSession


class USStockMarketAdapter:
    """美股适配层占位 — 后续接入行情/下单源。"""

    market_kind: MarketKind = "us_stock"

    def __init__(self, settings: object | None = None) -> None:
        pass

    def is_available(self) -> bool:
        return False

    def trading_session(self) -> TradingSession:
        return TradingSession(is_open=False, detail="美股适配层尚未实现")

    async def fetch_snapshot(self, symbol: str) -> dict[str, Any]:
        raise NotImplementedError("美股行情适配尚未实现（预留钩子）")

    async def execute_market_order(
        self,
        *,
        side: str,
        amount: float,
        symbol: str,
        market_data: dict[str, Any],
        venue: str = "spot",
    ) -> dict[str, Any]:
        raise NotImplementedError("美股下单适配尚未实现（预留钩子）")
