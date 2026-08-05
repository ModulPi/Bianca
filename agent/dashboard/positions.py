from __future__ import annotations

from agent.api.schemas import BalanceResponse, DashboardPositionItem, TickerResponse
from agent.llm.prompts import base_asset_for_symbol
from agent.markets.registry import get_market_adapter

MARKET_QUOTE: dict[str, str] = {
    "crypto": "USDT",
    "a_share": "CNY",
    "us_stock": "USD",
}


def quote_currency_for_market(trade_market: str) -> str:
    return MARKET_QUOTE.get(trade_market, "USDT")


def cash_asset_for_market(trade_market: str) -> str:
    """结算货币在 balance.free 中的键名。"""
    if trade_market == "crypto":
        return "USDT"
    if trade_market == "a_share":
        return "CNY"
    if trade_market == "us_stock":
        return "USD"
    return "USDT"


def build_dashboard_positions(
    balance: BalanceResponse | None,
    tickers: list[TickerResponse],
    symbols: list[str],
    trade_market: str,
) -> list[DashboardPositionItem]:
    adapter = get_market_adapter(trade_market)
    quote = quote_currency_for_market(trade_market)
    available = adapter.is_available()
    ticker_map = {t.symbol: t for t in tickers if t.symbol}
    target_symbols = symbols or [t.symbol for t in tickers if t.symbol]

    if not available and trade_market != "crypto":
        return [
            DashboardPositionItem(
                symbol=symbol,
                base=base_asset_for_symbol(symbol),
                free=0.0,
                used=0.0,
                mark=None,
                notional_usdt=None,
                market=trade_market,
                quote_currency=quote,
                available=False,
            )
            for symbol in target_symbols
            if symbol
        ]

    if balance is None:
        return []

    rows: list[DashboardPositionItem] = []
    for symbol in target_symbols:
        if not symbol:
            continue
        base = base_asset_for_symbol(symbol)
        free = balance.free.get(base, 0.0)
        used = balance.used.get(base, 0.0)
        ticker = ticker_map.get(symbol)
        mark = ticker.last if ticker else None
        notional = free * mark if mark is not None else None
        rows.append(
            DashboardPositionItem(
                symbol=symbol,
                base=base,
                free=free,
                used=used,
                mark=mark,
                notional_usdt=notional,
                market=trade_market,
                quote_currency=quote,
                available=available,
            )
        )
    return rows


def enrich_session_positions(
    positions: dict,
    *,
    items: list[DashboardPositionItem],
    trade_market: str,
    cash_free: float,
) -> dict:
    """在 legacy 单 symbol 字段上附加多 symbol items 与市场元数据。"""
    quote = quote_currency_for_market(trade_market)
    primary = items[0] if items else None
    enriched = {
        **positions,
        "market": trade_market,
        "quote_currency": quote,
        "cash_free": cash_free,
        "items": [
            {
                "symbol": p.symbol,
                "base": p.base,
                "free": p.free,
                "used": p.used,
                "mark_price": p.mark,
                "notional_quote": p.notional_usdt,
                "market": p.market,
                "quote_currency": p.quote_currency,
                "available": p.available,
            }
            for p in items
        ],
    }
    if primary:
        enriched["base_asset"] = primary.base
        enriched["base_free"] = primary.free
        enriched["mark_price"] = primary.mark
    enriched["usdt_free"] = cash_free
    return enriched
