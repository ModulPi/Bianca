from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, Literal

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from agent.config import Settings, get_settings
from agent.exchange.spot_demo import SpotDemoExchange
from agent.graph.analysis_agent import apply_analysis_to_state, run_analysis_agent
from agent.graph.execute_agent import run_execute_agent
from agent.graph.risk_agent import run_risk_agent
from agent.graph.state import TradeState
from agent.risk.params import RiskParams
from agent.storage.repository import (
    EquityRepository,
    TradeRepository,
    decision_context,
)

logger = logging.getLogger(__name__)


async def fetch_market_node(state: TradeState) -> TradeState:
    if state.get("market_data"):
        return state

    settings = get_settings()
    if not settings.binance_configured:
        raise RuntimeError("market_data missing and Binance not configured")

    async with SpotDemoExchange(settings) as demo:
        market_data = await demo.fetch_market_context(settings.trade_symbol)

    return {**state, "market_data": market_data}


async def analysis_node(state: TradeState) -> TradeState:
    settings = get_settings()
    market_data = state.get("market_data") or {}
    result = await run_analysis_agent(
        market_data,
        settings=settings,
        persist=True,
        goal_id=state.get("goal_id"),
        plan_id=state.get("plan_id"),
    )
    return apply_analysis_to_state(state, result)


async def _latest_account() -> tuple[float | None, list[dict] | None]:
    """决策时刻的权益与持仓，取自本轮快照（tick 开头刚采集的那张）。

    **`None` 是"不知道"，`[]` 是"确实没有"。** 两者在归因里是两件事：前者说明
    记录不全、这个数不可用；后者说明账户当时真的是空的。没有快照时返回 `None`
    而不是 `[]` —— 写成空列表，事后读到的会是"当时没有任何持仓"，
    一个凭空捏造的结论，而且没有任何地方会报出它是编的。
    """
    row = await EquityRepository().latest()
    if row is None:
        return None, None
    try:
        return row.equity, json.loads(row.positions or "[]")
    except (TypeError, ValueError):
        logger.warning("权益快照 %s 的 positions 解析失败，归因记录里持仓记为未知", row.id)
        return row.equity, None


async def log_signal_only_node(state: TradeState) -> TradeState:
    signal = state.get("llm_signal") or {}
    market_data = state.get("market_data") or {}
    trade_id = str(uuid.uuid4())
    reason = "HOLD" if signal.get("action") == "HOLD" else "LLM_AUTO_EXECUTE=false"

    equity, positions = await _latest_account()
    repo = TradeRepository()
    await repo.save_signal(
        trade_id=trade_id,
        signal=signal,
        market_data=market_data,
        status="signal_only",
        risk_decision="skipped",
        risk_reason=reason,
        decision_id=state.get("decision_id"),
        # HOLD 也要答得出"当时看到了什么、手里有什么" —— R2/R3 不是只对成交的单生效。
        # 这些轮次正是日后要复盘"为什么没动手"的地方，恰恰最容易被漏记。
        context=decision_context(market_data, equity=equity, positions=positions),
        equity=equity,
        positions=positions,
        goal_id=state.get("goal_id"),
        plan_id=state.get("plan_id"),
    )
    return {
        **state,
        "trade_log_id": trade_id,
        "status": "signal_only",
        "message": reason,
    }


def route_after_analysis(state: TradeState) -> Literal["risk", "log_only"]:
    signal = state.get("llm_signal") or {}
    if signal.get("action") == "HOLD":
        return "log_only"
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
    graph.add_node("risk", run_risk_agent)
    graph.add_node("execute", run_execute_agent)

    graph.add_edge(START, "fetch_market")
    graph.add_edge("fetch_market", "analysis")
    graph.add_conditional_edges("analysis", route_after_analysis, {"risk": "risk", "log_only": "log_only"})
    graph.add_edge("log_only", END)
    graph.add_conditional_edges("risk", route_after_risk, {"execute": "execute", END: END})
    graph.add_edge("execute", END)
    return graph


