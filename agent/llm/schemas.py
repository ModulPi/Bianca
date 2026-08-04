from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class TradeSignal(BaseModel):
    """Structured LLM trading signal (BUY / SELL / HOLD)."""

    action: Literal["BUY", "SELL", "HOLD"]
    symbol: str
    amount: float | None = Field(
        default=None,
        description="BUY: quote amount (USDT); SELL: base asset quantity; HOLD: null",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)

    @field_validator("action", mode="before")
    @classmethod
    def normalize_action(cls, value: object) -> str:
        if isinstance(value, str):
            return value.strip().upper()
        raise ValueError("action must be BUY, SELL, or HOLD")

    @property
    def is_actionable(self) -> bool:
        return self.action in {"BUY", "SELL"}

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")


class AnalysisResult(BaseModel):
    signal: TradeSignal
    raw_output: str
    model_used: str
    prompt_summary: str
    auto_execute: bool
    decision_id: str | None = None
    analysis_report_id: str | None = None
    usage: dict[str, Any] | None = None
