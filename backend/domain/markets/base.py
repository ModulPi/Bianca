from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

MarketKind = Literal["crypto", "a_share", "us_stock"]


@dataclass(frozen=True)
class TradingSession:
    """交易时段（A 股 / 美股后续实现）。"""

    is_open: bool
    detail: str = ""


class MarketAdapter(Protocol):
    market_kind: MarketKind

    async def fetch_snapshot(self, symbol: str) -> dict[str, Any]: ...

    async def execute_market_order(
        self,
        *,
        side: str,
        amount: float,
        symbol: str,
        market_data: dict[str, Any],
        venue: str = "spot",
    ) -> dict[str, Any]: ...

    def trading_session(self) -> TradingSession: ...

    def is_available(self) -> bool: ...
