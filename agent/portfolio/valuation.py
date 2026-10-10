"""把"账户里有什么"和"这些东西值多少"算成一个权益数（纯逻辑）。

**持仓来自交易所，价格来自实盘**（ADR-013：行情取实盘、交易走 Demo）。
两边都不是我们自己推的 —— 这一层的职责只是把它们乘起来。

原则仍然是"交易所没说的，我们不编"：**取不到价的持仓不进权益，但必须显式列出来**。
悄悄按 0 计会让权益偏低而无人察觉，那是另一种谎报。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

# 计价/现金资产：按面值 1:1 计入现金。USDC 与 USDT 并非严格等价，这里当作现金
# 是 PoC 的简化，代价是当两者脱锚时权益会有偏差 —— 记为已知近似。
CASH_ASSETS = frozenset({"USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI"})

# 小于此数量的残渣不计入持仓。交易所常留下 1e-8 量级的粉尘，
# 把它当持仓会让归因差异被噪声淹没。
DUST_THRESHOLD = 1e-8


@dataclass(frozen=True)
class Position:
    """单个资产的持仓。`value_quote` 为 None 表示**取不到价**，不是"值 0"。"""

    asset: str
    quantity: float
    price: float | None
    value_quote: float | None

    @property
    def priced(self) -> bool:
        return self.value_quote is not None


@dataclass(frozen=True)
class Valuation:
    """某一时刻的账户价值。

    `equity` **不含**取不到价的持仓，所以它是真实权益的**下界**。
    只要 `unpriced` 非空，这个数就不完整 —— 调用方有义务把它暴露出去。
    """

    cash_quote: float
    holdings_value: float
    equity: float
    positions: tuple[Position, ...] = ()
    unpriced: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default=())


def positions_from_rows(rows: Iterable[Mapping[str, Any]] | None) -> tuple[Position, ...]:
    """快照里持久化的 positions JSON → `Position` 元组。

    形状由 `service.PortfolioView.as_dict()` 决定。解析要**宽进**：库里可能留着旧版本
    写的行，缺字段的行跳过而不是让整条风控链崩掉。跳过是安全的 —— 少一个持仓会让
    仓位上限看起来更宽松，所以这里只跳**读不成**的行，读得成的照单全收。
    """
    out: list[Position] = []
    for row in rows or ():
        if not isinstance(row, Mapping):
            continue
        asset = row.get("asset")
        if not asset:
            continue
        try:
            quantity = float(row.get("quantity") or 0.0)
        except (TypeError, ValueError):
            continue
        price = _opt_float(row.get("price"))
        out.append(
            Position(
                asset=str(asset).upper(),
                quantity=quantity,
                price=price,
                value_quote=_opt_float(row.get("value_quote")),
            )
        )
    return tuple(out)


def position_to_row(pos: Position) -> dict[str, Any]:
    """`Position` → 可序列化的 dict。形状与 `positions_from_rows` **互为逆**。

    两者刻意放在一起：落库的形状和读回的形状必须是一份约定，分开写迟早会漂。
    """
    return {
        "asset": pos.asset,
        "quantity": pos.quantity,
        "price": pos.price,
        "value_quote": pos.value_quote,
    }


def positions_to_rows(positions: Iterable[Position]) -> list[dict[str, Any]]:
    return [position_to_row(p) for p in positions]


def _opt_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def value_portfolio(
    balances: Mapping[str, float],
    prices: Mapping[str, float | None],
) -> Valuation:
    """按实盘价给持仓估值。

    `balances` 是资产 → 数量；`prices` 是资产 → 计价币价格（现金资产无需提供）。
    """
    cash = 0.0
    holdings = 0.0
    positions: list[Position] = []
    unpriced: list[str] = []

    for asset, raw_qty in sorted(balances.items()):
        qty = float(raw_qty or 0.0)
        if abs(qty) < DUST_THRESHOLD:
            continue

        if asset in CASH_ASSETS:
            cash += qty
            positions.append(Position(asset=asset, quantity=qty, price=1.0, value_quote=qty))
            continue

        price = prices.get(asset)
        if price is None or price <= 0:
            # 有持仓但问不到价 —— 绝不按 0 计，而是排除并标记
            unpriced.append(asset)
            positions.append(Position(asset=asset, quantity=qty, price=None, value_quote=None))
            continue

        value = qty * float(price)
        holdings += value
        positions.append(Position(asset=asset, quantity=qty, price=float(price), value_quote=value))

    notes: list[str] = []
    if unpriced:
        notes.append(
            f"{', '.join(unpriced)} 取不到价格，未计入权益 —— 权益为下界，不是完整值"
        )

    return Valuation(
        cash_quote=cash,
        holdings_value=holdings,
        equity=cash + holdings,
        positions=tuple(positions),
        unpriced=tuple(unpriced),
        notes=tuple(notes),
    )
