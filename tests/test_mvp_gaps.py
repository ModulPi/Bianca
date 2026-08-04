import pytest
from httpx import ASGITransport, AsyncClient

from agent.config import clear_settings_cache
from agent.exchange.quotes import ticker_to_response
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


def test_ticker_to_response_includes_24h_fields():
    raw = {
        "symbol": "BTC/USDT",
        "last": 50000.0,
        "bid": 49999.0,
        "ask": 50001.0,
        "timestamp": 1_700_000_000_000,
        "change": 120.5,
        "percentage": 0.24,
        "high": 51000.0,
        "low": 49000.0,
        "baseVolume": 1234.56,
    }
    parsed = ticker_to_response(raw)
    assert parsed["change_24h"] == 120.5
    assert parsed["change_24h_pct"] == 0.24
    assert parsed["high_24h"] == 51000.0
    assert parsed["low_24h"] == 49000.0
    assert parsed["volume_24h"] == 1234.56


@pytest.mark.asyncio
async def test_trades_in_progress_alias(client):
    resp = await client.get("/api/v1/trades?status=in_progress&limit=5")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body


@pytest.mark.asyncio
async def test_dashboard_positions(client):
    resp = await client.get("/api/v1/dashboard/positions")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert "generated_at" in body


@pytest.mark.asyncio
async def test_analysis_reports_empty(client):
    resp = await client.get("/api/v1/analysis/reports?limit=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0
