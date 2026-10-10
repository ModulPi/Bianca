"""确定性抽取：把一句话里**认得出**的数字抠出来（阶段 C-2 第一层，ADR-034）。

**为什么数字不交模型。** ADR-034 定的是"金额、百分比、时长走确定性抽取"。理由很
直接：数字读错了是**硬错**。模型把「最多亏 5%」读成 5 倍，下游没有任何东西能拦住
它 —— 而风控线正是最经不起读错的那个数。所以这几个数不放模型手里。

**这一层最要紧的规矩：只认它认识的说法。**

「认不出来」和「用户没说」是两件事，绝不能混：

  · 「我想稳一点」—— 没有数字。这是**用户没说**，该按空处理。
  · 「最多承受 5 个点」—— 有个数，但「个点」不在词表里。这是**我们没看懂**，
    该回问一句，而不是当成"他什么都没说"然后拿别的数去顶。

混掉的后果是：一个说了「5 个点」的用户，最后拿到的是一个没人问过他上限的目标。
所以这里有个 `unclear` 出口，专门收集"看见了但归不了类"的东西，
交给复述（`retell.py`）去问。

抽出来的每个值都带**原句片段**（`evidence`）。这是 ADR-019 的要求：记录要说得出
"这个数是从哪句话来的"，而不是只留一个孤零零的 `0.05`。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

__all__ = ["Extract", "extract", "SYMBOL_ALIASES"]


# --- 词表 -------------------------------------------------------------------
#
# 这三个词表**故意做得很小**：它们是判据，不是猜测。「回撤」「亏」认得出，
# 「承受能力有限」认不出 —— 认不出的走 unclear 出口被问回来，比硬猜安全。
# 词表靠实测往里加，不靠想象。

#: 说这个百分比是"下跌方向"的。
_DRAWDOWN_WORDS = ("回撤", "亏", "止损", "损失", "跌", "输")

#: 说这个百分比是"上涨方向"的。
_PROFIT_WORDS = ("赚", "盈利", "收益", "回报", "涨", "赢", "挣", "做到", "翻")

#: 金额出现在这些词之后就**不是本金**，是目标 —— 「做到 1100」里的 1100。
_TARGET_MONEY_WORDS = ("做到", "赚到", "挣到", "变成", "翻到")

#: 标的别名。**故意只有这么几个。** 加新的要看实测里用户真说了什么，
#: 不要凭想象把整张币安列表抄进来 —— 抄错了会把「比特币」之外的话也认成标的。
SYMBOL_ALIASES: dict[str, str] = {
    "比特币": "BTCUSDT",
    "大饼": "BTCUSDT",
    "bitcoin": "BTCUSDT",
    "btc": "BTCUSDT",
    "以太坊": "ETHUSDT",
    "以太": "ETHUSDT",
    "ethereum": "ETHUSDT",
    "eth": "ETHUSDT",
    "索拉纳": "SOLUSDT",
    "solana": "SOLUSDT",
    "sol": "SOLUSDT",
    "狗狗币": "DOGEUSDT",
    "doge": "DOGEUSDT",
    "币安币": "BNBUSDT",
    "瑞波": "XRPUSDT",
}

#: 直接写全的现货对，如 `BTCUSDT`。**只认 USDT 计价** —— 本项目就是 USDT 本位，
#: 认别的计价会把 `BTCBUSD` 也吃进来，而下游没有任何地方处理得了它。
_PAIR_RE = re.compile(r"(?<![A-Za-z])([A-Z]{2,10}USDT)(?![A-Za-z])")

_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[%％]")
_MONEY_RE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s*(USDT|USDC|USD|usdt|usdc|usd|美元|美刀|刀|块|U)(?![A-Za-z])"
)
_TARGET_MONEY_RE = re.compile(
    r"(?:" + "|".join(_TARGET_MONEY_WORDS) + r")\s*(\d[\d,]*(?:\.\d+)?)"
)

#: 一个裸数字。**它是"最后一道网"，不是抽取规则** —— 所有正经规则都跑完之后，
#: 还剩下没被认领的数字，就是"看见了但归不了类"的。见 `extract()` 末尾。
_BARE_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

_CN_DIGITS = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "半": 0.5,
}

#: 时长 → 小时。**「个月」按 30 天算**，这是个约定不是事实 —— 所以它进了复述，
#: 用户看到的是「约 720 小时（按 30 天一个月折算）」而不是一个伪装成精确的值。
_UNIT_HOURS: tuple[tuple[str, float], ...] = (
    ("个星期", 168.0), ("个礼拜", 168.0),
    ("星期", 168.0), ("礼拜", 168.0), ("周", 168.0),
    ("个月", 720.0), ("月", 720.0),
    ("小时", 1.0), ("钟头", 1.0),
    ("天", 24.0), ("日", 24.0),
)
_TIME_RE = re.compile(
    r"(\d+(?:\.\d+)?|[零一二两三四五六七八九十半])\s*("
    + "|".join(unit for unit, _ in _UNIT_HOURS)
    + r")"
)

#: 从句边界。窗口不跨这些标点 —— 否则「赚了 10%，最多亏 5% 就停」里前一个分句的
#: 「赚」会被算成后一个百分比的判据。
_CLAUSE_BREAK = "，。；！？、\n,;!?"


@dataclass
class Extract:
    """一句话里抽出来的东西。

    `values` 直接就是 `GoalDraft` 的字段名，可以交给 `GoalDraft.revise()` 合并。
    """

    values: dict[str, Any] = field(default_factory=dict)
    #: 字段名 → 原句片段。ADR-019：记录得说得出"这个数从哪句话来的"。
    evidence: dict[str, str] = field(default_factory=dict)
    #: **看见了但归不了类的东西。** 这些必须被问，不能被当成"用户没说"。
    unclear: list[str] = field(default_factory=list)
    #: 抽出来了、但是算出来的（不是用户直接说的）。复述时要说清楚是算的。
    derived: list[str] = field(default_factory=list)


def _clause(text: str, start: int, end: int) -> str:
    """命中片段所在的那个从句。不跨标点，免得把别处的判据拉过来。"""
    left = start
    while left > 0 and text[left - 1] not in _CLAUSE_BREAK:
        left -= 1
    right = end
    while right < len(text) and text[right] not in _CLAUSE_BREAK:
        right += 1
    return text[left:right]


#: 百分比保留几位。**两个地方靠它，都是真靠得住的那两个：**
#:
#:   · 用户自己打出来的精度 —— 「0.123456789%」这种，6 位（十万分之一个点）远够用；
#:   · 换算出来的值 —— `1100 / 1000 - 1` 在浮点里是 `0.10000000000000009`，
#:     不取整就把这个数落库了，事后看记录的人会以为算错了。
#:
#: **顺便记一句被证伪的话**：原先这里的注释写的是"`7.5 / 100` 会带出
#: `0.07500000000000001` 这种尾巴"。实测不成立 —— `x / 100` 和十进制字面量
#: 都按最近舍入，对用户说得出嘴的百分比（一位小数）两者总相等。
#: 所以用户直接说的百分比并不需要这个 round；真正需要它的是上面第二条。
_PCT_PLACES = 6


def _number(raw: str) -> float:
    return float(raw.replace(",", "").rstrip("."))


def _to_pct(raw: str) -> float:
    return round(_number(raw) / 100, _PCT_PLACES)


class _Collector:
    """把同一个字段的多次出现收在一起 —— **两次说得不一样时要报出来**，
    不能默默地后面覆盖前面。"""

    def __init__(self, extract: Extract) -> None:
        self._extract = extract

    def put(self, name: str, value: Any, source: str) -> None:
        current = self._extract.values.get(name)
        if current is not None and current != value:
            self._extract.unclear.append(
                f"「{name}」说过两个不一样的值（{current} 和 {value}），不知道以哪个为准"
            )
            return
        self._extract.values[name] = value
        self._extract.evidence[name] = source


def _classify_percent(text: str, match: re.Match[str]) -> str:
    """这个百分比是回撤还是收益。**两个都像或都不像 → 说不知道。**

    判据只看它所在的那个从句，不跨标点 —— 否则「赚 10%，最多亏 5%」里的
    「赚」会把 5% 也认成收益，而这正是最容易发生的一种误读。
    """
    around = _clause(text, match.start(), match.end())
    is_drawdown = any(w in around for w in _DRAWDOWN_WORDS)
    is_profit = any(w in around for w in _PROFIT_WORDS)
    if is_drawdown and not is_profit:
        return "max_drawdown_pct"
    if is_profit and not is_drawdown:
        return "target_return_pct"
    return ""


def extract(text: str) -> Extract:
    """从一句话里抽出目标维度。**认不出来的进 `unclear`，不硬猜。**"""
    out = Extract()
    collect = _Collector(out)

    def _claimed(start: int) -> bool:
        return any(s <= start < e for s, e in claimed)

    #: 已经被某条规则认领的字符区间。跑完所有规则后还剩下的数字就是"没看懂"的。
    claimed: list[tuple[int, int]] = []

    # ---- 目标金额（先做：它的片段要从金额里排除掉）----
    for match in _TARGET_MONEY_RE.finditer(text):
        claimed.append((match.start(), match.end()))
        collect.put("target_equity", _number(match.group(1)), match.group(0))

    # ---- 百分比 ----
    for match in _PCT_RE.finditer(text):
        claimed.append((match.start(), match.end()))
        field_name = _classify_percent(text, match)
        pct = _to_pct(match.group(1))
        source = _clause(text, match.start(), match.end())
        if field_name:
            collect.put(field_name, pct, source)
        else:
            out.unclear.append(
                f"看到一个「{match.group(0)}」，但看不出它是收益目标还是回撤上限"
                f"（原句：{source.strip()}）"
            )

    # ---- 本金：带单位的金额，且不属于上面那些目标金额 ----
    for match in _MONEY_RE.finditer(text):
        if _claimed(match.start()):
            continue
        claimed.append((match.start(), match.end()))
        collect.put("stated_capital", _number(match.group(1)), match.group(0))

    # ---- 时间窗 ----
    for match in _TIME_RE.finditer(text):
        if _claimed(match.start()):
            continue
        claimed.append((match.start(), match.end()))
        raw, unit = match.group(1), match.group(2)
        amount = float(raw) if raw[0].isdigit() else float(_CN_DIGITS.get(raw, 0))
        hours = next(h for u, h in _UNIT_HOURS if u == unit)
        collect.put("horizon_hours", amount * hours, match.group(0))

    # ---- 标的 ----
    symbols: list[str] = []
    for match in _PAIR_RE.finditer(text):
        if match.group(1) not in symbols:
            symbols.append(match.group(1))
    if not symbols:
        lowered = text.lower()
        for alias, symbol in SYMBOL_ALIASES.items():
            if alias in lowered and symbol not in symbols:
                symbols.append(symbol)
    if symbols:
        collect.put("symbols", symbols, "、".join(symbols))

    # ---- 落网之鱼：看见了、但一条规则都没用上的数字 ----
    #
    # **这就是「认不出来」的出口。** 「最多承受 5 个点」里的 5 走完了上面每一条都
    # 认领不了它，于是落在这里被问回去 —— 而不是当作"用户没提上限"。
    #
    # 故意**不加"附近得有风控词"这种过滤**：加过滤就等于在说"离风控词远的数字
    # 可以当不存在"，而「5 个点」那句里恰好一个风控词都没有。宁可多问一句，
    # 也不能让一个说过的数悄悄消失 —— 那正是这一层存在的理由。
    for match in _BARE_NUMBER_RE.finditer(text):
        if _claimed(match.start()):
            continue
        source = _clause(text, match.start(), match.end())
        out.unclear.append(
            f"看到一个「{match.group(0)}」，但认不出它说的是哪个维度"
            f"（原句：{source.strip()}）"
        )

    # ---- 算出来的收益目标 ----
    #
    # 「用 1000 USDT 试水，两周内做到 1100」里的 1100 是个**绝对数**，
    # 而 §2.2 的收益目标是个百分比。两个都记着（ADR-033 同样的路子：
    # 口径有两个就都留着），然后**用用户自己给的两个数**换算一个百分比出来。
    #
    # 注意这只用用户说的数，不碰账户实际权益 —— 两者不一致是常事（ADR-033），
    # 那件事归复述去讲，不归这里悄悄换算。
    if "target_return_pct" not in out.values:
        capital = out.values.get("stated_capital")
        target = out.values.get("target_equity")
        if isinstance(capital, float) and isinstance(target, float) and capital > 0:
            out.values["target_return_pct"] = round(target / capital - 1, _PCT_PLACES)
            out.evidence["target_return_pct"] = f"由「{capital:g}」和「{target:g}」算出来"
            out.derived.append("target_return_pct")

    return out
