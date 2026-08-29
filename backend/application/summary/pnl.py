from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class PnLResult:
    cash_flow_usdt: float
    realized_usdt: float
    unrealized_usdt: float
    total_usdt: float


def compute_pnl(
    filled_trades: list[dict[str, Any]],
    *,
    base_free: float = 0.0,
    mark_price: float | None = None,
    daily_pnl_legacy: float = 0.0,
) -> PnLResult:
    """根据 filled 成交计算盈亏（加权平均成本法）。"""
    buy_cost = 0.0
    buy_qty = 0.0
    sell_revenue = 0.0
    sell_qty = 0.0

    for t in filled_trades:
        side = (t.get("side") or "").upper()
        qty = float(t.get("quantity") or 0)
        price = float(t.get("price") or 0)
        if qty <= 0 or price <= 0:
            continue
        if side == "BUY":
            buy_cost += qty * price
            buy_qty += qty
        elif side == "SELL":
            sell_revenue += qty * price
            sell_qty += qty

    cash_flow = sell_revenue - buy_cost
    avg_cost = (buy_cost / buy_qty) if buy_qty > 0 else 0.0
    realized = sell_qty * (sell_revenue / sell_qty - avg_cost) if sell_qty > 0 and sell_revenue > 0 else 0.0

    remaining_qty = max(buy_qty - sell_qty, base_free)
    remaining_cost = remaining_qty * avg_cost
    mark = mark_price or (filled_trades[-1].get("price") if filled_trades else 0) or 0
    unrealized = remaining_qty * float(mark) - remaining_cost if remaining_qty > 0 and mark else 0.0

    return PnLResult(
        cash_flow_usdt=round(cash_flow, 4),
        realized_usdt=round(realized, 4),
        unrealized_usdt=round(unrealized, 4),
        total_usdt=round(realized + unrealized, 4),
    )
