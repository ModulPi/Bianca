from __future__ import annotations

from typing import Any

from agent.config import Settings, get_settings
from agent.graph.state import TradeState
from agent.metrics import record_trade
from agent.storage.repository import AgentConfigRepository, TradeRepository
from agent.trading.executor import execute_market_order, resolve_trade_market


async def run_execute_agent(state: TradeState, *, settings: Settings | None = None) -> TradeState:
    cfg = settings or get_settings()
    from agent.validation.paper_gate import assert_demo_mode_for_trading

    await assert_demo_mode_for_trading()
    signal = state.get("llm_signal") or {}
    market_data = state.get("market_data") or {}
    trade_id = state.get("trade_log_id")

    action = signal.get("action", "HOLD").upper()
    amount = signal.get("amount")
    symbol = signal.get("symbol") or cfg.trade_symbol
    market = resolve_trade_market(signal, cfg)

    if action not in {"BUY", "SELL"} or not amount:
        return {
            **state,
            "status": "skipped",
            "message": "无可执行信号",
        }

    side = action.lower()
    try:
        order = await execute_market_order(
            side=side,
            amount=float(amount),
            symbol=symbol,
            market_data=market_data,
            settings=cfg,
            market=market,
        )
    except Exception as exc:  # noqa: BLE001
        record_trade(side=action, status="failed", market=market)
        if trade_id:
            repo = TradeRepository()
            await repo.update_status(
                trade_id,
                status="failed",
                risk_decision="approved",
                risk_reason=str(exc),
            )
        return {
            **state,
            "order_result": {"error": str(exc)},
            "status": "failed",
            "message": str(exc),
        }

    filled_price = order.get("average") or order.get("price") or market_data.get("last")
    filled_qty = order.get("filled") or order.get("amount")
    external_id = str(order.get("id", ""))

    if trade_id:
        repo = TradeRepository()
        await repo.update_status(
            trade_id,
            status="filled",
            risk_decision="approved",
            risk_reason="风控通过",
            quantity=float(filled_qty) if filled_qty else None,
            price=float(filled_price) if filled_price else None,
            external_order_id=external_id or None,
            order_type="MARKET",
        )

    await _update_daily_pnl(cfg, action, float(amount), float(filled_price or 0))
    record_trade(side=action, status="filled", market=market)

    return {
        **state,
        "order_result": order,
        "status": "filled",
        "message": f"{action} 订单已提交 ({market}): {external_id}",
    }


async def _update_daily_pnl(
    settings: Settings,
    action: str,
    amount: float,
    price: float,
) -> None:
    """PoC 简化：BUY 扣减、SELL 增加 USDT 名义，用于日亏损熔断估算。"""
    repo = AgentConfigRepository()
    pnl = await repo.get_daily_pnl()
    if action == "BUY":
        pnl -= amount
    elif action == "SELL" and price:
        pnl += amount * price
    await repo.set_daily_pnl(pnl)
