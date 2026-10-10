"""阶段 C-2 第一层：确定性抽取（ADR-034）。

**这一层最要紧的不是"抽得准"，是"认不出来时说认不出来"。**

一个把「5 个点」当成"用户没提上限"的抽取器，会让用户拿到一个他从来没同意过的
目标 —— 而事后从库里看，那一行只有一个默认值，看不出是谁定的。
所以下面盯得最紧的是三件事：
  ① 「没看懂」与「没说」分得开（`unclear` 出口）；
  ② 百分比归到哪一类，只看它所在的**从句**，不跨标点乱认；
  ③ 同一个维度说了两个不一样的数，要报出来，不能默默后覆盖前。

全程不出网、不碰模型。
"""

from __future__ import annotations

from agent.dialogue import GoalDraft, extract

DOC_EXAMPLE = "我想用 1000 USDT 试水，两周内做到 1100，最多亏 5% 就停"


def _values(text: str):
    """大多数断言只关心抽出来的值，不关心出处 —— 给个短的。"""
    return extract(text).values


def test_the_doc_example_is_fully_understood():
    """§2.1 里那句话是这套东西的存在理由，它必须被整句读对。"""
    out = extract(DOC_EXAMPLE)
    assert out.values["stated_capital"] == 1000.0
    assert out.values["target_equity"] == 1100.0
    assert out.values["max_drawdown_pct"] == 0.05
    assert out.values["horizon_hours"] == 336.0
    assert out.values["target_return_pct"] == 0.10
    assert out.unclear == []


def test_every_value_can_say_which_clause_it_came_from():
    """ADR-019：记录得说得出"这个数是从哪句话来的"，不能只留一个孤零零的 0.05。"""
    out = extract(DOC_EXAMPLE)
    assert out.evidence["max_drawdown_pct"] == "最多亏 5% 就停"
    assert out.evidence["horizon_hours"] == "两周"
    assert out.evidence["stated_capital"] == "1000 USDT"


def test_the_derived_return_is_marked_as_derived():
    """「做到 1100」是绝对值，§2.2 的收益目标是个百分比 —— 中间那次换算是我们做的，
    复述时必须标明是算的，不能让他以为那是他亲口说的。"""
    out = extract(DOC_EXAMPLE)
    assert "target_return_pct" in out.derived
    assert "算出来" in out.evidence["target_return_pct"]


# --- ① 没看懂 ≠ 没说 ---------------------------------------------------------

def test_a_percentage_with_no_recognisable_anchor_is_reported_not_dropped():
    """**这是本层最要紧的一条。** 「承受 5 个点」里有个数，我们没看懂 ——
    报出来问一句，而不是当成"他什么都没说"。

    两种处理方式的差别在下游才显形：不报 → 用户拿到一个没人问过他上限的目标；
    报了 → 他有机会说"对，就是 5%"。
    """
    out = extract("我最多能承受 5 个点")
    assert "max_drawdown_pct" not in out.values, "没看懂就不能当成看懂了"
    assert out.unclear, "但也不能当成没说 —— 必须报出来"
    assert "5 个点" in out.unclear[0]


def test_a_sentence_with_no_numbers_reports_nothing_unclear():
    """「我想稳一点」是真的一句话也没说，那就该安安静静地走"缺维度"那条路 ——
    不能也报成"没看懂"，否则 `unclear` 这个出口会被噪声淹掉。"""
    out = extract("我想稳一点")
    assert out.values == {}
    assert out.unclear == []


def test_an_ambiguous_percentage_is_asked_about_rather_than_guessed():
    """一个从句里"赚"和"亏"都在，归不了类。**猜一半的代价是把风控线定反。**"""
    out = extract("我既想赚 10% 又不能亏 10% 以上")
    assert out.unclear, "没有唯一的判据就得问"


# --- ② 只看所在从句 -----------------------------------------------------------

def test_a_percentage_is_classified_by_its_own_clause_not_the_neighbours():
    """**这条钉的是最容易发生的一种误读。** 「赚 10%，最多亏 5%」里两个百分比，
    判据必须各看各的从句 —— 跨标点取窗口的话，前一个分句的「赚」会把 5% 也认成收益，
    于是回撤上限变成空的，而那是最危险的一个空。"""
    out = extract("两周内赚 10%，最多亏 5% 就停")
    assert out.values["target_return_pct"] == 0.10
    assert out.values["max_drawdown_pct"] == 0.05


def test_a_drawdown_phrased_after_the_number_is_still_recognised():
    """判据不能只看数字左边 —— 「5% 的回撤」也是回撤。"""
    out = extract("我不想看到超过 5% 的回撤")
    assert out.values["max_drawdown_pct"] == 0.05


# --- ③ 说了两个不一样的数 -------------------------------------------------------

