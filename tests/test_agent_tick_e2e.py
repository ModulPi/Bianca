import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from agent.graph.supervisor import run_agent_tick
from agent.llm.schemas import AnalysisResult, TradeSignal
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
async def test_agent_tick_hold_signal_only():
    await init_db()
    hold = TradeSignal(
        action="HOLD", symbol="BTCUSDT", amount=None, confidence=0.5, reason="wait"
    )
    mock_result = AnalysisResult(
        signal=hold,
        raw_output="{}",
        model_used="test:mock",
        prompt_summary="test",
        auto_execute=False,
        decision_id="dec-e2e-hold",
        usage=None,
    )
    market = {
        "symbol": "BTCUSDT",
        "last": 65000.0,
        "balance": {"free": {"USDT": 1000.0, "BTC": 0.0}},
    }
    with patch("agent.graph.supervisor.run_analysis_agent", AsyncMock(return_value=mock_result)):
        result = await run_agent_tick(market_data=market, thread_id="e2e-hold")
    assert result["status"] == "signal_only"
    await close_db()


@pytest.mark.asyncio
async def test_agent_tick_buy_execute_path():
    await init_db()
    buy = TradeSignal(
        action="BUY", symbol="BTCUSDT", amount=10.0, confidence=0.9, reason="e2e buy"
    )
    mock_analysis = AnalysisResult(
        signal=buy,
        raw_output="{}",
        model_used="test:mock",
        prompt_summary="test",
        auto_execute=True,
        decision_id="dec-e2e-buy",
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    )
    mock_order = {
        "id": "ord-e2e-1",
        "filled": 0.00015,
        "average": 65000.0,
        "status": "closed",
    }
    market = {
        "symbol": "BTCUSDT",
        "last": 65000.0,
        "balance": {"free": {"USDT": 1000.0, "BTC": 0.0}},
    }

    with patch("agent.graph.supervisor.run_analysis_agent", AsyncMock(return_value=mock_analysis)):
        with patch(
            "agent.graph.execute_agent._place_market_order",
            AsyncMock(return_value=mock_order),
        ):
            result = await run_agent_tick(market_data=market, thread_id="e2e-buy")
    assert result["status"] == "filled"
    assert result.get("order_result") == mock_order
    await close_db()
