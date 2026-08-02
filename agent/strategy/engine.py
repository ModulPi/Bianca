from __future__ import annotations

import json
from typing import Any

from agent.config import Settings, get_settings
from agent.confirmation.service import confirm_pending_signal, queue_pending_signal
from agent.exchange.spot_demo import SpotDemoExchange
from agent.graph.execute_agent import run_execute_agent
from agent.graph.risk_agent import run_risk_agent
from agent.graph.state import TradeState
from agent.llm.prompts import normalize_symbol
from agent.storage.json_utils import parse_json_field
from agent.storage.repository import StrategyRepository
from agent.strategy.base import StrategyEvalResult, StrategySignal, StrategyType
from agent.strategy.dca import evaluate_dca
from agent.strategy.grid import evaluate_grid
from agent.strategy.trend import evaluate_trend

DEFAULT_PARAMS: dict[str, dict[str, Any]] = {
    "grid": {
        "lower_price": 60000.0,
        "upper_price": 70000.0,
        "grid_count": 10,
        "amount_per_grid": 10.0,
    },
    "dca": {"interval_minutes": 60, "buy_amount_usdt": 10.0},
    "trend": {"fast_period": 5, "slow_period": 20, "trade_amount_usdt": 10.0},
}


def default_params(strategy_type: StrategyType) -> dict[str, Any]:
    return dict(DEFAULT_PARAMS[strategy_type])


def evaluate_strategy(
    strategy_type: StrategyType,
    *,
    params: dict[str, Any],
    state: dict[str, Any],
    market_data: dict[str, Any],
    symbol: str,
) -> StrategyEvalResult:
    if strategy_type == "grid":
        return evaluate_grid(params=params, state=state, market_data=market_data, symbol=symbol)
    if strategy_type == "dca":
        return evaluate_dca(params=params, state=state, market_data=market_data, symbol=symbol)
    return evaluate_trend(params=params, state=state, market_data=market_data, symbol=symbol)


async def fetch_market(settings: Settings | None = None) -> dict[str, Any]:
    cfg = settings or get_settings()
    async with SpotDemoExchange(cfg) as demo:
        ticker = await demo.fetch_ticker(cfg.trade_symbol)
        balance = await demo.fetch_balance()
    free = {k: float(v) for k, v in balance.get("free", {}).items() if v}
    return {
        "symbol": normalize_symbol(ticker.get("symbol", cfg.trade_symbol)),
        "last": ticker.get("last"),
        "bid": ticker.get("bid"),
        "ask": ticker.get("ask"),
        "timestamp": ticker.get("timestamp"),
        "balance": {"free": free},
    }


async def execute_signal_pipeline(
    signal: StrategySignal,
    market_data: dict[str, Any],
    *,
    execution_mode: str,
    strategy_id: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    state: TradeState = {
        "llm_signal": signal.to_dict(),
        "market_data": market_data,
        "llm_auto_execute": True,
    }

    if execution_mode == "semi_auto":
        state = await queue_pending_signal(
            state, settings=cfg, session_id=strategy_id, strategy_id=strategy_id
        )
        return {"status": state.get("status"), "pending_signal_id": state.get("pending_signal_id")}

    state = await run_risk_agent(state, settings=cfg)
    risk = state.get("risk_decision") or {}
    if not risk.get("approved"):
        return {"status": "risk_rejected", "reason": risk.get("reason"), "state": state}

    state = await run_execute_agent(state, settings=cfg)
    return {"status": state.get("status"), "trade_log_id": state.get("trade_log_id"), "state": state}


async def run_strategy_tick(strategy_id: str, *, settings: Settings | None = None) -> dict[str, Any]:
    cfg = settings or get_settings()
    repo = StrategyRepository()
    row = await repo.get_by_id(strategy_id)
    if row is None:
        raise ValueError("策略不存在")
    if row.status != "running":
        raise ValueError(f"策略状态 {row.status}，非 running")

    params = parse_json_field(row.params_json)
    state = parse_json_field(row.state_json)
    market_data = await fetch_market(cfg)
    symbol = market_data.get("symbol") or cfg.trade_symbol

    result = evaluate_strategy(row.type, params=params, state=state, market_data=market_data, symbol=symbol)
    await repo.update_state(strategy_id, result.state)

    signal = result.signal
    if signal.action == "HOLD":
        return {"status": "hold", "signal": signal.to_dict(), "reason": signal.reason}

    exec_result = await execute_signal_pipeline(
        signal,
        market_data,
        execution_mode=row.execution_mode,
        strategy_id=strategy_id,
        settings=cfg,
    )
    return {
        "status": exec_result.get("status"),
        "signal": signal.to_dict(),
        "trade_log_id": exec_result.get("trade_log_id"),
        "pending_signal_id": exec_result.get("pending_signal_id"),
    }
