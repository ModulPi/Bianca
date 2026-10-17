from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent.portfolio.attribution import split_symbol
from agent.portfolio.valuation import CASH_ASSETS, Position
from agent.risk.params import RiskParams


@dataclass
class RiskContext:
    signal: dict[str, Any]
    market_data: dict[str, Any]
    #: 这次裁决用哪套边界。目标在跑就是目标确认时那份（ADR-021），
    #: 不是当前 `.env` —— 规则只管读，不问来路。
    params: RiskParams
    # 今天的盈亏，**权益口径**（含持仓浮亏）。None 表示**算不出来** ——
    # 它不等于 0，也不等于"没亏"。见 `agent/portfolio/pnl.py`。
    daily_pnl: float | None = None
    # 最新的权益与持仓（取自权益快照）。None / 空同样表示**算不出来**。
    # 风控看不见持仓就没法谈仓位上限 —— 那时候只能拦，不能放。
    equity: float | None = None
    positions: tuple[Position, ...] = field(default_factory=tuple)
    # 资产 → 该资产对计价币的**实盘**近期最高价（ADR-026）。缺失 = 拿不到，不是 0。
    highs: Mapping[str, float | None] = field(default_factory=dict)
    # 权益相对峰值的回撤比例，None = 算不出来（ADR-029）。
    drawdown_pct: float | None = None
    peak_equity: float | None = None
    drawdown_since: str | None = None

    def holding(self, asset: str) -> Position | None:
        wanted = asset.upper()
        for pos in self.positions:
            if pos.asset == wanted:
                return pos
        return None


@dataclass
class RiskVerdict:
    approved: bool
    reason: str
    rule: str | None = None


class RiskRule(Protocol):
    name: str

    def evaluate(self, ctx: RiskContext) -> RiskVerdict | None:
        """Return RiskVerdict if rule triggers rejection; None if passed."""


class MaxTradeAmountRule:
    name = "max_trade_amount"

    def evaluate(self, ctx: RiskContext) -> RiskVerdict | None:
        signal = ctx.signal
        action = signal.get("action", "HOLD")
        if action not in {"BUY", "SELL"}:
            return None

        amount = signal.get("amount")
        if amount is None or float(amount) <= 0:
            return RiskVerdict(
                approved=False,
                reason="可执行信号缺少有效 amount",
                rule=self.name,
            )

        max_usdt = ctx.params.max_trade_amount
        notional = float(amount)

        if action == "SELL":
            price = ctx.market_data.get("last") or ctx.market_data.get("bid")
            if price:
                notional = float(amount) * float(price)

        if notional > max_usdt:
            return RiskVerdict(
                approved=False,
                reason=f"单笔名义金额 {notional:.2f} USDT 超过上限 {max_usdt:.2f}",
                rule=self.name,
            )
        return None


class DailyLossRule:
    """日亏熔断。**只拦开仓**（BUY）。

    熔断的目的是"别再加大风险"，不是"不许逃跑"。若把 SELL 也一起拦，触及
    熔断后仓位就锁死了，只能看着它继续跌 —— 熔断本身成了最大的风险源。
    所以这里放行 SELL（降低敞口的方向）。单笔金额上限（MaxTradeAmountRule）
    对买卖双向都仍然生效，风控依旧必经（ADR-006 不变）。
    """

    name = "daily_loss"

    def evaluate(self, ctx: RiskContext) -> RiskVerdict | None:
        # 只对开仓生效。方向判断放在最前面，后面的两条拒绝理由都只针对 BUY。
        if ctx.signal.get("action", "HOLD").upper() != "BUY":
            return None

        limit = ctx.params.daily_loss_limit

        if ctx.daily_pnl is None:
            # **算不出来就拦住**，而不是放行。把"不知道"当成 0 会让熔断静默失效 ——
            # 一个永远通不过的风控会被人发现，一个永远放行的不会。
            return RiskVerdict(
                approved=False,
                reason="日盈亏未知（今天尚无权益基准），无法判断熔断，暂不开新仓",
                rule=self.name,
            )

        if ctx.daily_pnl <= -limit:
            return RiskVerdict(
                approved=False,
                reason=f"日亏损 {abs(ctx.daily_pnl):.2f} USDT 已达熔断阈值 {limit:.2f}",
                rule=self.name,
            )
        return None


