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
    status: str
    message: str
    # 归因链的链头（ADR-019 R1）：目标 → 计划 → 判断 → 信号 → 风控 → 订单。
    # 阶段 C 才会有 goal 实体，但通道**现在就得在**：LangGraph 只传递 Schema 里
    # 声明过的键，没声明的会被静默丢掉 —— 节点里那些 `state.get("goal_id")`
    # 会永远拿到 None，而列建好了、看着像是通的。
    goal_id: str | None
    plan_id: str | None
    # 本轮裁决用的风控边界，**从目标快照抄来**（ADR-021 的落地判据）。
    # 不放在这里的话，风险节点只能读全局 settings —— 目标在跑、`.env` 一改，
    # 它的止损线就跟着变，而且没有任何地方会报出来。用 dict 是为了能进
    # LangGraph 的检查点（dataclass 没有声明就会被静默丢掉，见上）。
    risk_params: dict | None
