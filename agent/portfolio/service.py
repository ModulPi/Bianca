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
from agent.storage.repository import EquityRepository, OrderRepository

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
) -> PortfolioView:
    """纯合成：余额 + 价格 + 订单账本 → 一张视图。无 I/O。"""
    valuation = value_portfolio(balances, prices)
    attributed, unmapped = attributed_holdings(orders)
    gaps = compare(_as_floats(balances), attributed)
    return PortfolioView(valuation=valuation, gaps=tuple(gaps), unmapped_symbols=tuple(unmapped))


def _as_floats(balances: Mapping[str, float]) -> dict[str, float]:
    return {str(k): float(v or 0.0) for k, v in balances.items()}


def price_assets(balances: Mapping[str, float]) -> list[str]:
    """需要取价的资产：持有的、非现金的。"""
    return [a for a, q in balances.items() if q and abs(float(q)) > 0 and a not in CASH_ASSETS]


async def capture(settings: Settings | None = None) -> PortfolioView:
    """拉余额与实盘价，合成快照并落库。**唯一有 I/O 的入口。**"""
    cfg = settings or get_settings()
    exchange = await _fetch_balance(cfg)
    prices = await fetch_prices(price_assets(exchange), settings=cfg)
    orders = await OrderRepository().list_recent(limit=1000)

    view = build_view(exchange, prices, orders)
    repo = EquityRepository()
    notes = list(view.valuation.notes)

    # 账外持仓的**变化**才是信号（见 attribution.unexplained_drift）。
    # 首次快照没有基准，此时整账户都是"账外"，那是期初余额，不是异常。
    previous = await repo.latest()
    if previous is None:
        notes.append("首次快照：全部未归因持仓视为期初余额，后续只报其变化")
    else:
        before = {
            g["asset"]: float(g.get("unexplained_qty") or 0.0)
            for g in json.loads(previous.gaps or "[]")
        }
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
