"""目标什么时候结束 —— **由代码判定，不交给模型**（§6.6 D-1，阶段 D-2）。

三种终止：到期、破线、达成。它们的共同点是"能不能停"不该由经理决定：
一个会被模型重新解释的截止时间不是截止时间，一个能被模型忽略的回撤线
也不是边界（ADR-018 全自动的代价就在这里 —— 没有人能在事前拦下它）。

这一层是纯的：给目标条款、当前权益、回撤，出一个判定。取数、平仓、落库
都在调用方（`run_goal_tick`）：D 阶段要保证"破线时先 `force_close()` 再转终态"，
而那是顺序问题，不是判据问题。

**优先级：到期 → 达成 → 破线。**
到期排第一是因为它是墙上的钟，与账户状态无关；达成排在破线之前，是因为
"已经到过目标、随后回撤又越线"时把结局记成 `aborted` 是在说反话 ——
用户的目标确实达成过。破线仍是账户级的保护动作（ADR-029），只是终态名字不同。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from agent.dialogue.goal import GoalStatus
from agent.risk.params import RiskParams

if TYPE_CHECKING:  # 见文件末尾：模块级导入会造成 storage ↔ dialogue 的循环
    from agent.storage.models import GoalLog
    from agent.storage.repository import GoalRepository


class TerminationKind(StrEnum):
    DEADLINE = "deadline"
    DRAWDOWN = "drawdown"
    ACHIEVED = "achieved"


@dataclass(frozen=True)
class GoalTerms:
    """判定终止所需的全部目标侧条款。**已经过解析，不再碰库。**"""

    deadline_at: str
    baseline_equity: float | None
    target_return_pct: float | None
    target_equity: float | None
    max_drawdown_pct: float

    @classmethod
    def from_row(cls, row: GoalLog) -> GoalTerms:
        """从 `goals` 行取条款。缺什么就抛 —— active 目标不该缺这些。

        `risk_snapshot` 用 `RiskParams.from_snapshot` 解析，而不是只掏
        `max_drawdown_pct`：同一份快照执行期还要用来裁决，两处用不同的解析
        迟早会对"这份快照到底完不完整"给出不同答案。
        """
        if not row.deadline_at:
            raise ValueError(
                f"目标 {row.id} 没有 deadline_at —— active 目标不该缺这个时刻，"
                "没有它到期判据永远不会触发"
            )
        try:
            snapshot = json.loads(row.risk_snapshot or "")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"目标 {row.id} 的 risk_snapshot 不是合法 JSON") from exc
        params = RiskParams.from_snapshot(snapshot)
        return cls(
            deadline_at=row.deadline_at,
            baseline_equity=row.baseline_equity,
            target_return_pct=row.target_return_pct,
            target_equity=row.target_equity,
            max_drawdown_pct=params.max_drawdown_pct,
        )


@dataclass(frozen=True)
class TerminationVerdict:
    """一个判定。`status is None` 表示**继续执行**，不是"判不了所以放行"。"""

    kind: TerminationKind | None
    status: GoalStatus | None
    reason: str
    target_equity: float | None = None

    @property
    def terminates(self) -> bool:
        return self.status is not None


def _utc(value: str) -> datetime:
    """目标里的时间戳。**没有时区的值不接受** —— 猜一个时区等于猜截止时刻。"""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"截止时刻不是合法的时间戳：{value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"截止时刻没有时区，无法比较：{value!r}")
    return parsed.astimezone(UTC)


def target_equity_of(terms: GoalTerms) -> float | None:
    """达成阈值（ADR-040）：**绝对数优先**，没有才按基准权益 × (1 + 收益率)。

    优先级不能反过来：用户说「做到 1100」，账户基准恰好是 1000 时两种算法
    得数一样，看不出差别；基准是 990 时，按收益率算会变成 1089 —— 一个用户
    从没说过的目标。ADR-033 管的是收益率的分母，不是把绝对目标悄悄改写。
    """
    if terms.target_equity is not None:
        return float(terms.target_equity)
    if terms.baseline_equity is not None and terms.target_return_pct is not None:
        return float(terms.baseline_equity) * (1.0 + float(terms.target_return_pct))
    return None


def evaluate_termination(
    terms: GoalTerms,
    *,
    equity: float | None,
    drawdown_pct: float | None,
    now: datetime | None = None,
) -> TerminationVerdict:
    """该不该结束。**每一段取不到都是"判不了"，不是 0。**"""
    at = now or datetime.now(UTC)

    deadline = _utc(terms.deadline_at)
    if at >= deadline:
        return TerminationVerdict(
            kind=TerminationKind.DEADLINE,
            status=GoalStatus.EXPIRED,
            reason=f"时间窗已到（截止 {deadline.isoformat()}）",
        )

    target = target_equity_of(terms)
    if target is not None and equity is not None and equity >= target:
        return TerminationVerdict(
            kind=TerminationKind.ACHIEVED,
            status=GoalStatus.ACHIEVED,
            reason=_achieved_reason(terms, equity=equity, target=target),
            target_equity=target,
        )

    if drawdown_pct is not None and drawdown_pct >= terms.max_drawdown_pct:
        return TerminationVerdict(
            kind=TerminationKind.DRAWDOWN,
            status=GoalStatus.ABORTED,
            reason=(
                f"权益回撤 {drawdown_pct:.2%} 已达上限 "
                f"{terms.max_drawdown_pct:.2%}（ADR-029）"
            ),
        )

    return TerminationVerdict(kind=None, status=None, reason=_not_yet(terms, equity, drawdown_pct))


def _achieved_reason(terms: GoalTerms, *, equity: float, target: float) -> str:
    text = f"权益 {equity:.2f} 已达到目标 {target:.2f}"
    if (
        terms.target_equity is not None
        and terms.baseline_equity is not None
        and terms.target_return_pct is not None
    ):
        by_pct = float(terms.baseline_equity) * (1.0 + float(terms.target_return_pct))
        if abs(by_pct - target) > 1e-9:
            # 两个数不一致时都说出来（ADR-040）—— 只报生效的那个，事后会有人
            # 拿着用户原话对不上账，却看不出是口径不同。
            text += f"（用户原话是绝对数 {target:.2f}；按收益率算会是 {by_pct:.2f}）"
    return text


def _not_yet(terms: GoalTerms, equity: float | None, drawdown_pct: float | None) -> str:
    notes: list[str] = []
    target = target_equity_of(terms)
    if target is None:
        notes.append("算不出目标权益（既没有绝对目标，也没有基准权益或收益率）")
    elif equity is None:
        notes.append("算不出当前权益，无法判断是否达成")
    if drawdown_pct is None:
        notes.append("算不出权益回撤，无法判断是否破线")
    return "；".join(notes) if notes else "未触发任何终止条件"


async def apply_termination(
    verdict: TerminationVerdict,
    *,
    goal_id: str,
    goals: GoalRepository | None = None,
):
    """把判定落成终态。**只接受真的终止了的判定。**

    `status is None` 时抛而不是静默返回：调用方拿它去结束目标，说明顺序写错了，
    而"结束一个不该结束的目标"比一次崩溃更难发现。
    """
    if verdict.status is None:
        raise ValueError("这个判定没有终止任何东西，不能拿它去结束目标")

    if goals is None:
        # 延迟导入：`storage.repository` 模块级依赖 `dialogue.goal`，
        # 这里模块级反向导入会形成循环（C-3 撞过一次，全量测试才报）。
        from agent.storage.repository import GoalRepository as _GoalRepository

        goals = _GoalRepository()
    return await goals.end(goal_id, status=verdict.status, reason=verdict.reason)


def describe(verdict: TerminationVerdict) -> dict[str, Any]:
    """给日志/事件用的形状。保持与 `TerminationVerdict` 同名字段，别另起一套。"""
    return {
        "kind": verdict.kind.value if verdict.kind else None,
        "status": verdict.status.value if verdict.status else None,
        "reason": verdict.reason,
        "target_equity": verdict.target_equity,
    }
