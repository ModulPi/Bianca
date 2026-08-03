import pytest
from httpx import ASGITransport, AsyncClient

from agent.config import clear_settings_cache
from agent.exchange.futures_coin_demo import _coin_symbol
from agent.main import app
from agent.storage.database import close_db, init_db
from agent.trading.executor import resolve_trade_market


@pytest.fixture
async def client():
    clear_settings_cache()
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await close_db()
    clear_settings_cache()


def test_coin_symbol_mapping():
    assert _coin_symbol("BTCUSDT") == "BTC/USD:BTC"
    assert _coin_symbol("ETHUSDT") == "ETH/USD:ETH"


def test_resolve_futures_coin_market():
    from agent.config import Settings

    cfg = Settings(futures_enabled=True)
    assert resolve_trade_market({"market": "futures_coin"}, cfg) == "futures_coin"
    cfg_off = Settings(futures_enabled=False)
    assert resolve_trade_market({"market": "futures_coin"}, cfg_off) == "spot"


@pytest.mark.asyncio
async def test_futures_status_includes_coin(client):
    resp = await client.get("/api/v1/futures/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "futures_u" in body
    assert "futures_coin" in body


@pytest.mark.asyncio
async def test_live_mode_blocked_without_confirm(client):
    await client.post("/api/v1/validation/reset")
    resp = await client.post("/api/v1/trading/mode", json={"mode": "live"})
    assert resp.status_code == 403
    assert "LIVE_TRADING_CONFIRMED" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_health_includes_binance_live(client):
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "binance_live" in body
    assert "binance_demo_detail" in body
