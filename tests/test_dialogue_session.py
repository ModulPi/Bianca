"""阶段 C-3：会话循环（多轮修正 + 确认）。

全程用临时库、不出网。这一层自己不做判断，所以下面盯的是**接线**：
判据有没有真的接上、拒绝有没有真的生效、说过的有没有真的记下来。

最要紧的一条在最上面：**`say()` 里没有任何一条路能激活目标**（ADR-037）。
"""

from __future__ import annotations

import json

import pytest

from agent.config import Settings
from agent.dialogue import Dialogue, GoalStatus
from agent.storage import database
from agent.storage.models import GoalLog
from agent.storage.repository import GoalRepository, MessageRepository

OPENING = "我想用 1000 USDT 试水，两周内做到 1100，最多亏 5% 就停"
COMPLETE = "用 1000 USDT 做 BTCUSDT，两周内赚 10%，最多亏 5% 就停"


@pytest.fixture
async def db(tmp_path, monkeypatch):
    """临时库。engine/session 是模块级单例，所以必须先关再换配置。"""
    await database.close_db()
    url = f"sqlite+aiosqlite:///{tmp_path.as_posix()}/bianca.db"
    monkeypatch.setattr(database, "get_settings", lambda: Settings(database_url=url))
    await database.init_db()
    yield
    await database.close_db()


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


def _dialogue(settings: Settings) -> Dialogue:
    return Dialogue(settings=settings)


async def _started(settings: Settings, text: str = OPENING) -> tuple[Dialogue, str]:
    d = _dialogue(settings)
    reply = await d.start(text)
    return d, reply.goal_id


# --- 最重的一条：正文里说「对」不算确认（ADR-037）-------------------------------

async def test_saying_yes_does_not_activate_the_goal(db, settings):
    """**这一条是 ADR-037 的钉子。**

    「对，就按这个来」在一个齐了的目标上，读起来像确认 —— 但它必须只是
    **一次普通的发言**。一旦这里放行，正文里认关键词就成了第二条激活路径，
    而那条路径上的下一步是「这样就对了，但是回撤再宽一点」被读成确认。
    """
    d, goal_id = await _started(settings, COMPLETE)

    reply = await d.say(goal_id, "对，就按这个来")

    assert reply.activated is False
    assert reply.status is GoalStatus.PROPOSED
    assert (await GoalRepository().get(goal_id)).status == GoalStatus.PROPOSED.value


async def test_the_only_way_to_activate_is_confirm(db, settings):
    """把上一条反过来钉：同样的目标，走 `confirm()` 就真的生效。"""
    d, goal_id = await _started(settings, COMPLETE)

    reply = await d.confirm(goal_id, equity=1000.0)

    assert reply.activated is True
    assert reply.status is GoalStatus.ACTIVE


async def test_a_yes_shaped_sentence_is_recorded_but_changes_nothing(db, settings):
    """说「对」也不是白说 —— 它得进记录。事后要能看出用户确实说了这么一句。"""
    d, goal_id = await _started(settings, COMPLETE)
    await d.say(goal_id, "对，就按这个来")

    said = [m.content for m in await MessageRepository().list_for_goal(goal_id)]
    assert "对，就按这个来" in said


async def test_a_turn_that_changed_nothing_says_so_and_points_at_the_real_action(db, settings):
    """**ADR-037 的代价条款落地。** 用户说「对」之后，如果只把同一段复述原样再念一遍，
    他很合理地会以为"系统收到我的确认了"。

    所以没改到东西的回合要说两件事：这一句我没读出可改的维度；要确认得走那个动作。
    """
    d, goal_id = await _started(settings, COMPLETE)

    reply = await d.say(goal_id, "对，就按这个来")

    assert "没有读出任何可以改的维度" in reply.text
    assert "确认那个动作" in reply.text


async def test_the_no_op_notice_does_not_fire_on_resume(db, settings):
    """`resume()` 是"重念一遍"，不是用户说了话 —— 它不该冒出一句
    「这一句我没读出可改的维度」。那是给用户发言配的话。"""
    d, goal_id = await _started(settings, COMPLETE)

    reply = await d.resume(goal_id)

    assert "没有读出任何可以改的维度" not in reply.text


