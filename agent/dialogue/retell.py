"""复述与确认文本（阶段 C-2，ADR-020 的把关点）。

§2.1 步骤②是"经理复述确认"，而 ADR-020 把这个环节定成**解析出错时的唯一防线**。

**所以它是模板，不是模型调用。** 复述要念回来的全是已经定下来的东西 ——
交给模型去"组织语言"，就有机会念出用户没说过的话（凭空补一个时间窗、
把"没说本金"讲成"本金 1000"）。这里每一句都由 `GoalDraft` 里的值拼出来，
念不出来的就是因为那个值是空的。

复述必须讲清楚三件事，缺一件这个环节就白设了：

1. **生效值**，不是用户说的值。ADR-036 定了回撤线取更紧的那个 ——
   用户说 5% 而配置是 3% 时，念回"5%"就是在让他照一个不存在的前提确认。
2. **算出来的值**要标明是算的（「由 1000 和 1100 算出来」），
   别让他以为那是他亲口说的。
3. **没看懂的地方**要问。`extract.py` 里那些"看见了但归不了类"的东西，
   不问就等于丢了 —— 而用户会以为自己在那句话里已经说过了。
"""

from __future__ import annotations

from agent.config import Settings
from agent.dialogue.extract import Extract
from agent.dialogue.goal import (
    AUTONOMY_FULL,
    REQUIRED_FIELDS,
    GoalDraft,
    effective_drawdown_limit,
)

_INDENT = "  "


def _pct(value: float) -> str:
    return f"{value * 100:g}%"


def _human_hours(hours: float) -> str:
    """既说人话也说原始小时数 —— 「个月按 30 天算」是个约定不是事实，
    只念「1 个月」会把那个约定藏起来。"""
    if hours >= 168 and hours % 168 == 0:
        return f"{hours / 168:g} 周（{hours:g} 小时）"
    if hours >= 24 and hours % 24 == 0:
        return f"{hours / 24:g} 天（{hours:g} 小时）"
    return f"{hours:g} 小时"


def _line(label: str, value: str | None) -> str | None:
    return None if value is None else f"{_INDENT}{label}  {value}"


def retell(
    draft: GoalDraft,
    *,
    settings: Settings,
    extract: Extract | None = None,
) -> str:
    """把当前的目标念回去，并点名问缺的。**只念，不补。**"""
    known: list[str] = []

    #: 回撤线全是比出来的：用户说的那个、配置里那个，取更紧的生效（ADR-036）。
    #: 两个口径都留在文本里，但**总结行只放生效值** —— 见模块开头第 1 条。
    stated_drawdown = draft.max_drawdown_pct
    effective_drawdown = (
        None if stated_drawdown is None
        else effective_drawdown_limit(settings, stated_drawdown)
    )
    #: 只在**配置赢**的时候提醒。用户比配置更紧时，生效值就是他说的那个 ——
    #: 他听到什么就执行什么，没有"你以为的和实际的不一样"这回事，说了反而添乱。
    config_is_tighter = (
        effective_drawdown is not None
        and abs(effective_drawdown - stated_drawdown) > 1e-12
    )

    if draft.symbols:
        known.append(_line("标的范围", "、".join(draft.symbols)))
    #: 用户说「做到 1100」时，念的就是这个数本身（ADR-040）。只念百分比会把
    #: 他的目标换成一个他没同意过的换算值；两者都在时两个都念，让差异可见。
    if draft.target_equity is not None:
        known.append(_line("目标权益", f"做到 {draft.target_equity:g} USDT"))
    if draft.target_return_pct is not None:
        note = ""
        if extract is not None and "target_return_pct" in extract.derived:
            target = draft.target_equity
            capital = draft.stated_capital
            if target is not None and capital is not None:
                note = f"（由你说的「{capital:g}」和「{target:g}」算出来）"
        known.append(_line("收益目标", f"+{_pct(draft.target_return_pct)}{note}"))
    if effective_drawdown is not None:
        # 念**生效值**，不念用户说的那个。总结行是大家真会读的那一行 ——
        # 它写 5% 而实际是 3%，就是让他照一个不存在的前提往下看（模块开头第 1 条）。
        # 用户说的那个数不丢，落在下面"要说清楚的是"里讲。
        known.append(_line("回撤上限", f"亏到 {_pct(effective_drawdown)} 就停"))
    if draft.horizon_hours is not None:
        known.append(_line("时间窗", _human_hours(draft.horizon_hours)))
    if draft.stated_capital is not None:
        known.append(_line("本金", f"你说的是 {draft.stated_capital:g} USDT"))

    lines = ["你说的目标是：", ""]
    lines += [x for x in known if x] or [f"{_INDENT}（还什么都没说）"]

    # ---- 必须说清楚的两件事 ----
    caveats: list[str] = []
    if config_is_tighter:
        caveats.append(
            f"**上面那条回撤线是 {_pct(effective_drawdown)}，不是你说的 "
            f"{_pct(stated_drawdown)}** —— 两边取更紧的那个（ADR-036）。"
        )
    if draft.stated_capital is not None:
        caveats.append(
            f"收益率按**账户实际权益**算，不是你口述的 {draft.stated_capital:g}"
            "（ADR-033）—— 这两个数不一定一样。差多少，确认时我读一次账户告诉你。"
        )
    if draft.autonomy == AUTONOMY_FULL:
        caveats.append(
            "授权是**全自动**（ADR-018）：确认之后执行期间我不再回头问你。"
        )
    if caveats:
        lines += ["", "要说清楚的是："]
        lines += [f"{_INDENT}· {c}" for c in caveats]

    # ---- 没看懂的地方：先问这个，再报缺的 ----
    #
    # 顺序是有意的。「5 个点」那种数我们归不了类，而「回撤上限」这会儿也确实是空的 ——
    # 如果先念"回撤上限你还没说"，用户会读成"我刚说了啊"，然后怀疑这东西没在听。
    # 先把"我看见一个 5，但不知道它算哪一档"摆出来，再列还缺什么，那句话才读得通。
    has_unclear = extract is not None and bool(extract.unclear)
    if has_unclear:
        lines += ["", "有地方我没看懂，你确认一下："]
        lines += [f"{_INDENT}· {item}" for item in extract.unclear]

    # ---- 缺的维度：点名问，不填默认值 ----
    missing = draft.missing()
    if missing:
        header = "还没定下来的（这些我不会替你填）："
        if has_unclear:
            header = "还没定下来的（上面那个要是其中之一，说一声就行）："
        lines += ["", header]
        for attr, label in REQUIRED_FIELDS:
            if label in missing:
                lines.append(f"{_INDENT}· {label} —— {_ASK.get(attr, '说一下')}")

    # ---- 收尾 ----
    lines += [""]
    if missing or has_unclear:
        lines.append("补齐之后我再念一遍；都对的话回一句确认。")
    else:
        lines.append(
            "都对的话回一句确认，我就把它锁成 active。"
            "之后要改，得先中止这个目标重新提（ADR-021）。"
        )
    return "\n".join(lines)


#: 缺某个维度时具体问什么。比光报一个"标的范围"有用 ——
#: 用户得知道我们想要的是一个什么样的话。
_ASK = {
    "symbols": "想做哪个交易对，比如 BTCUSDT",
    "target_return_pct": "做到多少算达成，比如「赚 10%」或者「做到 1100」",
    "max_drawdown_pct": "亏到多少就停",
    "horizon_hours": "打算做多久，比如「两周」",
}
