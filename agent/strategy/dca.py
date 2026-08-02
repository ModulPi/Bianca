from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agent.strategy.base import StrategyEvalResult, StrategySignal


def evaluate_dca(
    *,
    params: dict[str, Any],
    state: dict[str, Any],
    market_data: dict[str, Any],
    symbol: str,
) -> StrategyEvalResult:
    interval_min = int(params.get("interval_minutes") or 60)
    amount = float(params.get("buy_amount_usdt") or 10.0)
    now = datetime.now(UTC)
    last_buy = state.get("last_buy_at")

    if last_buy:
        try:
            prev = datetime.fromisoformat(last_buy)
            elapsed = (now - prev).total_seconds() / 60
            if elapsed < interval_min:
                return StrategyEvalResult(
                    signal=StrategySignal(
                        "HOLD",
                        symbol,
                        None,
                        0.5,
                        f"DCA 冷却中，还需 {interval_min - elapsed:.0f} 分钟",
                    ),
                    state=state,
                )
        except ValueError:
            pass

    balance = market_data.get("balance") or {}
    usdt = float((balance.get("free") or {}).get("USDT") or 0)
    if usdt + 0.01 < amount:
        return StrategyEvalResult(
            signal=StrategySignal("HOLD", symbol, None, 0.0, f"USDT 不足 DCA {amount}"),
            state=state,
        )

    new_state = {**state, "last_buy_at": now.isoformat()}
    return StrategyEvalResult(
        signal=StrategySignal(
            "BUY",
            symbol,
            amount,
            0.9,
            f"DCA 定时买入 {amount} USDT",
        ),
        state=new_state,
    )