class PositionLimitRule:
    """单标的市值不得超过权益的 `max_position_pct`（ADR-028）。

    判的是**这笔买下去之后**会不会超限，而不是现在有没有超 —— 否则可以每次踩着
    线加一点，一路加过上限。只拦 BUY（减仓方向永远放行）。
    """

    name = "position_limit"

    def evaluate(self, ctx: RiskContext) -> RiskVerdict | None:
        if ctx.signal.get("action", "HOLD").upper() != "BUY":
            return None

        symbol = ctx.signal.get("symbol") or ctx.market_data.get("symbol")
        pair = split_symbol(str(symbol)) if symbol else None
        if pair is None:
            # 看不出来买的是什么标的，就算不出它占了权益多少 —— 不猜，拦下
            return RiskVerdict(
                approved=False,
                reason=f"无法识别交易对 {symbol!r} 的基础资产，判不了仓位上限",
                rule=self.name,
            )
        base, _ = pair

        if ctx.equity is None or ctx.equity <= 0:
            # 与 DailyLossRule 同一条原则：保护不成立就不要开新仓
            return RiskVerdict(
                approved=False,
                reason="拿不到权益，无法判断仓位上限，暂不开新仓",
                rule=self.name,
            )

        held = ctx.holding(base)
        # 现金资产不会出现在这个位置（base 是基础币），但留个明确的判断更省事
        if held is not None and held.asset in CASH_ASSETS:
            held = None
        if held is not None and held.value_quote is None:
            return RiskVerdict(
                approved=False,
                reason=f"{base} 已有持仓但取不到价，算不出它占权益多少，暂不开新仓",
                rule=self.name,
            )

        amount = ctx.signal.get("amount")
        try:
            notional = float(amount) if amount is not None else 0.0
        except (TypeError, ValueError):
            notional = 0.0
        # BUY 的 amount 是**计价币名义**（见 execute_agent 的 amount_kind="quote"），
        # 与 value_quote 同为 USDT，可以直接相加。
        current = held.value_quote if held is not None else 0.0
        after = current + max(notional, 0.0)
        cap = ctx.equity * ctx.params.max_position_pct

        if after > cap:
            return RiskVerdict(
                approved=False,
                reason=(
                    f"{base} 买后市值约 {after:.2f} USDT，超过权益 {ctx.equity:.2f} 的 "
                    f"{ctx.params.max_position_pct:.0%} 上限 {cap:.2f}"
                ),
                rule=self.name,
            )
        return None


class TrailingStopRule:
    """单标的从**观测高点**回落超过 `stop_loss_pct` 即视为已破线（ADR-026）。

    用哪个价：`position.price`（快照里的**实盘**价），不是 `market_data["last"]`。
    后者来自 Demo 交易所，与实盘 K 线算出的高点**不是同一个世界**，混用会得出一个
    现实中不存在的回撤。两个数必须同源（都是生产 REST），ADR-013。

    拿不到高点就等于这道保护不成立 —— 那就拦住该标的的开仓，而不是当作没跌。

    **这里只负责"拦开仓"**。破线之后的清仓是 `agent/risk/monitor.py` 的事：
    那个判定必须每轮都跑，而本规则只在有交易信号时才会被叫到。
    """

    name = "stop_loss"

    def evaluate(self, ctx: RiskContext) -> RiskVerdict | None:
        if ctx.signal.get("action", "HOLD").upper() != "BUY":
            return None

        symbol = ctx.signal.get("symbol") or ctx.market_data.get("symbol")
        pair = split_symbol(str(symbol)) if symbol else None
        if pair is None:
            return RiskVerdict(
                approved=False,
                reason=f"无法识别交易对 {symbol!r} 的基础资产，判不了止损线",
                rule=self.name,
            )
        base, _ = pair

        held = ctx.holding(base)
        if held is None or held.asset in CASH_ASSETS:
            return None  # 没有该标的的仓位，谈不上破线

        high = ctx.highs.get(base)
        if high is None or high <= 0:
            return RiskVerdict(
                approved=False,
                reason=f"取不到 {base} 的近期高点，止损线算不出来，暂不加仓",
                rule=self.name,
            )
        if held.price is None or held.price <= 0:
            return RiskVerdict(
                approved=False,
                reason=f"{base} 持仓取不到现价，判不了止损线，暂不加仓",
                rule=self.name,
            )

        drop = (high - held.price) / high
        if drop >= ctx.params.stop_loss_pct:
            return RiskVerdict(
                approved=False,
                reason=(
                    f"{base} 已从近期高点 {high:.2f} 回落 {drop:.2%}"
                    f"（止损线 {ctx.params.stop_loss_pct:.2%}）"
                ),
                rule=self.name,
            )
        return None


