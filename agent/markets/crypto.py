from __future__ import annotations

from typing import Any

from agent.config import Settings, get_settings
from agent.exchange.futures_coin_demo import FuturesCoinDemoExchange
from agent.exchange.futures_demo import FuturesDemoExchange
from agent.exchange.spot_demo import SpotDemoExchange
from agent.markets.base import MarketKind, TradingSession
from agent.trading.executor import execute_market_order, resolve_trade_market


class CryptoMarketAdapter:
    market_kind: MarketKind = "crypto"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def is_available(self) -> bool:
        return self._settings.binance_configured

    def trading_session(self) -> TradingSession:
        return TradingSession(is_open=True, detail="crypto 7x24")

    async def fetch_snapshot(self, symbol: str, *, venue: str = "spot") -> dict[str, Any]:
        from agent.exchange.market_data import fetch_market_snapshot

        return await fetch_market_snapshot(market=venue, settings=self._settings, symbol=symbol)

    async def execute_market_order(
        self,
        *,
        side: str,
        amount: float,
        symbol: str,
        market_data: dict[str, Any],
        venue: str = "spot",
    ) -> dict[str, Any]:
        resolved = resolve_trade_market({"market": venue}, self._settings)
        return await execute_market_order(
            side=side,
            amount=amount,
            symbol=symbol,
            market_data=market_data,
            settings=self._settings,
            market=resolved,
        )
