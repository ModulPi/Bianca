import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock, patch

from agent.main import app
from agent.storage.database import close_db, init_db
from agent.storage.repository import StrategyRepository


@pytest.fixture
async def client():
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await close_db()


@pytest.mark.asyncio
async def test_create_and_list_strategies(client):
    resp = await client.post(
        "/api/v1/strategies",
        json={"name": "测试网格", "type": "grid", "execution_mode": "auto"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "grid"
    assert "lower_price" in body["params"]

    listing = await client.get("/api/v1/strategies")
    assert listing.status_code == 200
    assert listing.json()["total"] >= 1


@pytest.mark.asyncio
async def test_strategy_tick_hold(client):
    create = await client.post(
        "/api/v1/strategies",
        json={"name": "DCA", "type": "dca", "execution_mode": "auto"},
    )
    sid = create.json()["id"]
    await client.post(f"/api/v1/strategies/{sid}/start")

    repo = StrategyRepository()
    await repo.update(sid, status="running")

    market = {
        "symbol": "BTCUSDT",
        "last": 65000.0,
        "balance": {"free": {"USDT": 1000.0, "BTC": 0.0}},
    }
    with patch("agent.strategy.engine.fetch_market", AsyncMock(return_value=market)):
        tick = await client.post(f"/api/v1/strategies/{sid}/tick")
    assert tick.status_code == 200
    assert tick.json()["status"] in {"hold", "filled", "risk_rejected", "awaiting_confirmation", "risk_approved"}
