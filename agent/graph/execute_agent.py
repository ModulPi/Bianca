from __future__ import annotations

from typing import Any

from agent.config import Settings, get_settings
from agent.exchange.spot_demo import SpotDemoExchange, resolve_market_symbol
from agent.graph.state import TradeState
from agent.positions.sync import sync_positions_from_exchange
from agent.storage.repository import AgentConfigRepository, TradeRepository


async def run_execute_agent(state: TradeState, *, settings: Settings | None = None) -> TradeState:
    cfg = settings or get_settings()
    from agent.trading.mode import get_trading_mode
    from agent.validation.paper_gate import assert_demo_mode_for_trading

    await assert_demo_mode_for_trading()
    if await get_trading_mode() == "live" and not cfg.futures_enabled:
        return {
            **state,
            "status": "skipped",
            "message": "live 模式已启用但合约 API 未对接，暂不下单",
        }
    signal = state.get("llm_signal") or {}
    market_data = state.get("market_data") or {}
    trade_id = state.get("trade_log_id")

    action = signal.get("action", "HOLD").upper()
    amount = signal.get("amount")
    symbol = signal.get("symbol") or cfg.trade_symbol

    if action not in {"BUY", "SELL"} or not amount:
        return {
            **state,
            "status": "skipped",
            "message": "无可执行信号",
        }

    side = action.lower()
    try:
        async with SpotDemoExchange(cfg) as demo:
            order = await _place_market_order(demo, side, float(amount), symbol, market_data)
    except Exception as exc:  # noqa: BLE001
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
    await sync_positions_from_exchange(settings=cfg)

    return {
        **state,
        "order_result": order,
        "status": "filled",
        "message": f"{action} 订单已提交: {external_id}",
    }


async def _place_market_order(
    demo: SpotDemoExchange,
    side: str,
    amount: float,
    symbol: str,
    market_data: dict[str, Any],
) -> dict[str, Any]:
    exchange = demo.exchange
    sym = resolve_market_symbol(exchange, symbol)
    if side == "buy":
        return await exchange.create_order(
            sym,
            "market",
            "buy",
            None,
            None,
            {"quoteOrderQty": amount},
        )
    return await exchange.create_order(sym, "market", "sell", amount)


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
