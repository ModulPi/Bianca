from unittest.mock import AsyncMock, patch

import pytest

from agent.config import Settings, clear_settings_cache
from agent.market.kline_collector import (
    KlineBar,
    collector_sleep_seconds,
    collect_klines_once,
    ohlcv_to_bars,
    resolve_kline_symbols,
    should_run_collector,
)


def test_ohlcv_to_bars_skips_incomplete_last():
    ohlcv = [
        [1_700_000_000_000, 100.0, 110.0, 90.0, 105.0, 12.5],
        [1_700_000_060_000, 105.0, 115.0, 95.0, 110.0, 8.0],
        [1_700_000_120_000, 110.0, 120.0, 100.0, 115.0, 5.0],
    ]
    bars = ohlcv_to_bars(ohlcv, symbol="BTCUSDT", interval="1m")
    assert len(bars) == 2
    assert bars[0].close == 105.0
    assert bars[1].close == 110.0
    assert bars[0].time.tzinfo is not None


def test_ohlcv_to_bars_keep_all_when_single():
    ohlcv = [[1_700_000_000_000, 1.0, 2.0, 0.5, 1.5, 3.0]]
    bars = ohlcv_to_bars(ohlcv, symbol="ETHUSDT", interval="5m", skip_last=True)
    assert len(bars) == 1
    assert bars[0].symbol == "ETHUSDT"
    assert bars[0].interval == "5m"


def test_resolve_kline_symbols_default_and_list():
    clear_settings_cache()
    cfg = Settings(trade_symbol="BTCUSDT", kline_symbols="")
    assert resolve_kline_symbols(cfg) == ["BTCUSDT"]
    cfg2 = Settings(trade_symbol="BTCUSDT", kline_symbols="btc/usdt, ETHUSDT")
    assert resolve_kline_symbols(cfg2) == ["BTCUSDT", "ETHUSDT"]


def test_collector_sleep_seconds():
    assert collector_sleep_seconds("1m") == 60
    assert collector_sleep_seconds("unknown") == 60


def test_should_run_collector_sqlite_disabled():
    clear_settings_cache()
    cfg = Settings(
        database_url="sqlite+aiosqlite:///./data/bianca.db",
        binance_api_key="k",
        binance_api_secret="s",
    )
    assert should_run_collector(cfg) is False


def test_should_run_collector_postgres_enabled():
    clear_settings_cache()
    cfg = Settings(
        database_url="postgresql+asyncpg://localhost/bianca",
        binance_api_key="k",
        binance_api_secret="s",
        kline_collector_enabled=True,
    )
    assert should_run_collector(cfg) is True


@pytest.mark.asyncio
async def test_collect_klines_once_sqlite_noop():
    clear_settings_cache()
    cfg = Settings(database_url="sqlite+aiosqlite:///./data/bianca.db")
    assert await collect_klines_once(settings=cfg) == 0


@pytest.mark.asyncio
async def test_collect_klines_once_inserts_bars():
    clear_settings_cache()
    cfg = Settings(
        database_url="postgresql+asyncpg://localhost/bianca",
        binance_api_key="k",
        binance_api_secret="s",
        trade_symbol="BTCUSDT",
        kline_interval="1m",
    )
    ohlcv = [
        [1_700_000_000_000, 100.0, 110.0, 90.0, 105.0, 12.5],
        [1_700_000_060_000, 105.0, 115.0, 95.0, 110.0, 8.0],
    ]
    mock_repo = AsyncMock()
    mock_repo.get_latest_time.return_value = None
    mock_repo.insert_bars.return_value = 1

    mock_demo = AsyncMock()
    mock_demo.fetch_ohlcv.return_value = ohlcv
    mock_demo.__aenter__ = AsyncMock(return_value=mock_demo)
    mock_demo.__aexit__ = AsyncMock(return_value=False)

    with patch("agent.market.kline_collector.KlineRepository", return_value=mock_repo):
        with patch("agent.market.kline_collector.SpotDemoExchange", return_value=mock_demo):
            inserted = await collect_klines_once(settings=cfg)

    assert inserted == 1
    mock_demo.fetch_ohlcv.assert_awaited_once()
    bars = mock_repo.insert_bars.await_args.args[0]
    assert len(bars) == 1
    assert isinstance(bars[0], KlineBar)
    assert bars[0].close == 105.0
