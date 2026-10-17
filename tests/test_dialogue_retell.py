"""复述与确认（阶段 C-2，ADR-020 的把关点）。

复述是**解析出错时的唯一防线**，所以它得满足两件事：

  ① **只念不补** —— 念出来的每一句都是草稿里真有的值。一个顺手补上的时间窗，
     会让用户在"我确认过了"的印象里拿到一个他没说过的东西。所以这里既钉
     "该念的念了"，也钉"不该念的一句都没有"。
  ② **念生效值，不是原话** —— ADR-036 定了回撤线取更紧的那个。用户说 5%
     而配置是 3% 时，念回"5%"就是在让他照一个不存在的前提确认。

复述是模板不是模型调用，所以这些全都能离线钉死。
"""

from __future__ import annotations

from agent.config import Settings
from agent.dialogue import GoalDraft, extract, retell


def _settings(**overrides) -> Settings:
    """默认回撤线 5%（和配置里的一样），要制造"取更紧的"就把配置调紧。"""
    return Settings(_env_file=None, **overrides)


def _draft(**kwargs) -> GoalDraft:
    text = kwargs.pop("raw_text", "（测试用）")
    return GoalDraft(raw_text=text, **kwargs)


def _full(**kwargs) -> GoalDraft:
    """一个四个维度都齐了的草稿 —— 用来测"念得对不对"，不掺"缺维度"的干扰。"""
    base = dict(
        symbols=["BTCUSDT"],
        target_return_pct=0.10,
        max_drawdown_pct=0.05,
        horizon_hours=336.0,
    )
    base.update(kwargs)
    return _draft(**base)


# --- ① 只念不补 ---------------------------------------------------------------

def test_the_retelling_names_every_dimension_it_has():
    text = retell(_full(), settings=_settings())
    assert "BTCUSDT" in text
    assert "+10%" in text
    assert "5%" in text
    assert "336" in text


def test_the_retelling_does_not_invent_a_value_that_was_never_given():
    """**这一层最要紧的一条。** 一个只说了标的的草稿，念出来绝不能带出
    本金、收益、回撤 —— 那些数一旦出现，用户读到的就是"我说过"，而库里
    留下的会是一个没人认领过的默认值。

    注意断言只能看"你说的目标是"那一段：下面"还没定下来的"里这些标签
    本来就会出现，那是**在问**，不是在念。
    """
    text = retell(_draft(symbols=["BTCUSDT"]), settings=_settings())
    said = text.split("还没定下来的")[0]
    assert "BTCUSDT" in said
    assert "本金" not in said
    assert "收益目标" not in said
    assert "回撤上限" not in said


def test_an_empty_draft_says_so_instead_of_showing_a_blank_slate():
    text = retell(_draft(), settings=_settings())
    assert "还什么都没说" in text


def test_a_stated_capital_is_shown_as_stated_not_as_the_account():
    """ADR-033：用户口述的本金和账户实际权益是两个口径，复述要把这件事讲明白，
    否则他会拿口述的数当基准去理解收益率。"""
    text = retell(_full(stated_capital=1000.0), settings=_settings())
    assert "1000" in text
    assert "实际权益" in text
    assert "ADR-033" in text


def test_no_capital_means_no_capital_caveat():
    """没提本金就别讲本金那套 —— 否则用户会以为我们知道了一个他没说的数。"""
    text = retell(_full(), settings=_settings())
    assert "实际权益" not in text


# --- ② 念生效值（ADR-036 的代价落在这一环）------------------------------------

def test_the_retelling_shows_the_effective_drawdown_when_the_config_is_tighter():
    """**ADR-036 的代价就是这个。** 用户说 5%，配置是 3%，实际生效的是 3%。
    复述要念 3% 并且说明白为什么 —— 念 5% 等于让他照一个不存在的前提确认。"""
    settings = _settings(max_drawdown_pct=0.03)
    text = retell(_full(max_drawdown_pct=0.05), settings=settings)
    assert "3%" in text
    assert "不是你说的 5%" in text
    assert "ADR-036" in text


def test_a_stricter_user_line_wins_and_needs_no_correction():
    """反过来的方向：用户说 1% 而配置是 5%，生效的是**用户那个** 1%。

    这时候不能也来一句"不是你说的 X%" —— 生效值就是他说的那个，
    那句话会读成「是 1%，不是你说的 1%」，自己跟自己打架。
    提醒只在**配置赢**的时候才该出现。
    """
    settings = _settings(max_drawdown_pct=0.05)
    text = retell(_full(max_drawdown_pct=0.01), settings=settings)
    assert "亏到 1% 就停" in text
    assert "不是你说的" not in text


def test_no_caveat_when_the_two_lines_agree():
    """两边一样时不该冒出这句 —— 复述里每一条警告都得是有事要说才说的，
    否则用户会开始跳着读，真正要紧的那条也就没人看了。"""
    text = retell(_full(max_drawdown_pct=0.05), settings=_settings(max_drawdown_pct=0.05))
    assert "不是你说的" not in text
    assert "ADR-036" not in text


def test_the_drawdown_line_never_shows_only_the_users_number():
    """一条更钝但更要紧的钉法：只要生效值和用户说的值不一样，用户那个数
    就不能是回撤那一行**唯一**出现的数。"""
    settings = _settings(max_drawdown_pct=0.03)
    text = retell(_full(max_drawdown_pct=0.05), settings=settings)
    line = next(x for x in text.splitlines() if "回撤上限" in x)
    assert "3%" in line


