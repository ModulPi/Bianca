from __future__ import annotations

from typing import Any

from agent.markets.base import MarketKind, TradingSession


class AShareMarketAdapter:
    """A 股适配层占位 — 后续接入行情/下单源。"""

    market_kind: MarketKind = "a_share"

    def __init__(self, settings: object | None = None) -> None:
        pass

    def is_available(self) -> bool:
        return False

    def trading_session(self) -> TradingSession:
        return TradingSession(is_open=False, detail="A 股适配层尚未实现")

    async def fetch_snapshot(self, symbol: str) -> dict[str, Any]:
        raise NotImplementedError("A 股行情适配尚未实现（预留钩子）")

    async def execute_market_order(
        self,
        *,
        side: str,
        amount: float,
        symbol: str,
        market_data: dict[str, Any],
        venue: str = "spot",
    ) -> dict[str, Any]:
        raise NotImplementedError("A 股下单适配尚未实现（预留钩子）")
