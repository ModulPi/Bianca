"""M9 多 Agent 信号合并单测。"""

from agent.graph.merge_signals import merge_signals


def _sig(agent: str, action: str, reason: str = "", confidence: float = 0.8) -> dict:
    return {
        "agent": agent,
        "signal": {
            "action": action,
            "symbol": "BTCUSDT",
            "amount": 10.0 if action != "HOLD" else None,
            "confidence": confidence,
            "reason": reason or action,
        },
    }


def test_llm_primary_prefers_analysis_on_conflict():
    signals = [_sig("analysis", "BUY"), _sig("strategy", "SELL")]
    merged, meta = merge_signals(signals, mode="llm_primary")
    assert merged["action"] == "BUY"
    assert meta["conflict"] is True
    assert meta["winner"] == "analysis"


def test_llm_primary_falls_back_to_strategy_when_analysis_hold():
    signals = [_sig("analysis", "HOLD"), _sig("strategy", "BUY")]
    merged, meta = merge_signals(signals, mode="llm_primary")
    assert merged["action"] == "BUY"
    assert meta["winner"] == "strategy"


def test_consensus_requires_agreement():
    signals = [_sig("analysis", "BUY"), _sig("strategy", "SELL")]
    merged, meta = merge_signals(signals, mode="consensus")
    assert merged["action"] == "HOLD"
    assert meta["conflict"] is True


def test_consensus_both_buy():
    signals = [_sig("analysis", "BUY", confidence=0.9), _sig("strategy", "BUY", confidence=0.7)]
    merged, meta = merge_signals(signals, mode="consensus")
    assert merged["action"] == "BUY"
    assert meta["winner"] == "consensus"


def test_empty_signals_hold():
    merged, meta = merge_signals([], mode="llm_primary")
    assert merged["action"] == "HOLD"
