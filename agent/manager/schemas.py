"""经理产出的计划（阶段 B 的验收物）。

验收是「产出**可读的分解计划与理由**」。所以 `Plan` 里**每一条都得带 `why`** ——
一个只有动作没有依据的清单，事后无法归因，也就不满足 ADR-019 对这条链的要求。
"""

from __future__ import annotations

import json
import re
from typing import Literal

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


class ManagerDecision(BaseModel):
    """执行阶段经理的最终信号（§6.6 D-2）。

    **经理只给信号，不下单。** 动作之后的风控、幂等、对账全部是确定性管线，
    模型在这里没有旁路可走。
    """

    action: Literal["BUY", "SELL", "HOLD"]
    symbol: str | None = None
    amount: float | None = None
    reason: str
    #: 自报对应计划的第几步（ADR-039）。偏离计划允许，但要能指出偏离到哪去了。
    plan_step: int | None = Field(default=None, ge=1)
    deviation: str | None = None

    def unusable_reason(self) -> str | None:
        """这个信号能不能拿去执行。**不能执行的理由要说出来，不能默默变成 HOLD。**

        HOLD 本身不需要 symbol/amount；买卖缺任何一个都执行不了，而拿默认值补
        （symbol 用配置里的、amount 用上限）等于替经理下单 —— 那不是解析，是编造。
        """
        if self.action == "HOLD":
            return None
        if not (self.symbol or "").strip():
            return f"动作是 {self.action}，但没有给 symbol"
        if self.amount is None or float(self.amount) <= 0:
            return f"动作是 {self.action}，但 amount 缺失或不是正数"
        return None


def parse_decision(text: str) -> ManagerDecision | None:
    """正文 → `ManagerDecision`。**解不出来返回 None，调用方落 HOLD 并记原因。**"""
    body = strip_fences(text)
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    action = data.get("action")
    if isinstance(action, str):
        # 大小写不是语义差异 —— 只统一这一个，其余字段一个都不替它补。
        data["action"] = action.strip().upper()
    try:
        return ManagerDecision.model_validate(data)
    except Exception:  # noqa: BLE001 — 校验不过就是没解出来
        return None
