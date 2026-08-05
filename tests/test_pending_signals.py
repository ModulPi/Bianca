import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock, patch

from agent.main import app
from agent.storage.database import close_db, init_db
from agent.storage.repository import PendingSignalRepository


@pytest.fixture
async def client():
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await close_db()


@pytest.mark.asyncio
async def test_pending_signal_confirm_flow(client):
    repo = PendingSignalRepository()
    row = await repo.create(
        signal={"action": "BUY", "symbol": "BTCUSDT", "amount": 10.0, "confidence": 0.9, "reason": "t"},
        market_data={"symbol": "BTCUSDT", "last": 65000.0, "balance": {"free": {"USDT": 1000.0, "BTC": 0}}},
        decision_id="dec-pending-1",
        session_id="sess-1",
        ttl_minutes=30,
    )

    mock_order = {"id": "ord-1", "filled": 0.00015, "average": 65000.0, "status": "closed"}
    mock_demo = AsyncMock()
    mock_demo.__aenter__ = AsyncMock(return_value=mock_demo)
    mock_demo.__aexit__ = AsyncMock(return_value=False)
    with patch("agent.graph.execute_agent.SpotDemoExchange", return_value=mock_demo):
        with patch("agent.graph.execute_agent._place_market_order", AsyncMock(return_value=mock_order)):
            resp = await client.post(f"/api/v1/pending-signals/{row.id}/confirm")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in {"filled", "risk_approved", "submitted"}

    updated = await repo.get_by_id(row.id)
    assert updated is not None
    assert updated.status == "confirmed"


@pytest.mark.asyncio
async def test_pending_signal_reject(client):
    repo = PendingSignalRepository()
    row = await repo.create(
        signal={"action": "SELL", "symbol": "BTCUSDT", "amount": 0.001, "confidence": 0.8, "reason": "t"},
        market_data={"symbol": "BTCUSDT", "last": 65000.0, "balance": {"free": {"USDT": 100, "BTC": 0.01}}},
        decision_id=None,
        session_id=None,
        ttl_minutes=30,
    )
    resp = await client.post(f"/api/v1/pending-signals/{row.id}/reject")
    assert resp.status_code == 200
    updated = await repo.get_by_id(row.id)
    assert updated is not None
    assert updated.status == "rejected"


@pytest.mark.asyncio
async def test_strategy_confirm_alias(client):
    from agent.storage.repository import StrategyRepository

    repo = StrategyRepository()
    strategy = await repo.create(
        name="半自动DCA",
        strategy_type="dca",
        execution_mode="semi_auto",
        params={"interval_minutes": 60, "buy_amount_usdt": 10},
    )
    sid = strategy.id
    pending_repo = PendingSignalRepository()
    await pending_repo.create(
        signal={"action": "BUY", "symbol": "BTCUSDT", "amount": 5.0, "confidence": 0.95, "reason": "alias"},
        market_data={"symbol": "BTCUSDT", "last": 65000.0, "balance": {"free": {"USDT": 500.0, "BTC": 0}}},
        decision_id=None,
        session_id=None,
        ttl_minutes=30,
        strategy_id=sid,
    )
    with patch("agent.graph.execute_agent._place_market_order", AsyncMock(return_value={"id": "x", "filled": 1, "average": 1})):
        resp = await client.post(f"/api/v1/strategies/{sid}/confirm")
    assert resp.status_code == 200
