from __future__ import annotations

import uuid
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
    goal_id: str | None = None,
    plan_id: str | None = None,
) -> AnalysisResult:
    """
    Analysis Agent node: market snapshot → structured BUY/SELL/HOLD signal.
    Persists to decision_logs when persist=True.
    """
    cfg = settings or get_settings()
    analyzer = MarketAnalyzer(cfg)
    run = await analyzer.analyze(market_data)
    signal = run.signal
    usage = run.usage
    auto_execute = should_auto_execute(signal, cfg)

    decision_id: str | None = None
    if persist:
        decision_id = str(uuid.uuid4())
        repo = DecisionRepository()
        # 完整 prompt 与完整行情上下文一并入库（ADR-019/ADR-031）。
        # `prompt_summary` 仍写，但它是给人扫一眼的，归因依据是下面这两份。
        # 行情上下文从 `run.messages` 里取不到 —— 那是给模型看过的文本；
        # 这里存的是**喂进模板之前**的结构化原文，两者都要有：
        # 前者说明"被怎么问的"，后者说明"原始数据长什么样"，出问题时能对账。
        await repo.save(
            decision_id=decision_id,
            model_used=f"{cfg.llm_provider}:{cfg.llm_model}",
            prompt_summary=run.prompt_summary,
            raw_output=run.raw_output or signal.reason,
            parsed_signal=signal.to_dict(),
            prompt_tokens=(usage or {}).get("prompt_tokens"),
            completion_tokens=(usage or {}).get("completion_tokens"),
            total_tokens=(usage or {}).get("total_tokens"),
            prompt=run.messages,
            prompt_variant=run.variant,
            market_data=market_data,
            # 链头：这一条判断是为哪个目标、哪个计划做的。阶段 C 之前恒为 None，
            # 但通道是通的 —— 否则届时列建好了却静默不写，比没建更难查。
            goal_id=goal_id,
            plan_id=plan_id,
        )

    return AnalysisResult(
        signal=signal,
        raw_output=run.raw_output,
        model_used=f"{cfg.llm_provider}:{cfg.llm_model}",
        prompt_summary=run.prompt_summary,
        auto_execute=auto_execute,
        decision_id=decision_id,
        usage=usage,
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
