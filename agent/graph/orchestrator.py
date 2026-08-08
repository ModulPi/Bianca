from __future__ import annotations

import json
from typing import Any

from agent.cache.redis_client import cache_get, cache_set
from agent.config import Settings, get_settings
from agent.graph.state import TradeState

CHAT_DIRECTIVES_KEY = "bianca:chat:directives"
CHAT_MESSAGES_KEY = "bianca:chat:messages"


async def get_chat_directives(session_id: str | None = None) -> list[dict[str, Any]]:
    raw = await cache_get(CHAT_DIRECTIVES_KEY)
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if session_id:
        return [d for d in items if not d.get("session_id") or d.get("session_id") == session_id]
    return items


async def set_chat_directives(directives: list[dict[str, Any]]) -> None:
    await cache_set(CHAT_DIRECTIVES_KEY, json.dumps(directives[-50:]))


async def append_chat_directive(directive: dict[str, Any]) -> None:
    items = await get_chat_directives()
    action = directive.get("action")
    symbol = (directive.get("symbol") or "").upper()
    if action == "resume_symbol" and symbol:
        items = [
            d
            for d in items
            if not (d.get("action") == "pause_symbol" and (d.get("symbol") or "").upper() == symbol)
        ]
    elif action == "resume_all":
        items = [d for d in items if d.get("action") not in {"pause_symbol", "pause_all"}]
    items.append(directive)
    await set_chat_directives(items)


async def get_chat_messages(limit: int = 50) -> list[dict[str, Any]]:
    raw = await cache_get(CHAT_MESSAGES_KEY)
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return items[-limit:]


async def append_chat_message(role: str, content: str, *, meta: dict[str, Any] | None = None) -> None:
    items = await get_chat_messages(limit=200)
    items.append({"role": role, "content": content, **(meta or {})})
    await cache_set(CHAT_MESSAGES_KEY, json.dumps(items[-200:]))


def build_orchestrator_plan(
    state: TradeState,
    *,
    settings: Settings | None = None,
    chat_directives: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """规则调度：默认 AI + 趋势策略；聊天指令可暂停 symbol。"""
    cfg = settings or get_settings()
    symbol = str(state.get("symbol") or cfg.trade_symbol).upper()
    directives = chat_directives or []

    use_analysis = True
    use_strategy = True
    strategy_type = "trend"
    skip_tick = False
    skip_reason = ""

    for d in directives:
        action = d.get("action")
        target = (d.get("symbol") or "").upper()
        if action == "pause_symbol" and target == symbol:
            skip_tick = True
            skip_reason = f"聊天指令暂停 {symbol}"
            use_analysis = False
            use_strategy = False
        elif action == "pause_all":
            skip_tick = True
            skip_reason = "聊天指令暂停全部"
            use_analysis = False
            use_strategy = False
        elif action == "disable_strategy" and (not target or target == symbol):
            use_strategy = False
        elif action == "disable_analysis" and (not target or target == symbol):
            use_analysis = False
        elif action == "enable_strategy":
            use_strategy = True
        elif action == "enable_analysis":
            use_analysis = True

    return {
        "use_analysis": use_analysis,
        "use_strategy": use_strategy,
        "strategy_type": strategy_type,
        "strategy_ids": [],
        "skip_tick": skip_tick,
        "skip_reason": skip_reason,
        "symbol": symbol,
    }


async def orchestrator_node(state: TradeState) -> TradeState:
    session_id = state.get("session_id")
    directives = await get_chat_directives(session_id)
    plan = build_orchestrator_plan(state, chat_directives=directives)
    return {
        **state,
        "orchestrator_plan": plan,
        "chat_directives": directives,
        "agent_signals": [],
    }
