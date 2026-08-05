import pytest

from agent.api.schemas import BalanceResponse, TickerResponse
from agent.dashboard.positions import (
    build_dashboard_positions,
    enrich_session_positions,
    quote_currency_for_market,
)


def test_build_dashboard_positions_crypto():
    balance = BalanceResponse(
        total={"USDT": 1000, "BTC": 0.01},
        free={"USDT": 900, "BTC": 0.01},
        used={"USDT": 100, "BTC": 0},
    )
    tickers = [TickerResponse(symbol="BTCUSDT", last=65000.0, bid=64999, ask=65001, timestamp=1)]
    rows = build_dashboard_positions(balance, tickers, ["BTCUSDT"], "crypto")
    assert len(rows) == 1
    assert rows[0].base == "BTC"
    assert rows[0].quote_currency == "USDT"
    assert rows[0].available is True
    assert rows[0].notional_usdt == pytest.approx(650.0)


def test_build_dashboard_positions_a_share_placeholder():
    rows = build_dashboard_positions(None, [], ["600519.SH"], "a_share")
    assert len(rows) == 1
    assert rows[0].market == "a_share"
    assert rows[0].quote_currency == "CNY"
    assert rows[0].available is False
    assert rows[0].free == 0


def test_enrich_session_positions_items():
    from agent.api.schemas import DashboardPositionItem

    items = [
        DashboardPositionItem(
            symbol="BTCUSDT",
            base="BTC",
            free=0.01,
            used=0,
            mark=65000,
            notional_usdt=650,
            market="crypto",
            quote_currency="USDT",
        ),
        DashboardPositionItem(
            symbol="ETHUSDT",
            base="ETH",
            free=0.5,
            used=0,
            mark=3000,
            notional_usdt=1500,
            market="crypto",
            quote_currency="USDT",
        ),
    ]
    base = {"base_asset": "BTC", "base_free": 0, "usdt_free": 0, "mark_price": None}
    enriched = enrich_session_positions(base, items=items, trade_market="crypto", cash_free=900)
    assert enriched["market"] == "crypto"
    assert enriched["cash_free"] == 900
    assert len(enriched["items"]) == 2
    assert enriched["base_asset"] == "BTC"


def test_quote_currency_for_market():
    assert quote_currency_for_market("us_stock") == "USD"
    assert quote_currency_for_market("a_share") == "CNY"