async def test_the_no_op_notice_guesses_nothing_about_intent(db, settings):
    """那句提示是**陈述事实**，不是判"这句像不像确认"。

    判像不像就是回到自由文本认关键词 —— 那条路是被否掉的。所以一句完全不像
    确认的闲聊也该拿到同样的提示（这里钉的就是"没有在猜意图"）。
    """
    d, goal_id = await _started(settings, COMPLETE)

    reply = await d.say(goal_id, "今天天气不错")

    assert "没有读出任何可以改的维度" in reply.text


# --- 多轮修正（验收第 2 条）---------------------------------------------------

async def test_a_correction_touches_only_the_field_it_named(db, settings):
    """**§6.5 验收第 2 条：未确认时改一个字段，复述随之变。**

    「回撤放到 8%」只能动回撤 —— 若它顺手把别处也刷新了（比如重新跑一遍
    整句解析），用户会看到自己没提过的字段悄悄变了。
    """
    d, goal_id = await _started(settings)

    reply = await d.say(goal_id, "回撤放到 8%")

    assert reply.changed == ["max_drawdown_pct"]
    assert reply.draft.max_drawdown_pct == 0.08
    assert reply.draft.symbols is None, "这一句里没有标的，标的就该还是空的"
    assert reply.draft.horizon_hours == 336.0, "时间窗不该被动过"


async def test_the_correction_shows_up_in_what_is_read_back(db, settings):
    """改了得**念出来**。改完不重念，用户就没机会在确认前看到它到底改成了什么。"""
    d, goal_id = await _started(settings)

    reply = await d.say(goal_id, "回撤收到 2%")

    line = next(x for x in reply.text.splitlines() if "回撤上限" in x)
    assert "2%" in line


async def test_a_correction_that_would_loosen_the_line_is_shown_as_not_applying(db, settings):
    """**这条比上一条要紧。** 用户说「回撤放到 8%」而配置是 5% —— 生效值仍是 5%。

    这个方向必须让用户看见。他刚提了一个要求，如果复述里只有一行「亏到 5% 就停」，
    他很可能读成"系统没理我"或者干脆没注意到。所以**要求被记下（草稿里是 8%）、
    生效值被念出（5%）、两者的差被讲明白**，三件事都要发生。

    （这条是写测试时被撞出来的：我原本断言"8% 会出现在那一行"，红了 ——
    红得对，ADR-036 本来就该顶住。）
    """
    d, goal_id = await _started(settings)

    reply = await d.say(goal_id, "回撤放到 8%")

    assert reply.draft.max_drawdown_pct == 0.08, "他说的那个数要留下"
    line = next(x for x in reply.text.splitlines() if "回撤上限" in x)
    assert "5%" in line, "生效的还是配置里那个更紧的"
    assert "8%" in reply.text, "但他说过的 8% 不能消失 —— 下面得讲明白为什么不算数"


async def test_an_unchanged_field_is_not_reported_as_changed(db, settings):
    """用户重说一遍同一个值，不是一次修改 —— 否则事后看记录会以为他改过两次。"""
    d, goal_id = await _started(settings, COMPLETE)

    reply = await d.say(goal_id, "回撤还是 5%")

    assert reply.changed == []


# --- active 之后改不动（ADR-021）---------------------------------------------

async def test_an_active_goal_refuses_a_correction_without_raising(db, settings):
    """已经生效的目标上再提要求，**要好好回绝，不是抛异常**。

    抛异常是给程序看的，用户看到的是一个栈。而这里的正确行为是：
    告诉他改不了、以及要改该怎么办。
    """
    d, goal_id = await _started(settings, COMPLETE)
    await d.confirm(goal_id, equity=1000.0)

    reply = await d.say(goal_id, "回撤改到 20%")

    assert reply.status is GoalStatus.ACTIVE
    assert "改不了" in reply.text
    assert "abort" in reply.text


async def test_a_refused_correction_does_not_reach_the_database(db, settings):
    """回绝就得是真的没写进去 —— 只回一句"不行"而库里已经变了，是最坏的一种。"""
    d, goal_id = await _started(settings, COMPLETE)
    await d.confirm(goal_id, equity=1000.0)

    await d.say(goal_id, "回撤改到 20%")

    row = await GoalRepository().get(goal_id)
    assert row.max_drawdown_pct == 0.05
    assert json.loads(row.risk_snapshot)["max_drawdown_pct"] == 0.05


