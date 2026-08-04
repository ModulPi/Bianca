import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock, patch

from agent.config import clear_settings_cache
from agent.storage.database import close_db, init_db
from agent.main import app


@pytest.fixture
async def client():
    clear_settings_cache()
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await close_db()
    clear_settings_cache()


@pytest.mark.asyncio
async def test_market_klines_requires_binance(client):
    resp = await client.get("/api/v1/market/klines?symbol=BTCUSDT&interval=1m&limit=30")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_market_klines_live_fetch(client):
    fake_candles = [
        (1_700_000_000_000, 100.0, 101.0, 99.0, 100.5, 12.0),
        (1_700_000_060_000, 100.5, 102.0, 100.0, 101.0, 8.0),
    ]

    with patch("agent.api.market_routes.get_settings") as mock_settings:
        settings = mock_settings.return_value
        settings.binance_configured = True
        settings.trade_symbol = "BTCUSDT"
        settings.klines_interval = "1m"
        settings.klines_enabled = False
        settings.database_url = "sqlite+aiosqlite:///./data/test.db"

        with patch(
            "agent.market.klines._fetch_ohlcv_from_exchange",
            new=AsyncMock(return_value=fake_candles),
        ):
            resp = await client.get("/api/v1/market/klines?symbol=BTCUSDT&interval=1m&limit=30")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert body["source"] == "live"
    assert body["symbol"] == "BTCUSDT"
    assert body["items"][0]["open"] == 100.0
    assert body["items"][1]["close"] == 101.0
