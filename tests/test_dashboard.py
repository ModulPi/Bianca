import pytest
from httpx import ASGITransport, AsyncClient

from agent.config import clear_settings_cache
from agent.main import app
from agent.storage.database import close_db, init_db


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
async def test_dashboard_snapshot_shape(client):
    resp = await client.get("/api/v1/dashboard/snapshot")
    assert resp.status_code == 200
    body = resp.json()
    assert "agent" in body
    assert "trading_mode" in body
    assert "validation" in body
    assert "health" in body
    assert "usage" in body
    assert "positions" in body
    assert "tickers" in body
    assert "open_trades" in body
    assert "recent_filled" in body
    assert "pending_signals" in body
    assert "risk_events" in body
    assert "worker_token_usage" in body
    assert body["agent"]["running"] is False


@pytest.mark.asyncio
async def test_exchange_tickers_without_binance(client):
    resp = await client.get("/api/v1/exchange/tickers?symbols=BTCUSDT,ETHUSDT")
    assert resp.status_code == 503
