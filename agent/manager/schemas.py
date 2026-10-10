"""经理产出的计划（阶段 B 的验收物）。

验收是「产出**可读的分解计划与理由**」。所以 `Plan` 里**每一条都得带 `why`** ——
一个只有动作没有依据的清单，事后无法归因，也就不满足 ADR-019 对这条链的要求。
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    what: str
    why: str | None = None
    tool: str | None = None


class Plan(BaseModel):
    objective: str
    steps: list[PlanStep]
    stop_conditions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)

    @property
    def steps_without_reason(self) -> list[str]:
        """没写依据的步骤 —— 交给调用方决定要不要打回重写。"""
        return [s.what for s in self.steps if not (s.why or "").strip()]


def strip_fences(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    return t.strip()


def parse_plan(text: str) -> Plan | None:
    """正文 → `Plan`。**解不出来就返回 None，不猜、不补。**"""
    body = strip_fences(text)
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return Plan.model_validate(data)
    except Exception:  # noqa: BLE001 — 校验不过就当没解出来，由调用方决定重试
        return None
