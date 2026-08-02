from __future__ import annotations

from typing import Any

from agent.strategy.base import StrategyEvalResult, StrategySignal


def evaluate_grid(
    *,
    params: dict[str, Any],
    state: dict[str, Any],
    market_data: dict[str, Any],
    symbol: str,
) -> StrategyEvalResult:
    last = float(market_data.get("last") or 0)
    lower = float(params.get("lower_price") or 0)
    upper = float(params.get("upper_price") or 0)
    grids = int(params.get("grid_count") or 5)
    amount = float(params.get("amount_per_grid") or 10.0)

    if last <= 0 or upper <= lower or grids < 2:
        return StrategyEvalResult(
            signal=StrategySignal("HOLD", symbol, None, 0.0, "网格参数无效"),
            state=state,
        )

    step = (upper - lower) / grids
    level = min(max(int((last - lower) / step), 0), grids - 1)
    prev = int(state.get("grid_level", level))
    new_state = {**state, "grid_level": level, "last_price": last}

    if level < prev:
        return StrategyEvalResult(
            signal=StrategySignal(
                "BUY",
                symbol,
                amount,
                0.85,
                f"网格下穿 level {prev}→{level}，价格 {last:.2f}",
            ),
            state=new_state,
        )
    if level > prev:
        balance = market_data.get("balance") or {}
        free = balance.get("free") or {}
        from agent.llm.prompts import base_asset_for_symbol

        base = base_asset_for_symbol(symbol)
        base_qty = float(free.get(base) or 0)
        sell_qty = min(base_qty, amount / last) if last else 0
        if sell_qty <= 0:
            return StrategyEvalResult(
                signal=StrategySignal("HOLD", symbol, None, 0.0, "网格上穿但无 Base 可卖"),
                state=new_state,
            )
        return StrategyEvalResult(
            signal=StrategySignal(
                "SELL",
                symbol,
                sell_qty,
                0.85,
                f"网格上穿 level {prev}→{level}，价格 {last:.2f}",
            ),
            state=new_state,
        )

    return StrategyEvalResult(
        signal=StrategySignal("HOLD", symbol, None, 0.5, f"网格 level {level} 无变化"),
        state=new_state,
    )
