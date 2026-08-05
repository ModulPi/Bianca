from __future__ import annotations

import uuid
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from agent.checkpoint.saver import checkpointer_context
from agent.config import Settings, get_settings
from agent.degradation import get_effective_execution_mode
from agent.graph.analysis_agent import apply_analysis_to_state, run_analysis_agent
from agent.graph.execute_agent import run_execute_agent
from agent.graph.pending_agent import run_pending_agent
from agent.graph.risk_agent import run_risk_agent
from agent.graph.state import TradeState
from agent.markets.registry import get_market_adapter
from agent.storage.repository import TradeRepository


async def fetch_market_node(state: TradeState) -> TradeState:
    if state.get("market_data"):
        return state

    settings = get_settings()
    adapter = get_market_adapter(settings.trade_market, settings=settings)
    if not adapter.is_available():
        session = adapter.trading_session()
        raise RuntimeError(f"市场 {settings.trade_market} 不可用: {session.detail}")

    session = adapter.trading_session()
    if not session.is_open:
        return {**state, "status": "market_closed", "message": session.detail}

    symbol = str(state.get("symbol") or settings.trade_symbol)
    market_data = await adapter.fetch_snapshot(symbol, venue=settings.default_trade_market)
    if settings.trade_market == "crypto":
        from agent.market.klines import persist_recent_klines

        await persist_recent_klines(symbol, settings=settings)
    return {**state, "market_data": market_data, "symbol": symbol}


async def analysis_node(state: TradeState) -> TradeState:
    settings = get_settings()
    market_data = state.get("market_data") or {}
    result = await run_analysis_agent(market_data, settings=settings, persist=True)
    return apply_analysis_to_state(state, result)


async def log_signal_only_node(state: TradeState) -> TradeState:
    signal = state.get("llm_signal") or {}
    market_data = state.get("market_data") or {}
    trade_id = str(uuid.uuid4())
    reason = "HOLD" if signal.get("action") == "HOLD" else "LLM_AUTO_EXECUTE=false"

    repo = TradeRepository()
    await repo.save_signal(
        trade_id=trade_id,
        signal=signal,
        market_data=market_data,
        status="signal_only",
        risk_decision="skipped",
        risk_reason=reason,
        decision_id=state.get("decision_id"),
    )
    return {
        **state,
        "trade_log_id": trade_id,
        "status": "signal_only",
        "message": reason,
    }


def route_after_analysis(state: TradeState) -> Literal["risk", "queue_pending", "log_only"]:
    signal = state.get("llm_signal") or {}
    if signal.get("action") == "HOLD":
        return "log_only"
    mode = state.get("execution_mode") or get_settings().resolved_execution_mode
    if mode == "signal_only":
        return "log_only"
    if mode == "semi_auto":
        return "queue_pending"
    if not state.get("llm_auto_execute"):
        return "log_only"
    return "risk"


def route_after_risk(state: TradeState) -> Literal["execute"] | type(END):
    risk = state.get("risk_decision") or {}
    if risk.get("approved"):
        return "execute"
    return END


def build_trade_graph() -> StateGraph:
    graph = StateGraph(TradeState)
    graph.add_node("fetch_market", fetch_market_node)
    graph.add_node("analysis", analysis_node)
    graph.add_node("log_only", log_signal_only_node)
    graph.add_node("queue_pending", run_pending_agent)
    graph.add_node("risk", run_risk_agent)
    graph.add_node("execute", run_execute_agent)

    graph.add_edge(START, "fetch_market")
    graph.add_edge("fetch_market", "analysis")
    graph.add_conditional_edges(
        "analysis",
        route_after_analysis,
        {"risk": "risk", "queue_pending": "queue_pending", "log_only": "log_only"},
    )
    graph.add_edge("log_only", END)
    graph.add_edge("queue_pending", END)
    graph.add_conditional_edges("risk", route_after_risk, {"execute": "execute", END: END})
    graph.add_edge("execute", END)
    return graph


async def run_agent_tick(
    *,
    market_data: dict[str, Any] | None = None,
    thread_id: str = "default",
    session_id: str | None = None,
    symbol: str | None = None,
    settings: Settings | None = None,
) -> TradeState:
    """Run one full Supervisor → Analysis → Risk → Execute cycle."""
    cfg = settings or get_settings()
    sym = (symbol or cfg.trade_symbol).upper()
    effective_mode = await get_effective_execution_mode(cfg)

    initial: TradeState = {
        "llm_auto_execute": effective_mode != "signal_only",
        "execution_mode": effective_mode,
        "session_id": session_id,
        "symbol": sym,
    }
    if market_data:
        initial["market_data"] = market_data

    graph = build_trade_graph()
    async with checkpointer_context(settings=cfg) as checkpointer:
        app = graph.compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": thread_id}}
        result = await app.ainvoke(initial, config)
        return result
