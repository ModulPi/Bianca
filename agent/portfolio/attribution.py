"""把"这些持仓里哪些是我们造成的"从订单账本推出来（纯逻辑）。

这个问题必须和估值分开问。交易所只知道**现在有什么**，不知道**是谁弄的**；
后者只能由我们自己的账本回答 —— 而我们的账本天然是残缺的：账户在 agent 上线
之前就有余额，中间还可能有人工操作。

所以这一层不产出"持仓"，只产出**归因差异**：交易所的数与自己账本的数对不上多少。
对不上的部分应当被如实列出来（"账外持仓"），而不是被抹平成一个"正确的持仓数"。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

# 计价资产，用来把 `BTCUSDT` 切成 `BTC`。顺序有意义：先长后短，
# 否则 `USDT` 会被 `USD` 抢先匹配成 `BTCUSD` + `T`。
_QUOTE_ASSETS = ("USDT", "FDUSD", "BUSD", "TUSD", "USDC", "DAI", "BTC", "ETH", "BNB", "USD")

# 只有落定的成交才归因。`unknown` **刻意不计** —— 它可能已经成交了，
# 但也可能没有，按成交计入等于编数；不计入则会让差异里多出这一笔，
# 这恰好是我们希望被人看见的。
_ATTRIBUTABLE_STATUSES = frozenset({"filled", "partial"})


def split_symbol(symbol: str) -> tuple[str, str] | None:
    """`BTCUSDT` → `('BTC', 'USDT')`。切不出来返回 None，**不猜**。"""
    text = (symbol or "").strip().upper()
    for quote in _QUOTE_ASSETS:
        if text.endswith(quote) and len(text) > len(quote):
            return text[: -len(quote)], quote
    return None


@dataclass(frozen=True)
class HoldingGap:
    """一个资产上，交易所的数与账本（含期初基准）的数之间的差。

    `baseline_qty` 是 **agent 上线前就躺在账户里的数量**（ADR-042）。它不来自
    订单账本，而是第一次快照时记下的事实 —— 没有它，这些钱会永远被算成
    "账外"，而一个永远满格的告警等于没有告警。

    三者各归各位：交易所说的 = 账本解释的 + 期初就有的 + 真正解释不了的。
    """

    asset: str
    exchange_qty: float
    attributed_qty: float
    baseline_qty: float = 0.0

    @property
    def unexplained_qty(self) -> float:
        """真正账外的数量。正 = 账户里有不属于我们的；负 = 我们记了但账户里没有。"""
        return self.exchange_qty - self.attributed_qty - self.baseline_qty

    @property
    def explained(self) -> bool:
        return abs(self.unexplained_qty) < 1e-8


def attributed_holdings(orders: Iterable[object]) -> tuple[dict[str, float], list[str]]:
    """从订单账本推出"我们造成的净持仓变动"（基础币与计价币**两边都算**）。

    只算基础币的话，计价币那一格永远是"交易所有一大笔、账本里是零"，无法解释。
    把买卖的计价币流向也记上，那一格的残差才等于**有意义的**东西：
    上线前的余额 + 手续费 + 人工操作。

    返回 (资产 → 净数量, 无法归因的 symbol 列表)。切不出 base 的 symbol 会进
    第二个列表，**不计入任何资产** —— 归错地方比不归更糟。

    口径说明：手续费未计入。币安现货的买单手续费常以**基础币**收取
    （实测：买 BTC 时 fee 币种为 BTC），所以这里的归因数量会比实际略高，
    差额会落进"账外"里。这是已知偏差，不在这里臆造修正。
    """
    holdings: dict[str, float] = {}
    unmapped: list[str] = []

    for order in orders:
        status = str(getattr(order, "status", "") or "").lower()
        if status not in _ATTRIBUTABLE_STATUSES:
            continue
        filled = getattr(order, "filled_qty", None)
        if not filled:
            continue

        split = split_symbol(str(getattr(order, "symbol", "") or ""))
        if split is None:
            unmapped.append(str(getattr(order, "symbol", "")))
            continue
        base, quote = split

        side = str(getattr(order, "side", "") or "").lower()
        is_buy = side == "buy"
        base_delta = float(filled) if is_buy else -float(filled)

        # 计价币只在花掉/收回的金额已知时记账。缺 cost_quote 就不编一个数出来，
        # 代价是那一格的账外残差偏大 —— 可见的偏差好过不可见的编造。
        cost = getattr(order, "cost_quote", None)
        quote_delta = None if cost is None else (-float(cost) if is_buy else float(cost))

        holdings[base] = holdings.get(base, 0.0) + base_delta
        if quote_delta is not None:
            holdings[quote] = holdings.get(quote, 0.0) + quote_delta

    return holdings, sorted(set(unmapped))


def compare(
    exchange_balances: dict[str, float],
    attributed: dict[str, float],
    baseline: dict[str, float] | None = None,
) -> list[HoldingGap]:
    """逐资产对比交易所持仓与（账本 + 期初基准），**包含只在一边出现的资产**。

    只在一侧出现的资产正是最该被看见的：账户里凭空有的，或账本里记着而
    账户里没有的（后者通常意味着成交没落库或对账漏了）；期初有而现在没了
    同样是信号（说明在我们之外被动过）。

    `baseline` 是期初基准（ADR-042）。缺省为空 = 全部按账外算，兼容旧调用方。
    """
    base = baseline or {}
    assets = sorted(set(exchange_balances) | set(attributed) | set(base))
    return [
        HoldingGap(
            asset=asset,
            exchange_qty=float(exchange_balances.get(asset, 0.0)),
            attributed_qty=float(attributed.get(asset, 0.0)),
            baseline_qty=float(base.get(asset, 0.0)),
        )
        for asset in assets
    ]


def unexplained_drift(
    previous: dict[str, float],
    current: Iterable[HoldingGap],
    *,
    tolerance: float = 1e-8,
) -> list[tuple[str, float]]:
    """账外持仓的**变化量**，而不是账外持仓本身。

    裸的账外差额几乎没有信号：账户在 agent 上线前就有余额，第一次快照时
    100% 的资产都是"账外"，这个数字永远满格，看久了就没人看。真正意味着
    "出事了"的是它**变了** —— 有东西在我们账本之外动过钱。

    返回 [(资产, 变化量)]，只含超过 `tolerance` 的。`previous` 是上一次快照的
    asset → unexplained_qty。
    """
    out: list[tuple[str, float]] = []
    for gap in current:
        delta = gap.unexplained_qty - float(previous.get(gap.asset, 0.0))
        if abs(delta) > tolerance:
            out.append((gap.asset, delta))
    return out
