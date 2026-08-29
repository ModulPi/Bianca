from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest

AGENT_TICKS = Counter("bianca_agent_ticks_total", "Agent loop ticks", ["status"])
TRADES = Counter("bianca_trades_total", "Trade executions", ["side", "status", "market"])
RISK_REJECTS = Counter("bianca_risk_rejects_total", "Risk rule rejections", ["rule"])
LLM_CALLS = Counter("bianca_llm_calls_total", "LLM analysis calls", ["provider", "action"])
AGENT_RUNNING = Gauge("bianca_agent_running", "Whether background agent runner is active")


def record_agent_tick(status: str | None) -> None:
    AGENT_TICKS.labels(status=status or "unknown").inc()


def record_trade(*, side: str, status: str, market: str = "spot") -> None:
    TRADES.labels(side=side.upper(), status=status, market=market).inc()


def record_risk_reject(rule: str | None) -> None:
    RISK_REJECTS.labels(rule=rule or "unknown").inc()


def record_llm_call(*, provider: str, action: str) -> None:
    LLM_CALLS.labels(provider=provider, action=action.upper()).inc()


def set_agent_running(running: bool) -> None:
    AGENT_RUNNING.set(1 if running else 0)


def metrics_payload() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
