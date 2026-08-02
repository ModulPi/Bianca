from __future__ import annotations

from typing import TypedDict


class TradeState(TypedDict, total=False):
    market_data: dict
    llm_signal: dict | None
    risk_decision: dict | None
    order_result: dict | None
    llm_auto_execute: bool
    analysis_result: dict | None
    decision_id: str | None
    trade_log_id: str | None
    pending_signal_id: str | None
    session_id: str | None
    status: str
    message: str
