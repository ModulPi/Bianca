from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
import logging
from typing import Any

from agent.config import Settings, get_settings
from agent.llm.prompts import base_asset_for_symbol
from agent.storage.json_utils import parse_json_field
from agent.storage.models import SessionSummaryRow, TradeLog
from agent.storage.repository import (
    AgentConfigRepository,
    DecisionRepository,
    SessionSummaryRepository,
    TradeRepository,
)
from agent.summary.pnl import PnLResult, compute_pnl
from agent.summary.positions import resolve_session_positions

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _trade_to_dict(row: TradeLog) -> dict[str, Any]:
    return {
        "id": row.id,
        "side": row.side,
        "quantity": row.quantity,
        "price": row.price,
        "status": row.status,
        "created_at": row.created_at,
    }


def _count_trades(trades: list[TradeLog]) -> dict[str, Any]:
    buy_filled = sum(1 for t in trades if t.side == "BUY" and t.status == "filled")
    sell_filled = sum(1 for t in trades if t.side == "SELL" and t.status == "filled")
    failed = sum(1 for t in trades if t.status == "failed")
    signal_only = sum(1 for t in trades if t.status == "signal_only")
    return {
        "buy_filled": buy_filled,
        "sell_filled": sell_filled,
        "failed": failed,
        "signal_only": signal_only,
        "loop_closed": buy_filled >= 1 and sell_filled >= 1,
    }


async def _usage_in_window(started_at: str, ended_at: str | None) -> dict[str, int]:
    repo = DecisionRepository()
    rows = await repo.list_since(started_at, ended_at)
    calls = len(rows)
    prompt = sum(r.prompt_tokens or 0 for r in rows)
    completion = sum(r.completion_tokens or 0 for r in rows)
    total = sum(r.total_tokens or 0 for r in rows)
    return {
        "llm_calls": calls,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "estimated_cost_usd": round(total * 0.000001, 6),
    }


def _highlights(trades_stats: dict[str, Any], failed: int) -> list[str]:
    lines: list[str] = []
    if trades_stats.get("loop_closed"):
        lines.append("闭环：≥1 BUY filled + ≥1 SELL filled")
    else:
        lines.append("闭环未完成：需要 filled BUY 与 SELL 各至少 1 笔")
    if failed:
        lines.append(f"失败订单 {failed} 笔，详见 trade_logs")
    return lines


async def build_session_summary(
    *,
    session_id: str,
    started_at: str,
    ended_at: str | None,
    tick_count: int,
    last_status: str | None,
    settings: Settings | None = None,
    positions: dict[str, Any] | None = None,
    mark_price: float | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    trade_repo = TradeRepository()
    config_repo = AgentConfigRepository()
    trades = await trade_repo.list_since(started_at, ended_at)
    filled = [t for t in trades if t.status == "filled"]
    trades_stats = _count_trades(trades)
    usage = await _usage_in_window(started_at, ended_at)
    daily_pnl_legacy = await config_repo.get_daily_pnl()

    if positions is None:
        positions, resolved_mark = await resolve_session_positions(settings=cfg)
        if mark_price is None:
            mark_price = resolved_mark

    base = base_asset_for_symbol(cfg.trade_symbol)
    base_free = float((positions or {}).get("base_free") or 0)
    usdt_free = float((positions or {}).get("usdt_free") or 0)
    mark = mark_price or (positions or {}).get("mark_price")

    pnl: PnLResult = compute_pnl(
        [_trade_to_dict(t) for t in filled],
        base_free=base_free,
        mark_price=float(mark) if mark else None,
        daily_pnl_legacy=daily_pnl_legacy,
    )

    return {
        "session_id": session_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "agent": {
            "tick_count": tick_count,
            "tick_interval_sec": cfg.agent_tick_interval,
            "trading_style": cfg.trading_style,
            "last_status": last_status,
        },
        "usage": usage,
        "trades": trades_stats,
        "pnl": {
            "cash_flow_usdt": pnl.cash_flow_usdt,
            "realized_usdt": pnl.realized_usdt,
            "unrealized_usdt": pnl.unrealized_usdt,
            "total_usdt": pnl.total_usdt,
            "daily_pnl_legacy": daily_pnl_legacy,
        },
        "positions": {
            "base_asset": base,
            "base_free": base_free,
            "usdt_free": usdt_free,
            "mark_price": mark,
        },
        "highlights": _highlights(trades_stats, trades_stats["failed"]),
    }


async def build_daily_summary_text(date: str | None = None) -> str:
    day = date or datetime.now(UTC).date().isoformat()
    repo = SessionSummaryRepository()
    rows = await repo.list_recent(limit=100)
    day_rows = [r for r in rows if r.started_at.startswith(day)]
    total_pnl = sum(parse_json_field(r.pnl_json).get("realized_usdt", 0) for r in day_rows)
    loops = sum(1 for r in day_rows if r.loop_closed)
    return f"日期 {day}\n会话 {len(day_rows)} · 闭环 {loops}\n已实现 PnL: {total_pnl:.4f} USDT"


async def persist_session_summary(summary: dict[str, Any]) -> SessionSummaryRow:
    repo = SessionSummaryRepository()
    return await repo.save(
        session_id=summary["session_id"],
        started_at=summary["started_at"],
        ended_at=summary.get("ended_at"),
        tick_count=summary["agent"]["tick_count"],
        trading_style=summary["agent"]["trading_style"],
        usage_json=summary["usage"],
        trades_json=summary["trades"],
        pnl_json=summary["pnl"],
        positions_json=summary["positions"],
        loop_closed=bool(summary["trades"].get("loop_closed")),
    )


async def save_interim_snapshot(
    *,
    session_id: str,
    started_at: str,
    tick_count: int,
    last_status: str | None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    summary = await build_session_summary(
        session_id=session_id,
        started_at=started_at,
        ended_at=None,
        tick_count=tick_count,
        last_status=last_status,
        settings=settings,
    )
    await persist_session_summary(summary)
    return summary


async def close_session(
    *,
    session_id: str,
    started_at: str,
    tick_count: int,
    last_status: str | None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    ended_at = _utc_now()
    summary = await build_session_summary(
        session_id=session_id,
        started_at=started_at,
        ended_at=ended_at,
        tick_count=tick_count,
        last_status=last_status,
        settings=settings,
    )
    await persist_session_summary(summary)
    try:
        from agent.notify.telegram import format_session_summary, notify_session_closed
        from agent.notify.email import send_email
        from agent.validation.paper_gate import record_session_for_validation

        await record_session_for_validation(summary, settings=settings)
        cfg = settings or get_settings()
        text = format_session_summary(summary)
        await notify_session_closed(summary, settings=cfg)
        if cfg.notify_on_session_close and cfg.email_configured:
            await send_email("Bianca 会话结束", text, settings=cfg)
    except Exception:  # noqa: BLE001
        logger.exception("Post-session notify/validation failed")
    return summary
