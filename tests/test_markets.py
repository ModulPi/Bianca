import pytest

from agent.config import Settings, clear_settings_cache, set_effective_settings
from agent.markets.registry import get_market_adapter, list_market_kinds


def test_list_market_kinds():
    assert set(list_market_kinds()) == {"crypto", "a_share", "us_stock"}


def test_crypto_adapter_available_with_keys():
    clear_settings_cache()
    set_effective_settings(Settings(binance_api_key="k", binance_api_secret="s"))
    try:
        adapter = get_market_adapter("crypto")
        assert adapter.is_available()
        assert adapter.trading_session().is_open
    finally:
        clear_settings_cache()


@pytest.mark.asyncio
async def test_a_share_stub_not_available():
    adapter = get_market_adapter("a_share")
    assert not adapter.is_available()
    with pytest.raises(NotImplementedError):
        await adapter.fetch_snapshot("600519")


def test_us_stock_stub_not_available():
    adapter = get_market_adapter("us_stock")
    assert not adapter.is_available()
