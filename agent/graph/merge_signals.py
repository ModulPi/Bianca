from __future__ import annotations

from typing import Any, Literal

MergeMode = Literal["llm_primary", "strategy_primary", "consensus", "min_confidence"]


def _actionable(signal: dict[str, Any] | None) -> bool:
    if not signal:
        return False
    return signal.get("action") in {"BUY", "SELL"}


def merge_signals(
    agent_signals: list[dict[str, Any]],
    *,
    mode: MergeMode = "llm_primary",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    将多 Agent 信号聚合为单一 llm_signal。
    返回 (merged_signal, merge_meta)。
    """
    analysis = next((s for s in agent_signals if s.get("agent") == "analysis"), None)
    strategy = next((s for s in agent_signals if s.get("agent") == "strategy"), None)
    a_sig = (analysis or {}).get("signal") or {"action": "HOLD", "confidence": 0.0, "reason": "无 AI 信号"}
    s_sig = (strategy or {}).get("signal") or {"action": "HOLD", "confidence": 0.0, "reason": "无策略信号"}

    a_action = a_sig.get("action", "HOLD")
    s_action = s_sig.get("action", "HOLD")
    conflict = _actionable(a_sig) and _actionable(s_sig) and a_action != s_action
    also_conflict = _actionable(a_sig) and _actionable(s_sig) and a_action == s_action and (
        a_sig.get("symbol") != s_sig.get("symbol")
    )
    conflict = conflict or also_conflict

    winner: str
    reason: str
    merged: dict[str, Any]

    if mode == "llm_primary":
        if _actionable(a_sig):
            merged = dict(a_sig)
            winner = "analysis"
            reason = f"AI 为主：采用 AI {a_action}" + (f"（与策略 {s_action} 冲突，听 AI）" if conflict else "")
        elif _actionable(s_sig):
            merged = dict(s_sig)
            winner = "strategy"
            reason = f"AI 为 HOLD，采用策略 {s_action}"
        else:
            merged = dict(a_sig) if a_sig.get("reason") else dict(s_sig)
            merged.setdefault("action", "HOLD")
            winner = "none"
            reason = "AI 与策略均为 HOLD"
    elif mode == "strategy_primary":
        if _actionable(s_sig):
            merged = dict(s_sig)
            winner = "strategy"
            reason = f"策略为主：采用策略 {s_action}"
        elif _actionable(a_sig):
            merged = dict(a_sig)
            winner = "analysis"
            reason = f"策略为 HOLD，采用 AI {a_action}"
        else:
            merged = dict(s_sig) if s_sig.get("reason") else dict(a_sig)
            merged.setdefault("action", "HOLD")
            winner = "none"
            reason = "均为 HOLD"
    elif mode == "consensus":
        if a_action == s_action and _actionable(a_sig):
            merged = dict(a_sig)
            conf = min(float(a_sig.get("confidence") or 0), float(s_sig.get("confidence") or 0))
            merged["confidence"] = conf
            winner = "consensus"
            reason = f"一致 {a_action}"
        else:
            merged = {**a_sig, "action": "HOLD", "reason": f"不一致：AI={a_action} 策略={s_action}"}
            winner = "none"
            reason = merged["reason"]
    else:  # min_confidence
        if conflict:
            merged = {**a_sig, "action": "HOLD", "reason": f"冲突 HOLD：AI={a_action} 策略={s_action}"}
            winner = "none"
            reason = merged["reason"]
        elif _actionable(a_sig) and _actionable(s_sig):
            a_conf = float(a_sig.get("confidence") or 0)
            s_conf = float(s_sig.get("confidence") or 0)
            if a_conf <= s_conf:
                merged = dict(a_sig)
                winner = "analysis"
            else:
                merged = dict(s_sig)
                winner = "strategy"
            reason = f"取较低置信度 {winner}"
        elif _actionable(a_sig):
            merged = dict(a_sig)
            winner = "analysis"
            reason = f"仅 AI {a_action}"
        elif _actionable(s_sig):
            merged = dict(s_sig)
            winner = "strategy"
            reason = f"仅策略 {s_action}"
        else:
            merged = dict(a_sig)
            merged.setdefault("action", "HOLD")
            winner = "none"
            reason = "均为 HOLD"

    merge_meta = {
        "mode": mode,
        "conflict": conflict,
        "winner": winner,
        "reason": reason,
        "analysis_action": a_action,
        "strategy_action": s_action,
        "sources": agent_signals,
    }
    merged["merge_reason"] = reason
    return merged, merge_meta
