from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from agent.config import Settings, get_settings
from agent.graph.state import TradeState
from agent.llm.analyzer import MarketAnalyzer
from agent.llm.schemas import AnalysisResult, TradeSignal
from agent.storage.repository import DecisionRepository


def should_auto_execute(signal: TradeSignal, settings: Settings | None = None) -> bool:
    """P2.5 — only BUY/SELL proceed when LLM_AUTO_EXECUTE is enabled."""
    cfg = settings or get_settings()
    return cfg.llm_auto_execute and signal.is_actionable


async def run_analysis_agent(
    market_data: dict[str, Any],
    *,
    settings: Settings | None = None,
    persist: bool = True,
) -> AnalysisResult:
    """
    Analysis Agent node: market snapshot → structured BUY/SELL/HOLD signal.
    Persists to decision_logs when persist=True.
    """
    cfg = settings or get_settings()
    analyzer = MarketAnalyzer(cfg)
    signal, raw_output, prompt_summary = await analyzer.analyze(market_data)
    auto_execute = should_auto_execute(signal, cfg)

    decision_id: str | None = None
    if persist:
        decision_id = str(uuid.uuid4())
        repo = DecisionRepository()
        await repo.save(
            decision_id=decision_id,
            model_used=f"{cfg.llm_provider}:{cfg.llm_model}",
            prompt_summary=prompt_summary,
            raw_output=raw_output or signal.reason,
            parsed_signal=signal.to_dict(),
        )

    return AnalysisResult(
        signal=signal,
        raw_output=raw_output,
        model_used=f"{cfg.llm_provider}:{cfg.llm_model}",
        prompt_summary=prompt_summary,
        auto_execute=auto_execute,
        decision_id=decision_id,
    )


def apply_analysis_to_state(state: TradeState, result: AnalysisResult) -> TradeState:
    """Merge analysis output into LangGraph state (used by P3 supervisor)."""
    return {
        **state,
        "llm_signal": result.signal.to_dict(),
        "llm_auto_execute": result.auto_execute,
        "analysis_result": result.model_dump(mode="json"),
        "decision_id": result.decision_id,
    }
