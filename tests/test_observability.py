import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from agent.main import app
from agent.storage.database import close_db, init_db
from agent.storage.repository import DecisionRepository, TradeRepository


@pytest.fixture
async def client():
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await close_db()


@pytest.mark.asyncio
async def test_decision_persists_tokens():
    await init_db()
    repo = DecisionRepository()
    decision_id = f"obs-dec-{uuid.uuid4().hex[:12]}"
    await repo.save(
        decision_id=decision_id,
        model_used="deepseek:test",
        prompt_summary="obs",
        raw_output="{}",
        parsed_signal={"action": "HOLD"},
        prompt_tokens=10,
        completion_tokens=20,
        total_tokens=30,
    )
    row = await repo.get_by_id(decision_id)
    assert row is not None
    assert row.prompt_tokens == 10
    assert row.completion_tokens == 20
    assert row.total_tokens == 30
    await close_db()


@pytest.mark.asyncio
async def test_usage_summary_delta():
    await init_db()
    repo = DecisionRepository()
    before = await repo.usage_summary()
    await repo.save(
        decision_id=f"obs-dec-{uuid.uuid4().hex[:12]}",
        model_used="deepseek:test",
        prompt_summary="obs",
        raw_output="{}",
        parsed_signal={"action": "HOLD"},
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
    )
    after = await repo.usage_summary()
    assert after["total"]["calls"] == before["total"]["calls"] + 1
    assert after["total"]["total_tokens"] == before["total"]["total_tokens"] + 150
    assert after["total"]["prompt_tokens"] == before["total"]["prompt_tokens"] + 100
    assert after["total"]["completion_tokens"] == before["total"]["completion_tokens"] + 50
    await close_db()


@pytest.mark.asyncio
async def test_trade_persists_decision_link_and_order_type():
    await init_db()
    repo = TradeRepository()
    trade_id = f"obs-trd-{uuid.uuid4().hex[:12]}"
    decision_id = f"obs-dec-{uuid.uuid4().hex[:12]}"
    await repo.save_signal(
        trade_id=trade_id,
        signal={"action": "BUY", "symbol": "OBSBTC", "confidence": 0.7, "reason": "obs"},
        market_data={"last": 63000.0},
        status="filled",
        risk_decision="approved",
        decision_id=decision_id,
        order_type="MARKET",
    )
    row = await repo.get_by_id(trade_id)
    assert row is not None
    assert row.decision_id == decision_id
    assert row.order_type == "MARKET"
    await close_db()


@pytest.mark.asyncio
async def test_usage_endpoint(client):
    resp = await client.get("/api/v1/usage")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"today", "total"}
    for bucket in body.values():
        assert set(bucket.keys()) == {"calls", "prompt_tokens", "completion_tokens", "total_tokens"}


@pytest.mark.asyncio
async def test_trades_filter_and_fields(client):
    repo = TradeRepository()
    sym = f"OBS{uuid.uuid4().hex[:8]}"
    decision_id = f"obs-dec-{uuid.uuid4().hex[:12]}"
    await repo.save_signal(
        trade_id=f"obs-trd-{uuid.uuid4().hex[:12]}",
        signal={"action": "BUY", "symbol": sym, "confidence": 0.6, "reason": "obs"},
        market_data={"last": 100.0},
        status="filled",
        risk_decision="approved",
        decision_id=decision_id,
        order_type="MARKET",
    )
    # 反向干扰项：同 symbol 但 SELL / signal_only，不应被过滤命中
    await repo.save_signal(
        trade_id=f"obs-trd-{uuid.uuid4().hex[:12]}",
        signal={"action": "SELL", "symbol": sym, "confidence": 0.6, "reason": "obs"},
        market_data={"last": 200.0},
        status="signal_only",
        risk_decision="skipped",
    )

    resp = await client.get(
        "/api/v1/trades", params={"symbol": sym, "side": "BUY", "status": "filled"}
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["symbol"] == sym
    assert item["side"] == "BUY"
    assert item["status"] == "filled"
    assert item["order_type"] == "MARKET"
    assert item["decision_id"] == decision_id


@pytest.mark.asyncio
async def test_decisions_returns_token_fields(client):
    resp = await client.get("/api/v1/decisions")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert all("prompt_tokens" in item for item in items)
    assert all("completion_tokens" in item for item in items)
    assert all("total_tokens" in item for item in items)
