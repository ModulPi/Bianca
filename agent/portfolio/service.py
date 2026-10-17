"""把持仓、价格、账本拼成一张权益快照。

这里只做**编排**：三个来源（交易所余额、实盘价、本地订单账本）各由别的模块负责，
本模块把它们合到一起并落库。合成本身是纯函数（`build_view`），所以整条链
可以在没有任何网络的情况下被测。

来源与可信度不一样，必须分清：

  · 余额 ← 交易所，权威
  · 价格 ← 生产实盘，权威
  · 归因 ← 我们自己的账本，**不权威**，只解释变动
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, replace

from agent.config import Settings, get_settings
from agent.portfolio.attribution import (
    HoldingGap,
    attributed_holdings,
    compare,
    unexplained_drift,
)
from agent.portfolio.prices import fetch_prices
from agent.portfolio.valuation import CASH_ASSETS, Valuation, value_portfolio
from agent.storage.repository import (
    AttributionBaselineRepository,
    EquityRepository,
    OrderRepository,
)

logger = logging.getLogger(__name__)

# 余额拉取的重试参数。见 `_fetch_balance` 的说明。
_BALANCE_ATTEMPTS = 3
_BALANCE_BACKOFF_S = 1.5


@dataclass(frozen=True)
class PortfolioView:
    """一次快照的全部内容。

    `captured_at` 只有落库之后才知道，所以 `build_view()` 留 None，
    由 `capture()` 补上 —— 合成是纯的，时间戳属于持久化。
    """

    valuation: Valuation
    gaps: tuple[HoldingGap, ...]
    unmapped_symbols: tuple[str, ...]
    captured_at: str | None = None

    @property
    def unexplained(self) -> tuple[HoldingGap, ...]:
        """账外持仓：交易所有的而账本解释不了的。"""
        return tuple(g for g in self.gaps if not g.explained)

    def as_dict(self) -> dict:
        return {
            "captured_at": self.captured_at,
            "equity": self.valuation.equity,
            "cash_quote": self.valuation.cash_quote,
            "holdings_value": self.valuation.holdings_value,
            "unpriced": list(self.valuation.unpriced),
            "positions": [
                {
                    "asset": p.asset,
                    "quantity": p.quantity,
                    "price": p.price,
                    "value_quote": p.value_quote,
                }
                for p in self.valuation.positions
            ],
            "gaps": [
                {
                    "asset": g.asset,
                    "exchange_qty": g.exchange_qty,
                    "attributed_qty": g.attributed_qty,
                    "baseline_qty": g.baseline_qty,
                    "unexplained_qty": g.unexplained_qty,
                }
                for g in self.gaps
            ],
            "unmapped_symbols": list(self.unmapped_symbols),
            "notes": list(self.valuation.notes),
        }


def build_view(
    balances: Mapping[str, float],
    prices: Mapping[str, float | None],
    orders: Iterable[object],
    baseline: Mapping[str, float] | None = None,
) -> PortfolioView:
    """纯合成：余额 + 价格 + 订单账本（+ 期初基准）→ 一张视图。无 I/O。

    `baseline` 是 agent 上线前就存在的持仓（ADR-042）。它和账本分开传，
    因为两者来源不同：账本是我们写的，基准是第一次看账户时记下的。
    """
    valuation = value_portfolio(balances, prices)
    attributed, unmapped = attributed_holdings(orders)
    gaps = compare(_as_floats(balances), attributed, dict(baseline or {}))
    return PortfolioView(valuation=valuation, gaps=tuple(gaps), unmapped_symbols=tuple(unmapped))


def _as_floats(balances: Mapping[str, float]) -> dict[str, float]:
    return {str(k): float(v or 0.0) for k, v in balances.items()}


def _adjusted_unexplained(
    gaps: Iterable[dict], baseline: Mapping[str, float]
) -> dict[str, float]:
    """把历史快照的账外差额换算到**同一基准**上，供 drift 比较。

    ADR-042 之前的快照存的是裸差额（交易所 − 账本），基准是后来才记的；
    不补这一刀，基线建立后的第一张快照会报出一整串"账外减少"的假警报。

    新快照的 gap 带 `baseline_qty`，说明 `unexplained_qty` 已经是相对基准的，
    不能再减一次 —— 用**列在不在**判断，而不是按时间戳猜。
    """
    out: dict[str, float] = {}
    for g in gaps:
        asset = str(g.get("asset") or "")
        qty = float(g.get("unexplained_qty") or 0.0)
        if "baseline_qty" not in g:
            qty -= float(baseline.get(asset, 0.0))
        out[asset] = qty
    return out


def price_assets(balances: Mapping[str, float]) -> list[str]:
    """需要取价的资产：持有的、非现金的。"""
    return [a for a, q in balances.items() if q and abs(float(q)) > 0 and a not in CASH_ASSETS]


async def capture(settings: Settings | None = None) -> PortfolioView:
    """拉余额与实盘价，合成快照并落库。**唯一有 I/O 的入口。**"""
    cfg = settings or get_settings()
    exchange = await _fetch_balance(cfg)
    prices = await fetch_prices(price_assets(exchange), settings=cfg)
    orders = await OrderRepository().list_recent(limit=1000)

    repo = EquityRepository()

    # 期初持仓：账户在 agent 上线前就有的钱，账本永远解释不了（ADR-042）。
    # 第一次采集时记进 `attribution_baseline`，此后所有快照都相对它算账外。
    baseline_repo = AttributionBaselineRepository()
    baseline = await baseline_repo.load()
    seeded = False
    if not baseline:
        first = await repo.first()
        if first is not None:
            # 线上库在 ADR-042 之前就采过快照 —— 用最早那张的账外差额当基准，
            # 这就是"把之前的数据同步进来"：不重写历史行，只把起点记下来。
            quantities = {
                g["asset"]: float(g.get("unexplained_qty") or 0.0)
                for g in json.loads(first.gaps or "[]")
            }
            source = f"期初：最早一张权益快照（{first.captured_at}）"
        else:
            raw = build_view(exchange, prices, orders)
            quantities = {g.asset: g.unexplained_qty for g in raw.gaps}
            source = "期初：本账户第一张权益快照"
        baseline = await baseline_repo.seed(quantities, source=source)
        seeded = bool(baseline)

    view = build_view(exchange, prices, orders, baseline=baseline)
    notes = list(view.valuation.notes)
    if seeded:
        detail = ", ".join(f"{asset} {qty:g}" for asset, qty in sorted(baseline.items()))
        notes.append(
            f"期初持仓已记为归因基准（ADR-042）：{detail}；"
            "此后只报相对这个基准的变化，不再把它们算作账外"
        )

    # 账外持仓的**变化**才是信号（见 attribution.unexplained_drift）。
    previous = await repo.latest()
    if previous is None:
        notes.append("首次快照：账外持仓以本次采集为起点，后续只报其变化")
    elif not seeded:
        before = _adjusted_unexplained(json.loads(previous.gaps or "[]"), baseline)
        drift = unexplained_drift(before, view.gaps)
        if drift:
            detail = ", ".join(f"{asset} {delta:+.8f}" for asset, delta in drift)
            notes.append(f"账外持仓发生变动（非本 agent 造成）：{detail}")

    # 切不出 base 的 symbol 无处安放，但也不能丢 —— 它意味着有一笔成交
    # 完全没进归因。折进 notes，让它在快照里留下痕迹。
    if view.unmapped_symbols:
        notes.append(
            f"这些 symbol 无法切分基础币，未参与归因：{', '.join(view.unmapped_symbols)}"
        )
    row = await repo.save(
        cash_quote=view.valuation.cash_quote,
        holdings_value=view.valuation.holdings_value,
        equity=view.valuation.equity,
        positions=[
            {
                "asset": p.asset,
                "quantity": p.quantity,
                "price": p.price,
                "value_quote": p.value_quote,
            }
            for p in view.valuation.positions
        ],
        unpriced=list(view.valuation.unpriced),
        gaps=[
            {
                "asset": g.asset,
                "exchange_qty": g.exchange_qty,
                "attributed_qty": g.attributed_qty,
                "baseline_qty": g.baseline_qty,
                "unexplained_qty": g.unexplained_qty,
            }
            for g in view.gaps
        ],
        notes=notes,
    )
    # 返回的视图必须带上**刚存进去的**那些 notes，否则 POST 与 GET 会说两套话。
    return replace(
        view,
        valuation=replace(view.valuation, notes=tuple(notes)),
        captured_at=row.captured_at,
    )


async def _fetch_balance(
    settings: Settings,
    *,
    attempts: int = _BALANCE_ATTEMPTS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, float]:
    """拉余额，带退避重试。

    实测过：`load_markets()` 偶尔会 `RequestTimeout`（代理抖动），一次失败就
    让整张快照拿不到数。行情模块早就为同一个毛病加了重试（见行情设计 §风险），
    这里沿用同一套做法。
    """
    from agent.exchange.spot_demo import SpotDemoExchange

    last: Exception | None = None
    for attempt in range(attempts):
        try:
            async with SpotDemoExchange(settings) as demo:
                balance = await demo.fetch_balance()
            return {k: float(v) for k, v in (balance.get("total") or {}).items() if v}
        except Exception as exc:  # noqa: BLE001 — 重试由这一层负责
            last = exc
            if attempt < attempts - 1:
                await sleep(_BALANCE_BACKOFF_S * (2**attempt))
    raise last  # type: ignore[misc] — attempts >= 1，循环至少跑过一次
