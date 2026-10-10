"""权益口径的盈亏与回撤。

旧实现累加买卖的现金流（`execute_agent._update_daily_pnl`），把**买入当成亏损**：
花 100 USDT 买币，账上立刻记 -100，尽管那 100 变成了等值的持仓。它只能靠
"卖出时又记 +100"来抵消，而中间任何时候的读数都不是盈亏。

这里换成权益口径，两个派生量：

  · **日盈亏** = 现在的权益 − 今天开始时的权益（日亏熔断的输入）
  · **回撤**   = (峰值权益 − 现在权益) / 峰值权益（回撤熔断的输入，ADR-029）

持仓的浮亏自然被算进去（权益里已经按市值计了），因为那确实是我们承担的风险。

关键约定：**算不出来就说算不出来**。没有基准时返回 `known=False`，
由风控决定怎么办 —— 绝不返回 0.0。因为 0.0 意味着"今天没亏"，
熔断器会静默失效，那正是这套东西存在的理由。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from agent.storage.repository import (
    AgentConfigRepository,
    EquityRepository,
    RiskEventRepository,
)


def utc_day_start(now: datetime | None = None) -> str:
    """当天 UTC 零点，ISO 字符串。日盈亏按 UTC 日切，与 token 用量统计一致。"""
    moment = now or datetime.now(UTC)
    return (
        moment.astimezone(UTC)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .isoformat()
    )


@dataclass(frozen=True)
class DailyPnl:
    known: bool
    pnl: float | None = None
    baseline_equity: float | None = None
    current_equity: float | None = None
    reason: str | None = None


async def daily_pnl(
    repo: EquityRepository | None = None,
    *,
    now: datetime | None = None,
) -> DailyPnl:
    """今天的盈亏，以**当天第一条权益快照**为基准。

    基准不是"昨天收盘"而是"今天第一眼看到的样子"。agent 若在盘中才启动，
    上限就从启动那一刻开始算 —— 这是可达信息里的最诚实选择，已记为已知偏差。
    只要守卫在跑，第一条快照总在开盘前或第一笔交易前产生，两者的差别就只在
    盘中启动的那一天。
    """
    equity = repo or EquityRepository()
    baseline = await equity.first_since(utc_day_start(now))
    if baseline is None:
        return DailyPnl(
            known=False,
            reason="今天还没有权益快照，无从判断日盈亏（这不是没亏，是不知道）",
        )

    current = await equity.latest()
    if current is None:  # pragma: no cover — baseline 存在则 latest 必然存在
        return DailyPnl(known=False, reason="权益快照读取异常")

    return DailyPnl(
        known=True,
        pnl=current.equity - baseline.equity,
        baseline_equity=baseline.equity,
        current_equity=current.equity,
    )


# --------------------------------------------------------------- 回撤（ADR-029）

_DRAWDOWN_RESET_KEY = "drawdown_reset_at"


@dataclass(frozen=True)
class Drawdown:
    known: bool
    pct: float | None = None
    peak_equity: float | None = None
    current_equity: float | None = None
    since: str | None = None
    reason: str | None = None


async def drawdown_reset_at(config: AgentConfigRepository | None = None) -> str | None:
    """上一次"回撤重置"的时刻。没重置过是 None（峰值看全历史）。"""
    repo = config or AgentConfigRepository()
    return await repo.get(_DRAWDOWN_RESET_KEY)


@dataclass(frozen=True)
class DrawdownReset:
    """一次重置的结果。**含重置前的账** —— 只看事后状态看不出削掉了多少保护。

    由 `reset_drawdown` 一次读出、同时用于落事件与对外返回，避免两处各读一次而
    对不上（那会让"接口说的"与"库里记的"变成两笔账）。
    """

    reset_at: str
    peak_before: float | None = None
    current_equity: float | None = None
    drawdown_before: float | None = None
    previous_reset_at: str | None = None


async def reset_drawdown(*, reason: str, now: datetime | None = None) -> DrawdownReset:
    """显式重置回撤基准（ADR-029 / ADR-030：触发后**不会自愈**，必须有人确认）。

    写的是一个时刻而不是删数据：快照全留着（它们仍是权益历史），
    只是从此峰值只从这一刻往后看。这样"重置过"本身也是可查的。

    **理由必填，且会写一条 `risk_events`。** 重置是唯一能让回撤线松开的操作；
    一个能静默解除风控的动作，等于给"永远不再触发"留了后门（ADR-027 同一条规矩：
    绕过闸门必须留痕）。
    """
    if not reason or not reason.strip():
        raise ValueError("重置回撤必须写明理由（ADR-030：不留痕的重置等于静默解除风控）")

    before = await drawdown()
    moment = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
    result = DrawdownReset(
        reset_at=moment,
        peak_before=before.peak_equity,
        current_equity=before.current_equity,
        drawdown_before=before.pct,
        previous_reset_at=before.since,
    )

    await AgentConfigRepository().set(_DRAWDOWN_RESET_KEY, moment)
    await RiskEventRepository().save(
        event_type="drawdown_reset",
        detail={
            "reason": reason.strip(),
            "reset_at": result.reset_at,
            # 重置**前**的账：这是事后判断"这次重置削掉了多少保护"的唯一依据
            "peak_before": result.peak_before,
            "current_equity": result.current_equity,
            "drawdown_before": result.drawdown_before,
            "previous_reset_at": result.previous_reset_at,
        },
    )
    return result


async def drawdown(
    repo: EquityRepository | None = None,
    config: AgentConfigRepository | None = None,
    *,
    now: datetime | None = None,
) -> Drawdown:
    """权益当前值相对峰值的回撤比例。

    峰值 = 快照表的最高权益（重置点之后）。当前值 = **最新快照**，不是实时余额 ——
    回撤要的是可复现的口径，而实时余额每个 tick 都在动、事后无从复算。
    """
    del now  # 目前不按日切；保留参数是为了调用方与 daily_pnl 形状一致
    equity = repo or EquityRepository()
    since = await drawdown_reset_at(config)

    peak = await equity.peak(since)
    current = await equity.latest()
    if peak is None or current is None:
        return Drawdown(
            known=False,
            since=since,
            reason="还没有权益快照，算不出回撤（这不是没回撤，是不知道）",
        )
    if peak.equity <= 0:
        # 权益 <= 0 时比例没有意义（会算出负数或除零），不硬算
        return Drawdown(
            known=False,
            peak_equity=peak.equity,
            current_equity=current.equity,
            since=since,
            reason=f"峰值权益 {peak.equity:.2f} 非正，回撤比例没有意义",
        )

    return Drawdown(
        known=True,
        pct=(peak.equity - current.equity) / peak.equity,
        peak_equity=peak.equity,
        current_equity=current.equity,
        since=since,
    )
