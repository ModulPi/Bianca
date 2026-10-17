"""风控参数从哪来（ADR-021 / ADR-036）。

阶段 C 已经把风控参数在确认那一刻抄进了 `goals.risk_snapshot`，但执行层一直
还在读 `settings` —— 于是「执行期间只读」只停留在库里的那一行：改一次 `.env`
就能把正在跑的目标的止损线和回撤上限一起换掉，而且没有任何地方会报出来。

`RiskParams` 就是那个缺口：**目标在跑，参数就来自目标**；没有目标（旧的手动
tick 路径）才来自配置。两者形状相同，规则只管读。

**不从快照回落到配置。** 缺一列就抛：那个目标当初确认时就没有这条边界，
拿今天的 `.env` 补上等于凭空给它加了一条用户没确认过、也无法归因的规则。
宁可这一轮失败，也不要用一个来路不明的数去做风控。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agent.config import Settings

#: 要固化、也就是执行期要读的那几个。与 `dialogue.goal.RISK_PARAM_FIELDS` 是
#: 同一份 —— 那边的快照就是按这个列表抄的，这里再抄一次是为了不让 `risk`
#: 依赖 `dialogue`（依赖方向只能是对话层知道风控，不能反过来）。
RISK_PARAM_FIELDS: tuple[str, ...] = (
    "max_trade_amount",
    "daily_loss_limit",
    "stop_loss_pct",
    "stop_lookback_hours",
    "max_drawdown_pct",
    "max_position_pct",
    "force_close_enabled",
)


@dataclass(frozen=True)
class RiskParams:
    """一份风控参数的定稿。冻结的：一份裁决不该中途换尺子。"""

    max_trade_amount: float
    daily_loss_limit: float
    stop_loss_pct: float
    stop_lookback_hours: float
    max_drawdown_pct: float
    max_position_pct: float
    force_close_enabled: bool

    @classmethod
    def from_settings(cls, settings: Settings) -> RiskParams:
        """没有目标时的来源：当前配置。**只给旧的手动 tick 用。**"""
        return cls(**{name: getattr(settings, name) for name in RISK_PARAM_FIELDS})

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, Any]) -> RiskParams:
        """目标确认时抄下来的那份。**缺就是错，不回落。**

        `None` 与"没有这一列"同等对待：快照里的 `null` 表达不了任何边界，
        把它当成 0 或当成配置都会让这道闸门比用户确认的更松或更紧。
        """
        missing = [name for name in RISK_PARAM_FIELDS if snapshot.get(name) is None]
        if missing:
            raise ValueError(
                "目标的风控快照缺字段（"
                + "、".join(missing)
                + "），不敢替它补：那会变成一条用户没确认过、也无法归因的规则（ADR-021）"
            )
        return cls(**{name: snapshot[name] for name in RISK_PARAM_FIELDS})
