from __future__ import annotations

from typing import Any

from agent.config import Settings, get_settings
from agent.storage.json_utils import parse_json_field
from agent.storage.repository import StrategyRepository
from agent.strategy.base import StrategySignal, StrategyType
from agent.strategy.engine import default_params, enrich_market_with_klines, evaluate_strategy


async def list_running_strategies(
    symbol: str | None = None,
    *,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    rows = await StrategyRepository().list_running()
    out: list[dict[str, Any]] = []
    for row in rows:
        params = parse_json_field(row.params_json)
        if symbol and params.get("symbol", row.name).upper() != symbol.upper():
            continue
        out.append(
            {
                "id": row.id,
                "name": row.name,
                "type": row.type,
                "params": params,
                "execution_mode": row.execution_mode,
            }
        )
    return out


async def evaluate_trend_for_symbol(
    symbol: str,
    market_data: dict[str, Any],
    *,
    params: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> StrategySignal:
    cfg = settings or get_settings()
    sym = symbol.upper()
    enriched = await enrich_market_with_klines(market_data, sym)
    p = params or default_params("trend")
    st = state or {}
    result = evaluate_strategy("trend", params=p, state=st, market_data=enriched, symbol=sym)
    return result.signal


async def evaluate_strategy_by_id(
    strategy_id: str,
    market_data: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> tuple[StrategySignal, StrategyType, dict[str, Any]]:
    row = await StrategyRepository().get_by_id(strategy_id)
    if row is None:
        raise ValueError("策略不存在")
    params = parse_json_field(row.params_json)
    state = parse_json_field(row.state_json)
    sym = market_data.get("symbol") or params.get("symbol") or row.name
    enriched = market_data
    if row.type == "trend":
        enriched = await enrich_market_with_klines(market_data, str(sym))
    result = evaluate_strategy(row.type, params=params, state=state, market_data=enriched, symbol=str(sym))
    return result.signal, row.type, result.state
