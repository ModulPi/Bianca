from __future__ import annotations

from backend.config import Settings, get_settings
from backend.domain.markets.a_share import AShareMarketAdapter
from backend.domain.markets.base import MarketAdapter, MarketKind
from backend.domain.markets.crypto import CryptoMarketAdapter
from backend.domain.markets.us_stock import USStockMarketAdapter

_ADAPTERS: dict[MarketKind, type] = {
    "crypto": CryptoMarketAdapter,
    "a_share": AShareMarketAdapter,
    "us_stock": USStockMarketAdapter,
}


def get_market_adapter(kind: MarketKind | None = None, *, settings: Settings | None = None) -> MarketAdapter:
    cfg = settings or get_settings()
    resolved: MarketKind = kind or cfg.trade_market
    adapter_cls = _ADAPTERS.get(resolved)
    if adapter_cls is None:
        raise ValueError(f"未知市场类型: {resolved}")
    return adapter_cls(cfg)


def list_market_kinds() -> list[MarketKind]:
    return list(_ADAPTERS.keys())
