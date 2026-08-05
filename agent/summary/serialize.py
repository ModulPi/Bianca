from __future__ import annotations

from typing import Any

from agent.storage.json_utils import parse_json_field
from agent.storage.models import SessionSummaryRow


def session_row_to_summary(row: SessionSummaryRow) -> dict[str, Any]:
    return {
        "session_id": row.id,
        "started_at": row.started_at,
        "ended_at": row.ended_at,
        "agent": {
            "tick_count": row.tick_count,
            "tick_interval_sec": None,
            "trading_style": row.trading_style,
            "last_status": None,
        },
        "usage": parse_json_field(row.usage_json),
        "trades": parse_json_field(row.trades_json),
        "pnl": parse_json_field(row.pnl_json),
        "positions": parse_json_field(row.positions_json),
        "highlights": _highlights_from_row(row),
    }


def _highlights_from_row(row: SessionSummaryRow) -> list[str]:
    trades = parse_json_field(row.trades_json)
    lines: list[str] = []
    if trades.get("loop_closed"):
        lines.append("闭环：≥1 BUY filled + ≥1 SELL filled")
    if trades.get("failed"):
        lines.append(f"失败订单 {trades['failed']} 笔")
    return lines or ["会话已归档"]
