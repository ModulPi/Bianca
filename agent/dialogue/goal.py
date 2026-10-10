"""目标实体与它的生命周期（阶段 C-1，ADR-020 / 021 / 032 / 033）。

**这一层只解决一件事：把「目标」从一个字符串变成一个实体。**

阶段 B 的入口是 `Manager.plan(goal: str)` —— 收一句话，出去一个计划，中间什么
都不留。所以 `goal_id` / `plan_id` 至今没有生产者，R1 的链头是空的（§6.3）。
C-1 补的就是这一段：目标得有 id、有状态、有原话、有结构化结果，能被修正，
能被追溯。

两条硬规矩，**写在这里，不只写在文档里**：

1. **空就是空。** 用户没说的维度是 `None`，绝不在激活时替它填一个默认值。
   填了，后面每一次「离目标还差多少」都建立在一个用户没同意过的数上 ——
   而且事后看不出来：库里只有一行 `5.0`，没有"这是谁定的"。
   缺哪个就点名问哪个（`GoalDraft.missing()`），这正是 ADR-020 的把关点。
2. **`active` 之后不许改。** ADR-021：风控参数随目标一次性固化，执行期间只读。
   要改只能中止、重提。这条一松，"固化"就成了口号 —— 一个方便的 PATCH
   就能把止损线挪走，而挪走之后没有任何痕迹说明它曾经是别的值。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, ClassVar

from agent.config import Settings

#: ADR-018 把授权级别定死成全自动，所以它不参与"缺字段"的询问，只是留个位。
AUTONOMY_FULL = "full"


class GoalStatus(StrEnum):
    """§2.3 的状态机。

    `proposed` 之前没有别的状态：用户开口那一刻目标就存在了，只是还没确认。
    少一个 `draft` 态的理由是——**用户说过的话本身就是记录**（ADR-019），
    它不该因为"还没成目标"就丢掉。
    """

    PROPOSED = "proposed"
    ACTIVE = "active"
    ACHIEVED = "achieved"
    ABORTED = "aborted"
    EXPIRED = "expired"


TERMINAL: tuple[GoalStatus, ...] = (
    GoalStatus.ACHIEVED,
    GoalStatus.ABORTED,
    GoalStatus.EXPIRED,
)

# 合法迁移。**从 `proposed` 不能直接到 `achieved`** —— 中间必须真的 `active` 过。
# 否则"达成"可以凭空发生：一个从没执行过任何东西的目标，不该有达成记录，
# 因为那条记录背后没有任何可归因的历史。
_ALLOWED: dict[GoalStatus, tuple[GoalStatus, ...]] = {
    GoalStatus.PROPOSED: (GoalStatus.ACTIVE, GoalStatus.ABORTED),
    GoalStatus.ACTIVE: (GoalStatus.ACHIEVED, GoalStatus.ABORTED, GoalStatus.EXPIRED),
    GoalStatus.ACHIEVED: (),
    GoalStatus.ABORTED: (),
    GoalStatus.EXPIRED: (),
}


def check_transition(current: GoalStatus | str, new: GoalStatus | str) -> None:
    """迁移合不合法。不合法就抛，**不静默放过** —— 一个能被随便改回去的状态机
    等于没有状态机，而"这个目标最后是什么结局"是归因时第一个要问的问题。"""
    cur = GoalStatus(current)
    nxt = GoalStatus(new)
    allowed = _ALLOWED[cur]
    if nxt not in allowed:
        tail = "、".join(allowed) if allowed else "无（这是终态）"
        raise ValueError(f"目标状态不能从 {cur} 变成 {nxt}；从 {cur} 只能去：{tail}")


# --- 必要维度 ---------------------------------------------------------------

#: §2.2 六维里**用户必须自己说**的那几个。
#:
#: 「初始资金」不在其中：ADR-033 已把它改成以激活时的**实际权益**为准，
#: 口述的数字只作校验，没有它照样算得出收益率。
#: 「授权级别」也不在：ADR-018 已定死全自动，不是每提一次目标都要问一遍的事。
REQUIRED_FIELDS: tuple[tuple[str, str], ...] = (
    ("symbols", "标的范围"),
    ("target_return_pct", "收益目标"),
    ("max_drawdown_pct", "回撤上限"),
    ("horizon_hours", "时间窗"),
)


def _blank(value: Any) -> bool:
    """是不是「用户没说」。

    **0 不算没说** —— 「回撤上限 0」是句狠话（一分都不许亏），不是沉默。
    这两者在归因时是两回事，所以判空只能用 `is None` / 空串 / 空列表。
    """
    return value is None or value == "" or value == []


@dataclass
class GoalDraft:
    """目标在 `active` 之前的工作副本。

    与库里那一行是两个东西：这份是**还没定稿的**，可以随对话反复改；
    库里的 `goals` 行是它的落盘版本。激活之后这份就不再被写了。
    """

    raw_text: str
    objective: str | None = None
    symbols: list[str] | None = None
    target_return_pct: float | None = None
    max_drawdown_pct: float | None = None
    horizon_hours: float | None = None
    stated_capital: float | None = None
    autonomy: str = AUTONOMY_FULL

    #: 可以被用户改的字段。`raw_text` 与 `autonomy` 都不在里面：
    #: 原话是**记录**不是参数（ADR-019），授权级别由 ADR-018 定死。
    #: 用 `ClassVar` 是因为它**不是字段** —— 少了这个标注，dataclass 会把
    #: 它当成一个真正的字段，于是 `fields()` 里多一个、`from_row` 上多一次
    #: `getattr(row, "REVISABLE")`，直接炸。
    REVISABLE: ClassVar[tuple[str, ...]] = (
        "objective",
        "symbols",
        "target_return_pct",
        "max_drawdown_pct",
        "horizon_hours",
        "stated_capital",
    )

    def missing(self) -> list[str]:
        """还没说的必要维度。**空了才允许激活。**"""
        return [label for attr, label in REQUIRED_FIELDS if _blank(getattr(self, attr))]

    def revise(self, changes: dict[str, Any]) -> list[str]:
        """把这一轮用户提到的字段叠上去，**没提到的一个都不动**。

        「回撤放到 8%」不该把标的范围也一起重置了 —— 所以这里是叠加，不是覆盖。

        返回**真正变了**的字段名。空列表意味着"这句话没有改动任何字段"，
        调用方要能把它和"改成了同一个值"分开：前者该回问一句，后者直接复述。
        """
        unknown = sorted(set(changes) - set(self.REVISABLE))
        if unknown:
            raise ValueError(f"这些不是目标的一部分，改不了：{unknown}")
        changed = []
        for key, value in changes.items():
            if getattr(self, key) != value:
                changed.append(key)
            setattr(self, key, value)
        return changed

    def to_dict(self) -> dict[str, Any]:
        """落盘用的形状。**只含可写字段**，不含 `raw_text`（那是记录，见 rows）。"""
        return {name: getattr(self, name) for name in self.REVISABLE} | {
            "autonomy": self.autonomy
        }

    @classmethod
    def from_row(cls, row: Any) -> GoalDraft:
        """从库里的 `goals` 行还原一份草稿（用于"接着上一轮改"）。

        `symbols` 在库里是 JSON 文本（列表进不了 String 列），这里转回列表 ——
        转换只在这一个地方发生，其余地方拿到的都是列表。
        """
        names = {f.name for f in fields(cls)}
        kwargs = {name: getattr(row, name) for name in names if name != "raw_text"}
        raw_symbols = kwargs.get("symbols")
        if isinstance(raw_symbols, str):
            try:
                kwargs["symbols"] = json.loads(raw_symbols)
            except json.JSONDecodeError as exc:
                # **不退回 None。** None 的含义是"用户没说标的范围"，而这里的真相是
                # "库里的值坏了" —— 把后者说成前者，等于替一条损坏的记录编个合理解释。
                raise ValueError(f"目标 {row.id} 的 symbols 不是合法 JSON：{raw_symbols!r}") from exc
        return cls(raw_text=row.raw_text, **kwargs)


# --- ADR-021：风控参数在确认那一刻固化 ---------------------------------------

#: 要固化的那几个。**只有风控参数**，`trade_symbol` / `market_interval` 这类配置
#: 不在其中 —— 把它们一起冻住，会让"换个交易对要不要重新确认目标"变成一个
#: 没有答案的问题，而那不是 ADR-021 要管的事。
RISK_PARAM_FIELDS: tuple[str, ...] = (
    "max_trade_amount",
    "daily_loss_limit",
    "stop_loss_pct",
    "stop_lookback_hours",
    "max_drawdown_pct",
    "max_position_pct",
    "force_close_enabled",
)


def risk_snapshot(settings: Settings) -> dict[str, Any]:
    """`active` 那一刻把风控参数抄一份下来。

    ADR-021 说"执行期间只读" —— **落地判据就是这一份快照**：之后凡是读风控的
    地方读它，不读 `settings`。否则改一次 `.env` 就绕过去了：目标还在跑，
    它的止损线却悄悄换了，且没有一行记录说明它变过。

    **未决（写在代码里，别让它烂成"看起来没问题"）：**
    `goals.max_drawdown_pct`（用户说的）与这里抄下来的 `max_drawdown_pct`
    （引擎配置的）是**同一个数的两个来源**，而 C-1 这一版只抄 `Settings`，
    用户的那个数并没有进快照。于是"用户说最多亏 5%、配置里是 3%"时谁说了算，
    现在没有答案。这是 ADR-021 没说透的那一半（它只说"固化"，没说
    "用户能不能改设定值，以及能不能改得更宽"）。**定下来之前不许把任何一个
    悄悄当成生效值** —— 见 §6.5 末的待决项。
    """
    return {name: getattr(settings, name) for name in RISK_PARAM_FIELDS}


def _now() -> datetime:
    return datetime.now(UTC)


def activation_times(horizon_hours: float, *, now: datetime | None = None) -> tuple[str, str]:
    """激活那一刻的两个时间戳：`confirmed_at` 与 `deadline_at`。

    截止时刻在这里算一次、存下来，而不是每次现算 —— 时间窗到期是硬性终止条件
    （§2.3 由**代码**判定），它得有个钉死的时刻可比，而不是每次拿浮点数重算一遍。
    """
    at = now or _now()
    return at.isoformat(), (at + timedelta(hours=horizon_hours)).isoformat()