async def _capture_equity_snapshot() -> None:
    """每轮 tick 先落一张权益快照。失败只记日志，不抛 —— 见 run_agent_tick。"""
    from agent.portfolio.service import capture

    try:
        await capture()
    except Exception as exc:  # noqa: BLE001 — 快照失败不该让整轮 tick 崩
        logger.warning("权益快照采集失败，风控将使用上一次的快照: %s", exc)


async def _force_close_if_needed(
    settings: Settings,
    *,
    params: RiskParams | None = None,
    goal_id: str | None = None,
    plan_id: str | None = None,
) -> list[dict[str, Any]]:
    """破线巡检 —— **每轮都跑，与 LLM 说了什么无关**（ADR-027）。

    这是本模块唯一会**自己发起交易**的地方。它必须在 LLM 之外：紧急出口如果依赖
    模型，就在最需要它的时刻最不可靠 —— 模型可能超时、可能回 HOLD、可能根本没跑。

    返回本轮实际尝试的平仓单摘要，便于可观测。
    """
    from agent.risk.close import force_close

    # 目标在跑时，这个开关也来自目标快照（ADR-021）—— 它同样是风控参数的一份。
    enabled = params.force_close_enabled if params is not None else settings.force_close_enabled
    if not enabled:
        logger.warning("force_close_enabled=false：即使破线也不会自动平仓，只剩拦开仓")
        return []

    try:
        return await force_close(
            settings=settings, params=params, goal_id=goal_id, plan_id=plan_id
        )
    except Exception as exc:  # noqa: BLE001 — 平仓这条路自己崩了，不能让整轮 tick 挂掉
        logger.error("强制平仓巡检失败: %s", exc, exc_info=True)
        return []


async def run_agent_tick(
    *,
    market_data: dict[str, Any] | None = None,
    thread_id: str | None = None,
    settings: Settings | None = None,
    goal_id: str | None = None,
    plan_id: str | None = None,
    params: RiskParams | None = None,
) -> TradeState:
    """Run one full Supervisor → Analysis → Risk → Execute cycle.

    thread_id 缺省时每轮生成唯一值：避免 LangGraph 检查点从上一轮 END 恢复，
    导致 market_data 被首次 tick 冻结、后续不再刷新行情。

    `goal_id` / `plan_id` 是归因链的链头（ADR-019 R1）。现在没有生产者（阶段 C），
    但入口先留着：届时 manager 规划完把 id 传进来即可，不必再改这一层。
    """
    cfg = settings or get_settings()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)

    # 先取一次权益快照再进图。两件事：① 当天第一条快照就是日亏熔断的基准；
    # ② 风控读的是权益口径的盈亏（含持仓浮亏），不刷新就看不见浮亏。
    # 取不到不能让整轮 tick 崩掉 —— 风控那边会拿到"未知"并据此拦住，这是安全的。
    await _capture_equity_snapshot()

    # 破线巡检排在进图**之前**：先保护，再考虑开新仓。反过来的话，
    # 同一轮里可能刚建仓就被平掉（图里读的是同一张快照，会批准那笔买入）。
    force_close_results = await _force_close_if_needed(
        cfg, params=params, goal_id=goal_id, plan_id=plan_id
    )

    checkpoint_path = cfg.data_dir / "checkpoints.sqlite"

    initial: TradeState = {
        "llm_auto_execute": cfg.llm_auto_execute,
        "goal_id": goal_id,
        "plan_id": plan_id,
        # 目标路径把确认时那份边界带进图里；旧的手动 tick 不带，风险节点按配置来。
        "risk_params": asdict(params) if params is not None else None,
    }
    if market_data:
        initial["market_data"] = market_data

    graph = build_trade_graph()
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        app = graph.compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": thread_id or datetime.now(UTC).isoformat()}}
        result = await app.ainvoke(initial, config)

    if force_close_results:
        # 平仓不是图里的节点，结果得单独带出去 —— 否则调用方会以为这一轮什么都没发生
        result = {**result, "force_close": force_close_results}
    return result
