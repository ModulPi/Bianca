from __future__ import annotations

from typing import Any

from agent.strategy.base import StrategyEvalResult, StrategySignal


def evaluate_trend(
    *,
    params: dict[str, Any],
    state: dict[str, Any],
    market_data: dict[str, Any],
    symbol: str,
) -> StrategyEvalResult:
    fast = int(params.get("fast_period") or 5)
    slow = int(params.get("slow_period") or 20)
    amount = float(params.get("trade_amount_usdt") or 10.0)
    last = float(market_data.get("last") or 0)

    klines_closes = market_data.get("klines_5m_closes") or []
    if klines_closes:
        prices = [float(p) for p in klines_closes if p is not None]
        if last > 0 and (not prices or abs(prices[-1] - last) > 1e-9):
            prices.append(last)
    else:
        prices = list(state.get("prices") or [])
        if last > 0:
            prices.append(last)
    max_len = max(slow, fast) + 5
    prices = prices[-max_len:]
    new_state = {**state, "prices": prices}

    if len(prices) < slow:
        return StrategyEvalResult(
            signal=StrategySignal(
                "HOLD",
                symbol,
                None,
                0.3,
                f"趋势样本不足 {len(prices)}/{slow}",
            ),
            state=new_state,
        )

    fast_ma = sum(prices[-fast:]) / fast
    slow_ma = sum(prices[-slow:]) / slow
    prev_trend = state.get("trend_bias", "neutral")
    new_state["fast_ma"] = fast_ma
    new_state["slow_ma"] = slow_ma

    if fast_ma > slow_ma and prev_trend != "bull":
        new_state["trend_bias"] = "bull"
        return StrategyEvalResult(
            signal=StrategySignal(
                "BUY",
                symbol,
                amount,
                0.8,
                f"趋势金叉 fast={fast_ma:.2f} slow={slow_ma:.2f}",
            ),
            state=new_state,
        )

    if fast_ma < slow_ma and prev_trend != "bear":
        balance = market_data.get("balance") or {}
        free = balance.get("free") or {}
        from agent.llm.prompts import base_asset_for_symbol

        base = base_asset_for_symbol(symbol)
        base_qty = float(free.get(base) or 0)
        sell_qty = min(base_qty, amount / last) if last else 0
        new_state["trend_bias"] = "bear"
        if sell_qty <= 0:
            return StrategyEvalResult(
                signal=StrategySignal("HOLD", symbol, None, 0.0, "趋势死叉但无 Base 可卖"),
                state=new_state,
            )
        return StrategyEvalResult(
            signal=StrategySignal(
                "SELL",
                symbol,
                sell_qty,
                0.8,
                f"趋势死叉 fast={fast_ma:.2f} slow={slow_ma:.2f}",
            ),
            state=new_state,
        )

    return StrategyEvalResult(
        signal=StrategySignal(
            "HOLD",
            symbol,
            None,
            0.5,
            f"趋势延续 {prev_trend} fast={fast_ma:.2f} slow={slow_ma:.2f}",
        ),
        state=new_state,
    )