async def test_a_refused_correction_is_still_recorded(db, settings):
    """用户在那个目标上说过的话**照记**，回绝的话也照记。

    不记的话，事后翻记录看到的是"用户从没提过这个要求" ——
    而真相是我们没照做。这两件事在归因时完全不同。
    """
    d, goal_id = await _started(settings, COMPLETE)
    await d.confirm(goal_id, equity=1000.0)

    await d.say(goal_id, "回撤改到 20%")

    said = [m.content for m in await MessageRepository().list_for_goal(goal_id)]
    assert "回撤改到 20%" in said
    assert any("改不了" in x for x in said)


async def test_a_terminal_goal_refuses_too(db, settings):
    """中止掉的目标同样改不动 —— 终态就是终态（C-1）在这一层的体现。"""
    d, goal_id = await _started(settings, COMPLETE)
    await GoalRepository().end(goal_id, status=GoalStatus.ABORTED, reason="测试")

    reply = await d.say(goal_id, "回撤改到 20%")

    assert reply.status is GoalStatus.ABORTED
    assert "改不了" in reply.text


# --- 确认这一动作自己也有闸门 --------------------------------------------------

async def test_confirm_refuses_a_goal_that_is_still_missing_dimensions(db, settings):
    """缺着维度确认 = 替用户拍板。这里回一句人话，而不是把异常抛给用户看。"""
    d, goal_id = await _started(settings, OPENING)  # 没提标的

    reply = await d.confirm(goal_id, equity=1000.0)

    assert reply.activated is False
    assert "标的范围" in reply.text
    assert (await GoalRepository().get(goal_id)).status == GoalStatus.PROPOSED.value


async def test_confirm_pins_the_equity_it_was_given(db, settings):
    """基准权益是**调用方给的那一刻的数**（ADR-033），不是本金那一栏。

    用户口述 1000，而账户实际是 1012.34 —— 记下来的必须是后者。
    """
    d, goal_id = await _started(settings, COMPLETE)

    await d.confirm(goal_id, equity=1012.34)

    row = await GoalRepository().get(goal_id)
    assert row.baseline_equity == 1012.34
    assert row.stated_capital == 1000.0, "口述的那个数另存一栏，不能被覆盖"


async def test_the_lock_message_shows_the_frozen_drawdown(db, settings):
    """锁上之后那一行念的是生效值。"""
    d, goal_id = await _started(settings, COMPLETE)

    reply = await d.confirm(goal_id, equity=1000.0)

    assert "5%" in reply.text


async def test_the_lock_message_reads_the_frozen_snapshot_not_the_current_settings(db, settings):
    """**这条钉的是"念哪一份"。** 冻住的那份和配置里现在那份，激活之后就可能是
    两个数了（有人改了 `.env`）。

    写错的样子很自然 —— 顺手写 `self._settings` —— 而错了的表现是：
    用户回头看到的是**另一个数**，且没有任何地方报错。

    直接调 `_locked_text` 是因为这里要构造一个"快照与配置不一致"的状态，
    而 `confirm()` 只能成功一次，从外面推不出这个局面来。
    """
    d, goal_id = await _started(settings, COMPLETE)
    row = await GoalRepository().get(goal_id)
    row.risk_snapshot = json.dumps(
        {"max_drawdown_pct": 0.017, "daily_loss_limit": 42.0, "max_trade_amount": 7.0}
    )

    text = Dialogue(settings=Settings(_env_file=None, max_drawdown_pct=0.9))._locked_text(row, 1000.0)

    assert "1.7%" in text, "念的该是冻住的那一份"
    assert "90%" not in text, "不该是配置里现在那份"


async def test_a_second_confirm_is_refused(db, settings):
    """已经 active 了再确认一次，状态机要顶回来 —— 不能变成"重新冻一次"。"""
    d, goal_id = await _started(settings, COMPLETE)
    await d.confirm(goal_id, equity=1000.0)

    with pytest.raises(ValueError):
        await d.confirm(goal_id, equity=2000.0)


# --- 记账（验收第 4 条：重启接得上）--------------------------------------------

async def test_the_whole_conversation_survives_a_restart(db, settings):
    """**§6.5 验收第 4 条。** 真的把库关掉再开一次，而不是只查一遍。"""
    d, goal_id = await _started(settings)
    await d.say(goal_id, "做 BTCUSDT")
    await d.say(goal_id, "回撤放到 8%")

    await database.close_db()
    await database.init_db()

    msgs = await MessageRepository().list_for_goal(goal_id)
    assert msgs[0].content == OPENING
    assert [m.role for m in msgs] == ["user", "assistant"] * 3
    assert msgs[-2].content == "回撤放到 8%"

    # 重启之后还能接着改。
    reply = await _dialogue(settings).say(goal_id, "时间窗改成 3 天")
    assert reply.draft.horizon_hours == 72.0
    assert reply.draft.max_drawdown_pct == 0.08, "前面几轮改的不能丢"


