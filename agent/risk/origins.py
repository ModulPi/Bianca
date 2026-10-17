"""订单来源（`order_origin`）与它落到 `trade_logs.risk_decision` 的口径。

**为什么单独放一个模块。** 同一组来源值原先在 `agent/risk/close.py` 与
`agent/graph/execute_agent.py` 各写了一份映射，两边只要有一边漏掉新来源，
另一侧就会把它记成"风控通过"—— 这会静默污染归因，且只在新来源上出错，
老测试永远抓不到。来源是两个模块共用的词表，就该只有一份。

**四件事不能压成一个词**（ADR-035 / ADR-038）：

- `risk_force_close`：破线强平，风控判出来的保护动作；
- `goal_achieved`：目标达成后的清仓，不是风控动作；
- `manual_close`：用户指令下的人工全平，既不是破线也不是达成；
- 其余（含 `normal`）：走过常规闸门的普通交易。
"""

from __future__ import annotations

#: 旧值（ADR-035 之前）。库里已经有按它写下的行，要一直认。
ORIGIN_FORCE_CLOSE = "force_close"

#: 破线强平的来源（ADR-035）。
ORIGIN_RISK_FORCE_CLOSE = "risk_force_close"

#: 目标达成后的清仓（ADR-038）。
ORIGIN_GOAL_ACHIEVED = "goal_achieved"

#: 用户指令下的人工全平。走同一条确定性清仓管线，但它既不是破线、也不是达成，
#: 记成一个来源才看得出"这笔单为什么会存在"。
ORIGIN_MANUAL_CLOSE = "manual_close"

#: 由代码发起、绕过常规闸门的订单来源。
FORCED_ORIGINS = frozenset({ORIGIN_FORCE_CLOSE, ORIGIN_RISK_FORCE_CLOSE})


def risk_decision_for(origin: str) -> str:
    """来源 → `trade_logs.risk_decision`。

    **三件事不能压成一个词。** 破线强平、达成清仓、人工全平，在"这笔单为什么
    会存在"上是三个答案；尤其达成清仓与人工全平不是风控动作，记成 `force_close`
    会让事后统计"多少次强制平仓"时把它们算进去。
    """
    if origin in (ORIGIN_GOAL_ACHIEVED, ORIGIN_MANUAL_CLOSE):
        return origin
    if origin in FORCED_ORIGINS:
        return "force_close"
    return "approved"