class DrawdownRule:
    """权益从峰值回撤超过 `max_drawdown_pct` 即熔断（ADR-029）。

    账户级判据，因此拦的是**所有**开仓，而不只是某个标的。算不出回撤同样拦住 ——
    与 `DailyLossRule` 一致：保护不成立就不要开新仓。

    只拦 BUY。破线时的清仓是另一件事（ADR-027），由 supervisor 发起，
    在这里拦 SELL 反而会让人跑不掉。
    """

    name = "drawdown"

    def evaluate(self, ctx: RiskContext) -> RiskVerdict | None:
        if ctx.signal.get("action", "HOLD").upper() != "BUY":
            return None

        if ctx.drawdown_pct is None:
            return RiskVerdict(
                approved=False,
                reason="算不出权益回撤（还没有权益峰值基准），无法判断熔断，暂不开新仓",
                rule=self.name,
            )

        if ctx.drawdown_pct >= ctx.params.max_drawdown_pct:
            peak = f"{ctx.peak_equity:.2f}" if ctx.peak_equity is not None else "未知"
            return RiskVerdict(
                approved=False,
                reason=(
                    f"权益已从峰值 {peak} 回撤 {ctx.drawdown_pct:.2%}"
                    f"（上限 {ctx.params.max_drawdown_pct:.2%}）"
                ),
                rule=self.name,
            )
        return None


# ------------------------------------------------- 破线判定的纯函数（供 monitor 复用）


def drop_from_high(price: float, high: float) -> float:
    """从高点回落的比例。高点数必须为正 —— 否则这个比例没有意义。"""
    return (high - price) / high


@dataclass(frozen=True)
class StopBreach:
    """一个已破止损线的持仓。`drop` 是"从观测高点回落了多少"，不是盈亏。"""

    asset: str
    quantity: float
    price: float
    high: float
    drop: float


def stop_breaches(
    positions: tuple[Position, ...],
    highs: Mapping[str, float | None],
    stop_loss_pct: float,
) -> tuple[StopBreach, ...]:
    """哪些持仓已破止损线。

    **只报数据齐全的**：拿不到高点或现价的持仓不出现在结果里 —— 它们不是"没破线"，
    是"判不了"。这个区别由调用方负责表达（`TrailingStopRule` 会因此拦开仓，
    `monitor` 会把它记进 notes），不能在这里被抹平成一个空结果。
    """
    out: list[StopBreach] = []
    for pos in positions:
        # 只用**正数**持仓：0 平不了；负数不是"持仓"，是欠着的。
        # 让它过去会生成一笔金额为负的卖单 —— 那在交易所是一笔买单。
        if pos.asset in CASH_ASSETS or pos.quantity <= 0:
            continue
        high = highs.get(pos.asset)
        if high is None or high <= 0:
            continue
        if pos.price is None or pos.price <= 0:
            continue
        drop = drop_from_high(pos.price, high)
        if drop >= stop_loss_pct:
            out.append(
                StopBreach(
                    asset=pos.asset,
                    quantity=pos.quantity,
                    price=pos.price,
                    high=high,
                    drop=drop,
                )
            )
    return tuple(out)


def drawdown_breached(drawdown_pct: float | None, max_drawdown_pct: float) -> bool:
    """回撤是否已越线。算不出来（None）**不算越线** —— 那是"判不了"，走另一条分支。"""
    return drawdown_pct is not None and drawdown_pct >= max_drawdown_pct
