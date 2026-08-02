import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from agent.main import app
from agent.storage.database import close_db, init_db


@pytest.fixture
async def client():
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await close_db()


@pytest.mark.asyncio
async def test_checkpoint_history_after_tick(client):
    thread_id = f"test-{uuid.uuid4().hex[:8]}"
    market = {
        "symbol": "BTCUSDT",
        "last": 65000.0,
        "balance": {"free": {"USDT": 1000.0, "BTC": 0.001}},
    }

    with patch("agent.graph.supervisor.run_analysis_agent", AsyncMock()) as mock_analysis:
        from agent.llm.schemas import AnalysisResult, TradeSignal

        mock_analysis.return_value = AnalysisResult(
            signal=TradeSignal(action="HOLD", symbol="BTCUSDT", confidence=0.5, reason="test"),
            model_used="mock",
            prompt_summary="s",
            auto_execute=False,
            decision_id=f"dec-{uuid.uuid4().hex[:8]}",
            raw_output="{}",
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )
        tick_resp = await client.post(
            "/api/v1/agent/tick",
            json={"thread_id": thread_id, "market_data": {"last": 65000.0}},
        )
        assert tick_resp.status_code == 200

    threads_resp = await client.get("/api/v1/checkpoints/threads")
    assert threads_resp.status_code == 200
    thread_ids = {item["thread_id"] for item in threads_resp.json()["items"]}
    assert thread_id in thread_ids

    history_resp = await client.get(f"/api/v1/checkpoints/threads/{thread_id}/history")
    assert history_resp.status_code == 200
    body = history_resp.json()
    assert body["thread_id"] == thread_id
    assert body["total"] >= 1
    assert body["items"][0]["state"].get("llm_signal") is not None


@pytest.mark.asyncio
async def test_checkpoint_thread_not_found(client):
    resp = await client.get("/api/v1/checkpoints/threads/does-not-exist/history")
    assert resp.status_code == 404