def test_two_conflicting_values_are_reported_not_silently_overwritten():
    """「5%」和「8%」都说了。**默默取后一个是最坏的处理** ——
    库里只会留下一行 8%，看不出用户曾经说过 5%。"""
    out = extract("回撤最多亏 5%，不对，最多亏 8% 就停")
    assert len(out.unclear) == 1
    assert "两个不一样的值" in out.unclear[0]


def test_repeating_the_same_number_is_not_a_conflict():
    """重复一遍同一个值不是矛盾。这条防止 `unclear` 变成噪声出口。"""
    out = extract("最多亏 5% 就停，回撤就是 5%")
    assert out.values["max_drawdown_pct"] == 0.05
    assert out.unclear == []


# --- 金额与时间 ---------------------------------------------------------------

def test_a_money_after_a_target_word_is_a_target_not_capital():
    """「做到 1100」里的 1100 是目标，不是本金 —— 认反了会把收益率算成
    「要用 1000 赚到……1000」。"""
    out = extract("我准备放 2000 USDT，目标做到 3000")
    assert out.values["stated_capital"] == 2000.0
    assert out.values["target_equity"] == 3000.0


def test_thousands_separators_and_cents_survive():
    out = extract("本金 10,000 USDT，目标做到 12,500.5")
    assert out.values["stated_capital"] == 10_000.0
    assert out.values["target_equity"] == 12_500.5


def test_chinese_and_arabic_time_units_both_work():
    assert _values("做两周")["horizon_hours"] == 336.0
    assert _values("做 14 天")["horizon_hours"] == 336.0
    assert _values("做 2 周")["horizon_hours"] == 336.0
    assert _values("做半天")["horizon_hours"] == 12.0


def test_a_month_is_converted_by_convention_and_the_convention_is_visible():
    """「个月」按 30 天算是个**约定**，不是事实。数值照算，但复述里要让人看得见
    这个折算（`retell` 会同时念出小时数）。"""
    assert _values("做一个月")["horizon_hours"] == 720.0


# --- 标的 ---------------------------------------------------------------------

def test_symbols_are_recognised_from_pairs_aliases_and_chinese_names():
    assert _values("做 BTCUSDT")["symbols"] == ["BTCUSDT"]
    assert _values("做 btc")["symbols"] == ["BTCUSDT"]
    assert _values("想做比特币")["symbols"] == ["BTCUSDT"]
    assert _values("搞点以太坊")["symbols"] == ["ETHUSDT"]


def test_an_unknown_word_does_not_become_a_symbol():
    """标的认不出来就是没有 —— **不许拿默认值顶**（那会变成"拿 BTCUSDT 做
    一个用户根本没提过的市场"）。"""
    out = extract("随便做点什么，你看着办")
    assert "symbols" not in out.values


def test_symbols_do_not_duplicate():
    assert _values("BTCUSDT 和 比特币")["symbols"] == ["BTCUSDT"]


# --- 合并进草稿 ---------------------------------------------------------------

def test_extracted_values_merge_without_wiping_what_was_already_known():
    """抽取是**叠加**不是覆盖 —— 用户第二轮只说了回撤，标的不能被抹掉。"""
    draft = GoalDraft(raw_text="做 BTCUSDT")
    draft.revise(extract("做 BTCUSDT").values)
    draft.revise(extract("最多亏 5% 就停").values)
    assert draft.symbols == ["BTCUSDT"]
    assert draft.max_drawdown_pct == 0.05


def test_a_percentage_the_user_typed_gets_no_more_than_six_places():
    """用户打出比「一个点」还细的精度时，留 6 位就够 —— 下游拿它当风控线，
    位数再多是噪声不是信息。"""
    assert _values("最多亏 0.123456789%")["max_drawdown_pct"] == 0.001235


def test_a_plain_percentage_survives_the_rounding_unchanged():
    """**这条是防着上面那条 round 用力过猛。** `x / 100` 本身是精确舍入的，
    「7.5%」就该是干干净净的 `0.075`，不能被取整弄歪。

    （原先我在这里写过"`7.5 / 100` 会带出 `0.07500000000000001`"——
    实测是假的，见 `extract.py` 里 `_PCT_PLACES` 上那段。）"""
    assert _values("最多亏 7.5%")["max_drawdown_pct"] == 0.075


def test_the_derived_return_does_not_carry_a_float_tail():
    """**真正会出尾巴的是这里。** `1100 / 1000 - 1` 在浮点里等于
    `0.10000000000000009`。用户说的是「做到 1100」，落库却留下这么个数，
    事后翻记录的人会以为算错了 —— 而这是记录要能自证清白的场合。

    钉的是日后再有人"顺手"删掉那个 round 时，这条会响。
    """
    out = extract(DOC_EXAMPLE)
    assert repr(out.values["target_return_pct"]) == "0.1"
