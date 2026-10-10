"""对话层（阶段 C）。承载用户与经理的多轮交互，以及目标的生命周期。

模块分工见设计文档 §6.5：C-1 目标实体与会话、C-2 自然语言解析、C-3 多轮修正、
C-4 计划落库。**C 不下单** —— 这一层的终点是"一个有计划的 active 目标"。
"""

from agent.dialogue.extract import Extract, extract
from agent.dialogue.goal import (
    AUTONOMY_FULL,
    REQUIRED_FIELDS,
    RISK_PARAM_FIELDS,
    TERMINAL,
    GoalDraft,
    GoalStatus,
    activation_times,
    check_transition,
    effective_drawdown_limit,
    risk_snapshot,
)
from agent.dialogue.retell import retell

__all__ = [
    "AUTONOMY_FULL",
    "REQUIRED_FIELDS",
    "RISK_PARAM_FIELDS",
    "TERMINAL",
    "Extract",
    "GoalDraft",
    "GoalStatus",
    "activation_times",
    "check_transition",
    "effective_drawdown_limit",
    "extract",
    "retell",
    "risk_snapshot",
]
