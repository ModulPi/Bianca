from __future__ import annotations

import json
from typing import Any

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
        "usage": json.loads(row.usage_json),
        "trades": json.loads(row.trades_json),
        "pnl": json.loads(row.pnl_json),
        "positions": json.loads(row.positions_json),
        "highlights": _highlights_from_row(row),
    }


def _highlights_from_row(row: SessionSummaryRow) -> list[str]:
    trades = json.loads(row.trades_json)
    lines: list[str] = []
    if trades.get("loop_closed"):
        lines.append("闭环：≥1 BUY filled + ≥1 SELL filled")
    if trades.get("failed"):
        lines.append(f"失败订单 {trades['failed']} 笔")
    return lines or ["会话已归档"]
