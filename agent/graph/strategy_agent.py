from __future__ import annotations

from typing import Any

from agent.config import Settings, get_settings
from agent.graph.state import TradeState
from agent.graph.strategy_tools import evaluate_trend_for_symbol, list_running_strategies


async def run_strategy_agent(
    market_data: dict[str, Any],
    *,
    symbol: str,
    strategy_type: str = "trend",
    strategy_ids: list[str] | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """评估趋势策略，返回 agent_signals 条目。"""
    cfg = settings or get_settings()
    sym = symbol.upper()

    if strategy_type != "trend":
        return {
            "agent": "strategy",
            "signal": {
                "action": "HOLD",
                "symbol": sym,
                "amount": None,
                "confidence": 0.0,
                "reason": f"M9 暂不支持策略类型 {strategy_type}",
            },
            "strategy_type": strategy_type,
        }

    running = await list_running_strategies(sym, settings=cfg)
    if strategy_ids:
        running = [r for r in running if r["id"] in strategy_ids]

    if running:
        from agent.graph.strategy_tools import evaluate_strategy_by_id

        best = running[0]
        signal, stype, _state = await evaluate_strategy_by_id(best["id"], market_data, settings=cfg)
        return {
            "agent": "strategy",
            "signal": signal.to_dict(),
            "strategy_id": best["id"],
            "strategy_type": stype,
            "strategy_name": best["name"],
        }

    signal = await evaluate_trend_for_symbol(sym, market_data, settings=cfg)
    return {
        "agent": "strategy",
        "signal": signal.to_dict(),
        "strategy_type": "trend",
        "strategy_name": f"trend-{sym.lower()}",
    }


async def strategy_node(state: TradeState) -> TradeState:
    plan = state.get("orchestrator_plan") or {}
    if not plan.get("use_strategy"):
        return state

    if plan.get("skip_tick"):
        return {**state, "status": "skipped", "message": plan.get("skip_reason", "chat pause")}

    market_data = state.get("market_data") or {}
    symbol = str(state.get("symbol") or market_data.get("symbol") or get_settings().trade_symbol)
    entry = await run_strategy_agent(
        market_data,
        symbol=symbol,
        strategy_type=str(plan.get("strategy_type") or "trend"),
        strategy_ids=plan.get("strategy_ids"),
        settings=get_settings(),
    )

    signals = list(state.get("agent_signals") or [])
    signals.append(entry)
    return {**state, "agent_signals": signals, "strategy_signal": entry.get("signal")}