async def test_resume_reads_back_without_touching_anything(db, settings):
    """接上下文用 `resume()`：**不读任何新话**，所以不会改到任何字段。"""
    d, goal_id = await _started(settings)

    before = [m.content for m in await MessageRepository().list_for_goal(goal_id)]
    reply = await d.resume(goal_id)
    after = await MessageRepository().list_for_goal(goal_id)

    assert reply.changed == []
    # 只有一条新的（我们念回去的那句），没有多出来的"用户发言"。
    assert len(after) == len(before) + 1
    assert [m.role for m in after].count("user") == 1


async def test_the_first_parse_is_kept_next_to_what_the_user_later_said(db, settings):
    """ADR-020 要的是能分清「机器读错了」和「用户改了」。

    `parsed` 留的是**第一句**的解析，目标列留的是当前值 ——
    用户后来改成 8% 之后，两者不一样，而那个不一样正是信息。
    """
    d, goal_id = await _started(settings)
    await d.say(goal_id, "回撤放到 8%")

    row = await GoalRepository().get(goal_id)
    assert json.loads(row.parsed)["values"]["max_drawdown_pct"] == 0.05
    assert row.max_drawdown_pct == 0.08


async def test_a_completely_unparsed_opening_is_still_recorded(db, settings):
    """用户说了一句我们一个字都没读懂的，也要建行、也要记原文 ——
    否则他会以为那句没发出去。"""
    d = _dialogue(settings)

    reply = await d.start("我想稳一点")

    row = await GoalRepository().get(reply.goal_id)
    assert row.raw_text == "我想稳一点"
    assert reply.draft.missing(), "没读懂就该是四个维度全缺"


# --- 一次只跑一个 --------------------------------------------------------------

async def test_a_second_goal_is_refused_while_the_first_is_open(db, settings):
    """阶段 C 一次只跑一个（§8 第 3 条）。

    **这里故意抛异常而不是回一句**：这是调用方用错了（HTTP 层该在开新的之前
    先问一句），不是用户说了句听不懂的话。把它变成一句温柔的回话，
    就等于让"当前目标到底是哪个"这个歧义进了库。
    """
    d, goal_id = await _started(settings)

    with pytest.raises(ValueError, match="已经有一个没结束的目标"):
        await d.start("再开一个")

    assert (await GoalRepository().open_goal()) is not None
    assert (await GoalRepository().get(goal_id)) is not None


async def test_a_new_goal_is_allowed_once_the_old_one_ended(db, settings):
    """结束了就能开新的 —— 上一条的闸门不该把路彻底堵死。"""
    d, goal_id = await _started(settings)
    await GoalRepository().end(goal_id, status=GoalStatus.ABORTED, reason="换一个")

    reply = await _dialogue(settings).start("用 500 USDT 做 ETHUSDT，一周内赚 5%，最多亏 2%")

    assert reply.goal_id != goal_id
    assert reply.draft.symbols == ["ETHUSDT"]


async def test_saying_something_to_a_goal_that_does_not_exist_is_an_error(db, settings):
    """id 不存在是调用方用错了，不是用户说错了 —— 照抛。"""
    with pytest.raises(ValueError, match="没有这个目标"):
        await _dialogue(settings).say("nope", "随便说点什么")


# --- 给 C-2b 留的插口真的能用 --------------------------------------------------

async def test_the_extractor_is_swappable(db, settings):
    """`extractor` 这个插口是给 C-2b（语义层）留的。

    它得真的能被换掉 —— 不然等语义层要接进来的时候，会发现循环跟解析器
    已经长在一起了。这里换一个"永远只读出一个标的"的假解析器。
    """
    from agent.dialogue.extract import Extract

    def fake(text: str) -> Extract:
        return Extract(values={"symbols": ["BTCUSDT"]}, evidence={"symbols": "假的"})

    d = Dialogue(settings=settings, extractor=fake)
    reply = await d.start("随便说点什么")

    assert reply.draft.symbols == ["BTCUSDT"]
    assert reply.draft.max_drawdown_pct is None
