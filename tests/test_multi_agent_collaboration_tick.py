"""M9 Orchestrator 与协作 tick 集成测试（mock LLM/行情）。"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.config import Settings, clear_settings_cache
from backend.application.graph.orchestrator import build_orchestrator_plan
from backend.application.graph.state import TradeState
from backend.application.graph.supervisor import run_agent_tick
from backend.domain.llm.schemas import AnalysisResult, TradeSignal
from backend.infrastructure.storage.database import close_db, init_db


@pytest.fixture(autouse=True)
async def _db():
    clear_settings_cache()
    await init_db()
    yield
    await close_db()
    clear_settings_cache()


def test_orchestrator_respects_pause_directive():
    state: TradeState = {"symbol": "BTCUSDT"}
    directives = [{"action": "pause_symbol", "symbol": "BTCUSDT"}]
    plan = build_orchestrator_plan(state, chat_directives=directives)
    assert plan["skip_tick"] is True
    assert plan["use_analysis"] is False


def test_orchestrator_default_enables_both_agents():
    plan = build_orchestrator_plan({"symbol": "ETHUSDT"}, settings=Settings())
    assert plan["use_analysis"] is True
    assert plan["use_strategy"] is True


@pytest.mark.asyncio
async def test_collaboration_tick_merge_hold():
    hold = TradeSignal(action="HOLD", symbol="BTCUSDT", amount=None, confidence=0.5, reason="wait")
    mock_analysis = AnalysisResult(
        signal=hold,
        raw_output="{}",
        model_used="test",
        prompt_summary="t",
        auto_execute=False,
        decision_id="dec-m9",
        usage=None,
    )
    market = {
        "symbol": "BTCUSDT",
        "last": 65000.0,
        "balance": {"free": {"USDT": 100.0}},
        "klines_5m_closes": [64000.0, 64500.0, 65000.0, 65500.0, 66000.0] * 5,
    }

    with patch("backend.application.graph.supervisor.run_analysis_agent", AsyncMock(return_value=mock_analysis)):
        with patch(
            "backend.application.graph.strategy_agent.run_strategy_agent",
            AsyncMock(
                return_value={
                    "agent": "strategy",
                    "signal": {
                        "action": "HOLD",
                        "symbol": "BTCUSDT",
                        "confidence": 0.4,
                        "reason": "trend flat",
                    },
                }
            ),
        ):
            result = await run_agent_tick(market_data=market, thread_id="m9-hold")
    assert result.get("merge_meta") is not None
    assert result["llm_signal"]["action"] == "HOLD"
    assert len(result.get("agent_signals") or []) == 2


@pytest.mark.asyncio
async def test_collaboration_tick_ai_wins_conflict():
    buy = TradeSignal(action="BUY", symbol="BTCUSDT", amount=10.0, confidence=0.9, reason="ai buy")
    mock_analysis = AnalysisResult(
        signal=buy,
        raw_output="{}",
        model_used="test",
        prompt_summary="t",
        auto_execute=True,
        decision_id="dec-m9-buy",
        usage=None,
    )
    market = {
        "symbol": "BTCUSDT",
        "last": 65000.0,
        "balance": {"free": {"USDT": 1000.0}},
    }

    with patch("backend.application.graph.supervisor.run_analysis_agent", AsyncMock(return_value=mock_analysis)):
        with patch(
            "backend.application.graph.strategy_agent.run_strategy_agent",
            AsyncMock(
                return_value={
                    "agent": "strategy",
                    "signal": {
                        "action": "SELL",
                        "symbol": "BTCUSDT",
                        "amount": 0.001,
                        "confidence": 0.8,
                        "reason": "trend sell",
                    },
                }
            ),
        ):
            with patch("backend.application.graph.risk_agent.run_risk_agent", AsyncMock(return_value={"risk_decision": {"approved": False}, "status": "risk_rejected"})):
                result = await run_agent_tick(
                    market_data=market,
                    thread_id="m9-conflict",
                    settings=Settings(signal_merge_mode="llm_primary"),
                )
    assert result["llm_signal"]["action"] == "BUY"
    assert result["merge_meta"]["conflict"] is True
    assert result["merge_meta"]["winner"] == "analysis"
