from __future__ import annotations

import json
import logging
import uuid
from dataclasses import replace
from typing import Any

import ccxt

from agent.config import Settings, get_settings
from agent.exchange._client import format_binance_error
from agent.exchange.spot_demo import SpotDemoExchange
from agent.graph.state import TradeState
from agent.storage.models import OrderLog
from agent.storage.repository import OrderRepository, TradeRepository
from agent.trading.orders import (
    CANCELED,
    FILLED,
    PARTIAL,
    PENDING,
    REJECTED,
    UNKNOWN,
    OrderUpdate,
    classify_submit_error,
    is_terminal,
    new_client_order_id,
    normalize_order,
    reconcile,
)

logger = logging.getLogger(__name__)

# 订单状态 → 落库/上报用的状态词。`unknown` 单独成一类而不是并进 failed：
# 它意味着"这笔单的下落不明"，是唯一需要人介入的状态，混淆掉就等于丢掉告警。
_TRADE_STATUS = {
    FILLED: "filled",
    PARTIAL: "partial",
    UNKNOWN: "unknown",
    CANCELED: "failed",
    REJECTED: "failed",
}

# 对账参数。测试会改小它们以避开真实等待；在引入目标对象后应改为按目标配置。
_RECONCILE_ATTEMPTS = 5
_RECONCILE_DELAY_S = 1.0


async def run_execute_agent(state: TradeState, *, settings: Settings | None = None) -> TradeState:
    """下单并**对账**。

    与旧实现的区别有两条：

    ① 成交与否由交易所说了算。旧代码 `order.get("filled") or amount` 在交易所
       没给成交量时会拿请求量兜底，一笔 0 成交的单因此被记成 `filled`。现在拿不到
       可信回执就是 `unknown`。

    ② 提交前先问交易所认不认得这个幂等键。**交易所侧没有去重**：市价单成交后
       clientOrderId 立即释放，同一个 cid 再提交会被接受（实测，见
       `_find_existing_order`）。所以"不会重复下单"必须由我们自己证明。
    """
    cfg = settings or get_settings()
    signal = state.get("llm_signal") or {}
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
    # BUY 走 quoteOrderQty（USDT 名义），SELL 走基础币数量。口径随数值一起落库，
    # 否则事后分不清 signal.amount=25 是 25 USDT 还是 25 BTC。
    amount_kind = "quote" if side == "buy" else "base"
    amount_f = float(amount)

    orders = OrderRepository()
    # 幂等键由决策派生而非随机：重放时能认出"这就是上次那笔单"
    seed = trade_id or state.get("decision_id") or f"{symbol}:{side}:{uuid.uuid4()}"
    client_order_id = new_client_order_id(seed)

    replay = await orders.find_by_client_id(client_order_id)
    if replay is not None and is_terminal(replay.status):
        # 同一决策已经落定过：不在交易所留下第二笔单，把上次结果原样返回
        logger.info("execute 重放命中已有订单 %s status=%s", client_order_id, replay.status)
        return _result_from_order(state, replay, replayed=True)

    order_id = replay.id if replay is not None else uuid.uuid4().hex
    if replay is None:
        # 先写库再提交：崩在提交与落库之间时至少有这一行可查
        await orders.create_pending(
            order_id=order_id,
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            order_type="market",
            amount_kind=amount_kind,
            requested_amount=amount_f,
            trade_id=trade_id,
            decision_id=state.get("decision_id"),
        )

    # 一旦为真，后续任何异常都不再说"没下出去" —— 交易所已经收下了
    committed = False

    try:
        async with SpotDemoExchange(cfg) as demo:
            try:
                existing = await _find_existing_order(demo, symbol, client_order_id)
            except Exception as exc:  # noqa: BLE001 — 回查失败
                # 问不出来就不下。交易所侧没有去重，盲下的代价是重复建仓。
                # 记账但不落终态：这一轮不下单不等于这笔单作废，下一轮还要再试。
                detail = format_binance_error(exc)
                await orders.record_error(order_id, error=f"提交前回查失败，未下单：{detail}")
                return {
                    **state,
                    "order_result": {"error": detail, "status": PENDING},
                    "status": "failed",
                    "message": f"提交前回查失败，未下单：{detail}",
                }

            if existing is not None:
                # 交易所认得这个 cid —— 上次其实已经下出去了（可能崩在落库前，
                # 也可能本地账本丢过）。认领它，绝不再下一笔。
                logger.warning(
                    "cid %s 在交易所已存在（单号 %s），认领而非重下",
                    client_order_id,
                    existing.exchange_order_id,
                )
                committed = True
                await orders.mark_submitted(
                    order_id,
                    exchange_order_id=existing.exchange_order_id,
                    raw_response=None,
                )
                update = existing
            else:
                try:
                    raw = await _place_market_order(demo, side, amount_f, symbol, client_order_id)
                except Exception as exc:  # noqa: BLE001 — 已建连，提交失败
                    # 性质决定敢不敢重放：网络类异常下交易所可能已经收单
                    order_status = classify_submit_error(exc)
                    await _record_failure(
                        orders,
                        order_id,
                        trade_id,
                        order_status,
                        exc,
                        origin=state.get("order_origin", "normal"),
                    )
                    return _failure_state(state, exc, order_status=order_status)

                committed = True
                update = normalize_order(raw)
                await orders.mark_submitted(
                    order_id,
                    exchange_order_id=update.exchange_order_id,
                    raw_response=_dump(raw),
                )

            if not is_terminal(update.status):
                update = await _reconcile_or_unknown(demo, symbol, update)
    except Exception as exc:  # noqa: BLE001 — 建连/回查/收尾失败
        # 到这里有两种可能，处置相反：连交易所都没连上（没提交，可安全重试），
        # 或者提交之后的收尾失败（绝不能再试）。
        order_status = UNKNOWN if committed else REJECTED
        await _record_failure(
                        orders,
                        order_id,
                        trade_id,
                        order_status,
                        exc,
                        origin=state.get("order_origin", "normal"),
                    )
        return _failure_state(state, exc, order_status=order_status)

    row = await orders.apply_final(order_id, update)
    if row is None:  # pragma: no cover — 行只可能是被外部删掉
        return {**state, "status": "failed", "message": "订单记录丢失"}

    await _settle(
        trade_id,
        row,
        origin=state.get("order_origin", "normal"),
        origin_reason=state.get("order_origin_reason"),
    )
    # 日盈亏不再在这里记账。旧实现累加现金流（买入即记亏损），只有在卖出时
    # 才抵消回来，中间任何时刻的读数都不是盈亏。现在由权益快照口径统一计算：
    # 见 `agent/portfolio/pnl.py`。

    return _result_from_order(state, row)


