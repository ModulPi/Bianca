"""经理能用的只读工具集（阶段 B-2）。

**这些工具全部只读本地已落库的数据，不主动出网、不写任何表。** 理由有两条：

1. 阶段 B 的验收是「不产生任何订单」。一个会去交易所下单的工具不该出现在这里，
   而一个**只读但会出网**的工具同样会让「跑一次计划」变成不可离线复现的事。
2. 出网的那部分（拉余额、拉实盘价）是 `portfolio.service.capture()` 的活，
   由采集侧按自己的节奏跑。经理读它的**产物**，不替它决定什么时候跑。

**「不知道」要原样写出来。** 快照没采过、行情库里没有这个 symbol —— 这些一律返回
`{"available": false, "reason": …}`，**绝不用 0 或空列表冒充**。这是仓库级原则
「交易所没说的，我们不编」在经理层的落地：模型拿到 0 会理所当然地当成「账户是空的」，
而这和「我们不知道」是两件完全不同的事。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from agent.config import Settings, get_settings
from agent.exchange.indicators import rsi, sma
from agent.manager.tools import ToolRegistry, ToolSpec
from agent.market.collector import symbol_flow_verdict
from agent.market.repository import KlineRepository
from agent.portfolio.pnl import drawdown
from agent.risk.rules import drawdown_breached
from agent.storage.repository import (
    DecisionRepository,
    EquityRepository,
    RiskEventRepository,
    TradeRepository,
)

_DEFAULT_BARS = 60
_MAX_BARS = 500
_DEFAULT_ROWS = 20
_MAX_ROWS = 100


def _unknown(reason: str, **extra: Any) -> dict[str, Any]:
    """不知道就写不知道。**不要返回 0、不要返回空列表。**"""
    return {"available": False, "reason": reason, **extra}


def _ts(ms: Any) -> str | None:
    if not isinstance(ms, (int, float)):
        return None
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def _loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def _clamp(value: Any, *, low: int, high: int, fallback: int) -> int:
    try:
        return max(low, min(int(value), high))
    except (TypeError, ValueError):
        return fallback


# --- 各工具的实现 -----------------------------------------------------------


def _snapshot_age_seconds(captured_at: Any) -> float | None:
    """快照距今多少秒。算不出来就返回 None —— **不编一个 0**。"""
    if not isinstance(captured_at, str) or not captured_at:
        return None
    try:
        at = datetime.fromisoformat(captured_at)
    except ValueError:
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return (datetime.now(UTC) - at).total_seconds()


async def _get_portfolio(args: dict[str, Any], settings: Settings) -> dict[str, Any]:
    row = await EquityRepository().latest()
    if row is None:
        return _unknown("还没有任何权益快照：采集侧还没跑过，此刻账户里有什么是未知的")
    gaps = _loads(row.gaps, [])
    unexplained = [g for g in gaps if abs(float(g.get("unexplained_qty") or 0.0)) > 0]
    # 快照表的节奏不由 `_stale_after_s` 管，所以这里**只报事实、不编阈值**（§6.4.2）。
    age = _snapshot_age_seconds(row.captured_at)
    return {
        "available": True,
        "captured_at": row.captured_at,
        "age_seconds": age,
        "age_note": (
            None
            if age is None
            else f"这是 {age / 3600:.1f} 小时前的账户，不是此刻的 —— "
            "期间可能已经成交、强平或分红"
        ),
        "equity": row.equity,
        "cash_quote": row.cash_quote,
        "holdings_value": row.holdings_value,
        "positions": _loads(row.positions, []),
        "unpriced": _loads(row.unpriced, []),
        "unexplained_positions": unexplained,
        "notes": _loads(row.notes, []),
        "source": "本地权益快照表（由采集侧写）",
    }


async def _get_market(args: dict[str, Any], settings: Settings) -> dict[str, Any]:
    symbol = str(args.get("symbol") or settings.trade_symbol).upper()
    limit = _clamp(args.get("bars"), low=2, high=_MAX_BARS, fallback=_DEFAULT_BARS)
    interval = settings.market_interval
    bars = await KlineRepository().recent_closes(symbol, interval, limit=limit)
    if not bars:
        return _unknown(
            f"本地行情库里没有 {symbol} {interval} 的 K 线：采集侧可能没在跑",
            symbol=symbol,
            interval=interval,
        )
    closes = [float(b["c"]) for b in bars]
    highs = [float(b["h"]) for b in bars]
    lows = [float(b["l"]) for b in bars]
    first, last = closes[0], closes[-1]

    # 这数字有多旧 —— **判据复用采集侧那一份**，不在这里另立阈值（§6.4.2）。
    # 少了这一段，16 天前的 K 线会被原样当成现价交出去。
    data_status, lag_s, data_detail = await symbol_flow_verdict(symbol, interval, settings=settings)

    return {
        "available": True,
        "symbol": symbol,
        "interval": interval,
        "bars": len(bars),
        "first_bar": _ts(bars[0].get("t")),
        "last_bar": _ts(bars[-1].get("t")),
        "last_price": last,
        "high": max(highs),
        "low": min(lows),
        "change_pct": ((last - first) / first) if first else None,
        "sma20": sma(closes, 20),
        "rsi14": rsi(closes),
        "data_status": data_status,
        "data_status_detail": data_detail,
        "data_lag_seconds": lag_s,
        "stale": data_status != "ok",
        "note": (
            f"**这份行情已经停更 {lag_s} 秒**（{data_detail}）。"
            "last_price / change_pct / sma20 / rsi14 说的都是那一刻，不是此刻 —— "
            "据此做计划前先说明你要拿一个陈数据怎么办。"
            if data_status != "ok"
            else "行情是新鲜的"
        ),
        "source": "本地行情库（实盘采集，见 ADR-013）",
    }


async def _get_risk_limits(args: dict[str, Any], settings: Settings) -> dict[str, Any]:
    return {
        "available": True,
        "max_trade_amount": settings.max_trade_amount,
        "daily_loss_limit": settings.daily_loss_limit,
        "stop_loss_pct": settings.stop_loss_pct,
        "stop_lookback_hours": settings.stop_lookback_hours,
        "max_drawdown_pct": settings.max_drawdown_pct,
        "max_position_pct": settings.max_position_pct,
        "force_close_enabled": settings.force_close_enabled,
        "trade_symbol": settings.trade_symbol,
        "note": "这些是当前生效的风控参数，执行期间只读（ADR-021）",
    }


async def _get_drawdown(args: dict[str, Any], settings: Settings) -> dict[str, Any]:
    # **复用 `pnl.drawdown()`，不在这里重算一遍。** 自己算看起来更简单，但会漏掉
    # ADR-029/030 的重置点：重置之后峰值只从那一刻往后看，照搬"全历史最高"会算出
    # 一个虚高的回撤，和风控引擎报的数对不上 —— 同一个概念两个口径，是最坏的一种
    # 分歧（经理据此定计划，风控据此开闸）。
    state = await drawdown()
    if not state.known:
        return _unknown(state.reason or "算不出回撤", peak_equity=state.peak_equity)
    return {
        "available": True,
        "peak_equity": state.peak_equity,
        "current_equity": state.current_equity,
        "drawdown_pct": state.pct,
        "limit_pct": settings.max_drawdown_pct,
        "breached": drawdown_breached(state.pct, settings.max_drawdown_pct),
        "peak_since": state.since,
        "note": (
            "峰值取全历史，只能显式重置（ADR-029）"
            if state.since is None
            else f"峰值只从上次重置点 {state.since} 往后看（ADR-030）"
        ),
    }


async def _get_recent_decisions(args: dict[str, Any], settings: Settings) -> dict[str, Any]:
    limit = _clamp(args.get("limit"), low=1, high=_MAX_ROWS, fallback=_DEFAULT_ROWS)
    rows = await DecisionRepository().list_recent(limit)
    if not rows:
        return _unknown("还没有任何决策记录")
    items = []
    for r in rows:
        signal = _loads(r.parsed_signal, {})
        items.append(
            {
                "at": r.created_at,
                "model": r.model_used,
                "action": signal.get("action"),
                "symbol": signal.get("symbol"),
                "confidence": signal.get("confidence"),
                "reason": signal.get("reason"),
                "goal_id": r.goal_id,
                "plan_id": r.plan_id,
            }
        )
    return {"available": True, "count": len(items), "decisions": items}


async def _get_recent_trades(args: dict[str, Any], settings: Settings) -> dict[str, Any]:
    limit = _clamp(args.get("limit"), low=1, high=_MAX_ROWS, fallback=_DEFAULT_ROWS)
    rows = await TradeRepository().list_recent(limit)
    if not rows:
        return _unknown("还没有任何交易记录")
    items = [
        {
            "at": r.created_at,
            "symbol": r.symbol,
            "side": r.side,
            "quantity": r.quantity,
            "price": r.price,
            "status": r.status,
            "risk_decision": r.risk_decision,
            "risk_reason": r.risk_reason,
            "reason": r.decision_reason,
            "goal_id": r.goal_id,
            "plan_id": r.plan_id,
        }
        for r in rows
    ]
    return {"available": True, "count": len(items), "trades": items}


async def _get_recent_risk_events(args: dict[str, Any], settings: Settings) -> dict[str, Any]:
    limit = _clamp(args.get("limit"), low=1, high=_MAX_ROWS, fallback=_DEFAULT_ROWS)
    rows = await RiskEventRepository().list_recent(limit)
    if not rows:
        return _unknown("还没有任何风控事件")
    items = [
        {
            "at": r.created_at,
            "event_type": r.event_type,
            "detail": _loads(r.detail, {}),
            "related_trade_id": r.related_trade_id,
        }
        for r in rows
    ]
    return {"available": True, "count": len(items), "events": items}


# --- 注册 -------------------------------------------------------------------

_SYMBOL_PARAM = {
    "type": "object",
    "properties": {"symbol": {"type": "string", "description": "交易对，如 BTCUSDT"}},
}


def build_readonly_registry(settings: Settings | None = None) -> ToolRegistry:
    """阶段 B 的经理工具集。**全部只读，无例外。**"""
    cfg = settings or get_settings()
    reg = ToolRegistry()  # allow_writes 保持默认 False

    def _bind(fn):
        async def handler(args: dict[str, Any]) -> dict[str, Any]:
            return await fn(args, cfg)

        return handler

    reg.register(
        ToolSpec(
            name="get_portfolio",
            description=(
                "读最近一次权益快照：权益、现金、持仓、账外持仓。"
                "快照由采集侧按节奏写入，所以给出的是**当时**的账户，不是此刻的。"
            ),
            parameters={"type": "object", "properties": {}},
            handler=_bind(_get_portfolio),
        )
    )
    reg.register(
        ToolSpec(
            name="get_market",
            description=(
                "读本地行情库里某个交易对最近的 K 线摘要（高低、涨跌幅、SMA20、RSI14）。"
                "行情来自实盘采集，与交易所在的 Demo 账户不是同一个来源（ADR-013）。"
                "**先看 `data_status`：本地行情可能是停更的**，`stale=true` 时"
                "那些价格说的是当时、不是此刻，别把它们当现价用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    **_SYMBOL_PARAM["properties"],
                    "bars": {"type": "integer", "description": f"取多少根，默认 {_DEFAULT_BARS}"},
                },
            },
            handler=_bind(_get_market),
        )
    )
    reg.register(
        ToolSpec(
            name="get_risk_limits",
            description="读当前生效的风控参数（单笔上限、日亏上限、止损、回撤、仓位上限）。",
            parameters={"type": "object", "properties": {}},
            handler=_bind(_get_risk_limits),
        )
    )
    reg.register(
        ToolSpec(
            name="get_drawdown",
            description="读当前回撤：全历史峰值权益、最新权益、回撤比例，以及是否已破线。",
            parameters={"type": "object", "properties": {}},
            handler=_bind(_get_drawdown),
        )
    )
    reg.register(
        ToolSpec(
            name="get_recent_decisions",
            description="读最近的 LLM 决策记录（动作、信心、理由）。",
            parameters={
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": f"默认 {_DEFAULT_ROWS}"}},
            },
            handler=_bind(_get_recent_decisions),
        )
    )
    reg.register(
        ToolSpec(
            name="get_recent_trades",
            description="读最近的交易记录（含风控裁决与理由）。",
            parameters={
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": f"默认 {_DEFAULT_ROWS}"}},
            },
            handler=_bind(_get_recent_trades),
        )
    )
    reg.register(
        ToolSpec(
            name="get_recent_risk_events",
            description="读最近的风控事件（强平、回撤重置等）。",
            parameters={
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": f"默认 {_DEFAULT_ROWS}"}},
            },
            handler=_bind(_get_recent_risk_events),
        )
    )
    return reg