# --- 算出来的值要标明 -----------------------------------------------------------

def test_a_derived_return_is_flagged_as_calculated():
    """「做到 1100」是用户说的，收益率是我们换算的。复述得让他看出这个区别，
    不然他会以为自己说过「10%」。"""
    out = extract("用 1000 USDT 两周内做到 1100")
    draft = _draft(**out.values)
    text = retell(draft, settings=_settings(), extract=out)
    assert "算出来" in text
    assert "1100" in text


def test_a_stated_return_is_not_flagged_as_calculated():
    """用户亲口说的 10% 不该被标成"算出来的"—— 标错了会让他以为我们改了他的话。"""
    out = extract("两周内赚 10%，最多亏 5% 就停")
    draft = _draft(**out.values)
    text = retell(draft, settings=_settings(), extract=out)
    assert "算出来" not in text


# --- 缺维度：点名问，不填默认值 --------------------------------------------------

def test_every_missing_dimension_is_asked_about_by_name():
    text = retell(_draft(), settings=_settings())
    for label in ("标的范围", "收益目标", "回撤上限", "时间窗"):
        assert label in text


def test_a_question_is_a_question_not_a_placeholder_value():
    """问的时候要给个话头（「比如 BTCUSDT」），但**不能给个值** ——
    「标的范围  BTCUSDT」和「标的范围  想做哪个交易对」在用户眼里是两回事。"""
    text = retell(_draft(), settings=_settings())
    assert "比如 BTCUSDT" in text


def test_nothing_is_asked_once_the_draft_is_complete():
    text = retell(_full(), settings=_settings())
    assert "还没定下来的" not in text


# --- 没看懂的地方要问 -----------------------------------------------------------

def test_unclear_items_from_extraction_are_put_to_the_user():
    """`extract` 的 `unclear` 出口必须接上。接到了 = 用户有机会说"对，就是 5%"；
    接不上 = 那个数就丢了，而他还以为自己说过了。"""
    out = extract("我最多能承受 5 个点")
    text = retell(_draft(), settings=_settings(), extract=out)
    assert "没看懂" in text
    assert "5 个点" in text


def test_a_clean_extraction_produces_no_unclear_section():
    out = extract("两周内赚 10%，最多亏 5% 就停")
    text = retell(_draft(**out.values), settings=_settings(), extract=out)
    assert "没看懂" not in text


def test_the_unclear_bit_is_raised_before_the_missing_list():
    """**顺序是有意义的。** 「5 个点」认不出来，而「回撤上限」这会儿也确实空着。

    先列缺的再讲没看懂的，用户读到的是"回撤上限你还没说"，而他刚说了个 5 ——
    于是他会以为这东西没在听。先把那个 5 摆出来，那句"还没定下来"才读得通。
    """
    out = extract("我最多能承受 5 个点，做 BTCUSDT")
    text = retell(_draft(**out.values), settings=_settings(), extract=out)
    assert text.index("没看懂") < text.index("还没定下来的")


def test_the_missing_list_admits_the_unclear_bit_might_already_answer_it():
    """光换顺序还不够 —— 得让用户知道那个没看懂的数**可能就是**这里要问的东西，
    不然他还是会觉得"我在重复问"。"""
    out = extract("我最多能承受 5 个点，做 BTCUSDT")
    text = retell(_draft(**out.values), settings=_settings(), extract=out)
    assert "要是其中之一" in text


# --- 授权与收尾 -----------------------------------------------------------------

def test_full_autonomy_is_stated_before_the_user_confirms():
    """ADR-018 定了全自动。**这句话必须在确认之前说** —— 确认之后执行期间
    就不再回头问了，事后再讲是没用的。"""
    text = retell(_full(), settings=_settings())
    assert "全自动" in text
    assert "ADR-018" in text


def test_the_timescale_convention_is_visible_not_hidden():
    """「个月按 30 天算」是个约定。只念「1 个月」会把折算藏起来，
    所以小时数要一起念。"""
    text = retell(_full(horizon_hours=720.0), settings=_settings())
    assert "720" in text


def test_the_closing_line_says_the_goal_locks_once_confirmed():
    text = retell(_full(), settings=_settings())
    assert "active" in text


def test_an_incomplete_draft_is_not_told_to_confirm():
    """缺着维度的草稿不该出现"回一句确认我就锁成 active"—— 那时候点确认
    等于替用户拍板，而 `activate()` 会拒掉，用户会以为是系统坏了。"""
    text = retell(_draft(symbols=["BTCUSDT"]), settings=_settings())
    assert "锁成 active" not in text


def test_an_absolute_target_is_read_back_as_the_number_itself():
    """ADR-040：用户说「做到 10175」，念回的就是 10175 —— 不换成百分比。"""
    text = retell(
        _full(target_return_pct=None, target_equity=10175.0),
        settings=_settings(),
    )
    said = text.split("还没定下来的")[0]
    assert "目标权益" in said
    assert "10175" in said


def test_both_targets_are_shown_when_both_were_said():
    """两个口径不一致时把两个都念出来，别让用户在看不见的差异上点确认。"""
    text = retell(_full(target_equity=1100.0), settings=_settings())
    said = text.split("还没定下来的")[0]
    assert "1100" in said
    assert "+10%" in said