async def _find_existing_order(
    demo: SpotDemoExchange,
    symbol: str,
    client_order_id: str,
) -> OrderUpdate | None:
    """问交易所：这个幂等键你见过吗？

    **交易所侧没有去重，实测过的**（2026-10-11，Demo）：

      · 前一笔**挂单中** → 同一个 cid 再提交被拒：`-2010 Duplicate order sent.`
      · 前一笔**已成交**（市价单）→ 同一个 cid 再提交被**接受**，真的开出第二笔
        （订单 72002941540 与 72002945169，同一个 cid）

    币安的 clientOrderId 唯一性只约束当前挂单。市价单瞬间成交、立刻离开挂单集合，
    id 当场释放 —— 而市价单正是我们唯一使用的类型。所以不能指望交易所替我们挡。

    返回 None 表示交易所不认得这个 cid，可以安全提交。查不到单是**正常**结果，
    不是错误；只有网络类失败才会抛出来，由调用方决定不下单。
    """
    try:
        raw = await demo.exchange.fetch_order(
            None, symbol, {"origClientOrderId": client_order_id}
        )
    except ccxt.OrderNotFound:
        return None
    return normalize_order(raw)


async def _reconcile_or_unknown(
    demo: SpotDemoExchange, symbol: str, update: OrderUpdate
) -> OrderUpdate:
    if update.exchange_order_id:
        return await reconcile(
            _fetcher(demo, symbol),
            exchange_order_id=update.exchange_order_id,
            attempts=_RECONCILE_ATTEMPTS,
            delay=_RECONCILE_DELAY_S,
        )
    # 回执里连单号都没有，问无可问
    return replace(update, status=UNKNOWN)


def _fetcher(demo: SpotDemoExchange, symbol: str):
    async def fetch(order_id: str) -> dict[str, Any]:
        return await demo.fetch_order(order_id, symbol)

    return fetch


async def _place_market_order(
    demo: SpotDemoExchange,
    side: str,
    amount: float,
    symbol: str,
    client_order_id: str,
) -> dict[str, Any]:
    exchange = demo.exchange
    params: dict[str, Any] = {"newClientOrderId": client_order_id}
    if side == "buy":
        # 市价买单用计价币名义下单，amount 传 None 由 quoteOrderQty 决定数量
        return await exchange.create_order(
            symbol, "market", "buy", None, None, {**params, "quoteOrderQty": amount}
        )
    return await exchange.create_order(symbol, "market", side, amount, None, params)


def _dump(raw: dict[str, Any]) -> str:
    return json.dumps(raw, ensure_ascii=False, default=str)


def _result_from_order(state: TradeState, order: OrderLog, *, replayed: bool = False) -> TradeState:
    status = _TRADE_STATUS.get(order.status, "failed")
    prefix = "重放已有订单" if replayed else "订单已提交"
    return {
        **state,
        "order_result": {
            "order_id": order.id,
            "client_order_id": order.client_order_id,
            "exchange_order_id": order.exchange_order_id,
            "status": order.status,
            "amount_kind": order.amount_kind,
            "requested_amount": order.requested_amount,
            "filled_qty": order.filled_qty,
            "avg_price": order.avg_price,
            "cost_quote": order.cost_quote,
            "replayed": replayed,
        },
        "status": status,
        "message": f"{prefix}: {order.exchange_order_id or order.id} ({order.status})",
    }


def _failure_state(state: TradeState, exc: Exception, *, order_status: str) -> TradeState:
    """上报失败时必须带上**订单状态**，不能一律说 failed。

    提交超时是 `unknown` —— 它和"交易所明确拒绝"在处置上完全相反：
    前者绝不能再下单，后者可以。压平成同一个词就是把这条区别抹掉。
    """
    detail = format_binance_error(exc)
    return {
        **state,
        "order_result": {"error": detail, "status": order_status},
        "status": _TRADE_STATUS.get(order_status, "failed"),
        "message": detail,
    }


async def _record_failure(
    orders: OrderRepository,
    order_id: str,
    trade_id: str | None,
    status: str,
    exc: Exception,
    *,
    origin: str = "normal",
) -> None:
    await orders.mark_failed(order_id, status=status, error=format_binance_error(exc))
    await _mark_trade(
        trade_id,
        status=_TRADE_STATUS.get(status, "failed"),
        reason=format_binance_error(exc),
        origin=origin,
    )


#: 由代码发起、绕过常规闸门的订单来源。`force_close` 是旧值（ADR-035 之前），
#: 库里已经有按它写下的行，所以要一直认。
_FORCED_ORIGINS = frozenset({"force_close", "risk_force_close"})
ORIGIN_GOAL_ACHIEVED = "goal_achieved"


def _risk_decision_for(origin: str) -> str:
    """来源 → `trade_logs.risk_decision`。**三件事不能压成一个词。**

    破线强平、达成清仓、常规风控放行，在"这笔单为什么会存在"上是三个答案。
    尤其达成清仓：它不是风控动作，记成 `force_close` 会让事后统计"多少次
    强制平仓"时把它算进去。
    """
    if origin == ORIGIN_GOAL_ACHIEVED:
        return ORIGIN_GOAL_ACHIEVED
    if origin in _FORCED_ORIGINS:
        return "force_close"
    return "approved"


def _is_forced(origin: str) -> bool:
    return origin in _FORCED_ORIGINS


async def _mark_trade(
    trade_id: str | None, *, status: str, reason: str, origin: str = "normal"
) -> None:
    if not trade_id:
        return
    await TradeRepository().update_status(
        trade_id,
        status=status,
        # 失败也一样：强制平仓失败不能被记成一次普通的"风控放行后失败"。
        # 旧实现只认 "force_close"，于是 D-1 引入的 `risk_force_close` 在失败
        # 分支上被静默记成了 approved —— 成功的行对、失败的行错，最难发现的那种。
        risk_decision=_risk_decision_for(origin),
        risk_reason=reason,
    )


async def _settle(
    trade_id: str | None,
    order: OrderLog,
    *,
    origin: str = "normal",
    origin_reason: str | None = None,
) -> None:
    """把对账结果写回决策记录。`unknown` 不写成失败原因，它自己就是一种结论。

    `origin` 决定这条记录怎么解释自己。强制平仓**不能**被记成"风控通过" ——
    它不是风控放行的一笔交易，是绕过常规闸门的一次保护动作（ADR-027），
    事后归因时要一眼分得出来。
    """
    if not trade_id:
        return
    # `force_close` 是旧值；ADR-035 起来源是 `risk_force_close`。两个都认，
    # 因为库里已经有按旧值写下的行 —— 只认新值会把它们读成"风控通过"。
    force = _is_forced(origin)
    if order.status == UNKNOWN:
        risk_reason = "订单下落不明：提交后未能取得可信回执"
    elif origin == ORIGIN_GOAL_ACHIEVED:
        risk_reason = f"目标达成，清空持仓（{origin_reason or 'ADR-038'}）"
    elif force:
        risk_reason = f"强制平仓（{origin_reason or '风控破线'}）"
    else:
        risk_reason = "风控通过"
    await TradeRepository().update_status(
        trade_id,
        status=_TRADE_STATUS.get(order.status, "failed"),
        risk_decision=_risk_decision_for(origin),
        risk_reason=risk_reason,
        quantity=order.filled_qty,
        price=order.avg_price,
        external_order_id=order.exchange_order_id,
        order_type="MARKET",
    )
