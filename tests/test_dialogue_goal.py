"""阶段 C-1：目标实体与它的生命周期（ADR-020 / 021 / 032 / 033）。

全程用临时库，不碰 `data/bianca.db`，也不出网。

这里盯的不是"能存能取"，而是三条会烂在库里去不掉的事：
  ① **空就是空** —— 用户没说的维度不许被填上默认值。填了之后库里只有一行
     `5.0`，没有"这是谁定的"，而后面每一次"离目标还差多少"都建立在它上面。
  ② **确认后就冻住** —— 风控参数在 `active` 那一刻固化，改一次 `.env` 不能
     悄悄换掉一个正在跑的目标的止损线。
  ③ **终态就是终态** —— "这个目标最后是什么结局"是归因时第一个要问的问题，
     它不能因为有人手滑就改回去。
"""

from __future__ import annotations

import json
from dataclasses import fields
from datetime import datetime
from types import SimpleNamespace

import pytest

from agent.config import Settings
from agent.dialogue.goal import (
    GoalDraft,
    GoalStatus,
    check_transition,
    effective_drawdown_limit,
    risk_snapshot,
)
from agent.storage import database
from agent.storage.repository import GoalRepository, MessageRepository

GOAL_TEXT = "我想用 1000 USDT 试水，两周内做到 1100，最多亏 5% 就停"


@pytest.fixture
async def db(tmp_path, monkeypatch):
    """临时库。engine/session 是模块级单例，所以必须先关再换配置。"""
    await database.close_db()
    url = f"sqlite+aiosqlite:///{tmp_path.as_posix()}/bianca.db"
    monkeypatch.setattr(database, "get_settings", lambda: Settings(database_url=url))
    await database.init_db()
    yield url
    await database.close_db()


def _complete_draft() -> GoalDraft:
    return GoalDraft(
        raw_text=GOAL_TEXT,
        objective="两周内把 1000 做到 1100，回撤不超过 5%",
        symbols=["BTCUSDT"],
        target_return_pct=0.10,
        max_drawdown_pct=0.05,
        horizon_hours=336.0,
        stated_capital=1000.0,
    )


async def _activated(equity: float = 1000.0, *, settings: Settings | None = None):
    repo = GoalRepository()
    row = await repo.create(raw_text=GOAL_TEXT, draft=_complete_draft())
    return await repo.activate(row.id, equity=equity, settings=settings or Settings())


# --- ① 空就是空 ---------------------------------------------------------------

def test_missing_lists_exactly_what_the_user_did_not_say():
    """「我想试水」什么都没说清楚 —— 那就得一条一条问回来，而不是替它填。"""
    draft = GoalDraft(raw_text="我想试着做点交易")
    assert draft.missing() == ["标的范围", "收益目标", "回撤上限", "时间窗"]


def test_zero_is_a_stated_value_not_a_silence():
    """**「回撤上限 0」是句狠话，不是沉默。** 判空只能用 None/空串/空列表 ——
    拿真值判断会把 0 当成"没说"，然后回问用户一个他已经回答过的问题；
    更糟的是有人会给它补一个 5%，而用户要的是一分都不许亏。"""
    draft = _complete_draft()
    draft.max_drawdown_pct = 0.0
    assert draft.missing() == []


def test_revise_only_touches_the_fields_it_was_given():
    """「回撤放到 8%」不该把标的范围一起重置了 —— 叠加，不是覆盖。"""
    draft = _complete_draft()
    changed = draft.revise({"max_drawdown_pct": 0.08, "horizon_hours": 168.0})
    assert changed == ["max_drawdown_pct", "horizon_hours"]
    assert draft.symbols == ["BTCUSDT"], "没提到的字段必须原样留着"
    assert draft.target_return_pct == 0.10


def test_revise_says_so_when_nothing_actually_changed():
    """重复说一遍同一个值，与"这句话里没有任何可改的东西"是两回事 ——
    前者直接复述，后者该回问。返回空列表让调用方分得出来。"""
    draft = _complete_draft()
    assert draft.revise({"max_drawdown_pct": 0.05}) == []


def test_revise_refuses_fields_that_are_not_part_of_a_goal():
    """原话是**记录**不是参数（ADR-019）。想改 raw_text 得抛，不能默默忽略 ——
    默默忽略会让调用方以为改成功了。"""
    with pytest.raises(ValueError, match="改不了"):
        draft = _complete_draft()
        draft.revise({"raw_text": "另一句话"})


# --- ② 确认后就冻住 -----------------------------------------------------------

async def test_activation_refuses_a_goal_that_is_still_missing_dimensions(db):
    """缺维度就别激活。**替用户拍板是这里最坏的一种"帮忙"** ——
    它会在库里留下一个用户从没同意过的数，且事后看不出是谁定的。"""
    repo = GoalRepository()
    row = await repo.create(raw_text="随便做点啥")
    with pytest.raises(ValueError, match="没定下来的维度"):
        await repo.activate(row.id, equity=1000.0, settings=Settings())
    assert (await repo.get(row.id)).status == GoalStatus.PROPOSED.value


async def test_activation_freezes_the_risk_params_against_later_settings_changes(db):
    """**ADR-021 的落地判据。** 激活时抄的那一份，之后谁改 `.env` 都不动它 ——
    否则一个正在跑的目标，它的止损线会随着配置悄悄变化，且没有一行记录说它变过。"""
    frozen = Settings(max_drawdown_pct=0.05)
    row = await GoalRepository().activate(
        (await GoalRepository().create(raw_text=GOAL_TEXT, draft=_complete_draft())).id,
        equity=1000.0,
        settings=frozen,
    )

    snapshot = json.loads(row.risk_snapshot)
    assert snapshot["max_drawdown_pct"] == 0.05

    # 现在把"当前配置"改宽 10 倍 —— 已激活的目标不该受影响
    loosened = risk_snapshot(Settings(max_drawdown_pct=0.5))
    assert loosened["max_drawdown_pct"] == 0.5
    assert json.loads((await GoalRepository().get(row.id)).risk_snapshot)["max_drawdown_pct"] == 0.05
    assert snapshot["stop_loss_pct"] == frozen.stop_loss_pct


async def test_the_tighter_drawdown_line_wins(db):
    """**2026-10-11 拍板：用户说的与配置里的，取更紧的那个。**

    用户能要求更严，但不能一句话把系统设的闸门放宽 —— ADR-021 的整条理由就是
    "风控一旦可被被约束者修改，它就不再是边界"。"""
    repo = GoalRepository()
    draft = _complete_draft()
    draft.max_drawdown_pct = 0.20  # 用户想宽到 20%

    row = await repo.create(raw_text=GOAL_TEXT, draft=draft)
    activated = await repo.activate(
        row.id, equity=1000.0, settings=Settings(max_drawdown_pct=0.03)
    )

    assert activated.max_drawdown_pct == 0.20, "用户的原话要原样留着（ADR-019）"
    assert json.loads(activated.risk_snapshot)["max_drawdown_pct"] == 0.03, "生效的是更紧的那个"


async def test_a_stricter_user_line_is_honoured(db):
    """反过来：用户要求更严（2%），那就按 2% —— 取紧的，不是「一律以配置为准」。"""
    repo = GoalRepository()
    draft = _complete_draft()
    draft.max_drawdown_pct = 0.02

    row = await repo.create(raw_text=GOAL_TEXT, draft=draft)
    activated = await repo.activate(
        row.id, equity=1000.0, settings=Settings(max_drawdown_pct=0.05)
    )
    assert json.loads(activated.risk_snapshot)["max_drawdown_pct"] == 0.02


def test_the_drawdown_rule_is_one_comparison_in_one_place():
    """规则只有一份。`effective_drawdown_limit` 说错一个字，整个目标的风控线就错 ——
    所以这里把三种情形一次钉死：没提 / 更宽 / 更严。"""
    strict = Settings(max_drawdown_pct=0.03)
    assert effective_drawdown_limit(strict, None) == 0.03, "用户没提就按配置"
    assert effective_drawdown_limit(strict, 0.20) == 0.03, "更宽 → 配置赢"
    assert effective_drawdown_limit(strict, 0.01) == 0.01, "更严 → 用户赢"
    assert effective_drawdown_limit(strict, 0.03) == 0.03, "一样 → 谁赢都一样"


async def test_activation_keeps_both_capital_figures_apart(db):
    """ADR-033：用户说 1000、账户里 850，**两个都得留着且看得出来**。
    收益率按实际权益算（否则算的是一个不存在的账户），但口述那个数字是
    "用户以为自己在拿多少钱试水"，复盘时要看的就是这个差。"""
    row = await _activated(equity=850.0)
    assert row.stated_capital == 1000.0
    assert row.baseline_equity == 850.0
    assert row.stated_capital != row.baseline_equity


async def test_the_deadline_is_pinned_at_activation_not_recomputed(db):
    """时间窗到期是**硬性终止条件**（§2.3，由代码判定），得有个钉死的时刻可比。"""
    row = await _activated()
    assert row.deadline_at is not None and row.confirmed_at is not None
    span_hours = (
        datetime.fromisoformat(row.deadline_at) - datetime.fromisoformat(row.confirmed_at)
    ).total_seconds() / 3600
    assert span_hours == pytest.approx(336.0)


async def test_an_active_goal_cannot_be_revised(db):
    """**这条是 ADR-021 的闸门。** 确认之后改目标，在写库这一层就得被拒 ——
    不能指望调用方每次都记得先查状态。"""
    repo = GoalRepository()
    row = await _activated()

    draft = GoalDraft.from_row(row)
    draft.max_drawdown_pct = 0.30  # 想把止损线挪宽
    with pytest.raises(ValueError, match="不能再改"):
        await repo.save_draft(row.id, draft)
    assert (await repo.get(row.id)).max_drawdown_pct == 0.05, "库里那个值不许被动过"


# --- ③ 终态就是终态 -----------------------------------------------------------

def test_a_goal_cannot_reach_achieved_without_ever_being_active():
    """从 `proposed` 直接到 `achieved` 是不合法的 —— 一个从没执行过任何东西的
    目标不该有达成记录，那条记录背后没有任何可归因的历史。"""
    with pytest.raises(ValueError, match="不能从"):
        check_transition(GoalStatus.PROPOSED, GoalStatus.ACHIEVED)


def test_a_terminal_goal_cannot_change_again():
    with pytest.raises(ValueError, match="这是终态"):
        check_transition(GoalStatus.ACHIEVED, GoalStatus.ABORTED)


async def test_ending_a_goal_requires_a_reason(db):
    """同 ADR-030：让一个目标停下来是个需要解释的动作。没有理由的终止，
    事后说不清是谁的决定、也不知道是不是出了事。"""
    repo = GoalRepository()
    row = await _activated()
    with pytest.raises(ValueError, match="必须写明理由"):
        await repo.end(row.id, status=GoalStatus.ABORTED, reason="   ")
    assert (await repo.get(row.id)).status == GoalStatus.ACTIVE.value


async def test_ending_records_the_reason_and_closes_the_goal(db):
    repo = GoalRepository()
    row = await _activated()
    ended = await repo.end(row.id, status=GoalStatus.ABORTED, reason="用户改主意了")
    assert ended.status == GoalStatus.ABORTED.value
    assert ended.end_reason == "用户改主意了"
    assert ended.ended_at is not None
    assert await repo.open_goal() is None, "结束了就不该再算「当前目标」"


async def test_an_ended_goal_rejects_a_plan(db):
    """C-4 的计划只能挂到还活着的目标上。一个已经结束的目标再收一份计划，
    归因时会读成"这个计划执行过"。"""
    repo = GoalRepository()
    row = await _activated()
    await repo.end(row.id, status=GoalStatus.ABORTED, reason="用户改主意了")
    with pytest.raises(ValueError, match="不再接受新计划"):
        await repo.attach_plan(row.id, "plan-1")


# --- 会话：重启之后还得在 -----------------------------------------------------

async def test_symbols_round_trip_through_the_database(db):
    """列表进不了 String 列，中间有一次 JSON 编解码 —— 那一处必须能转回来。"""
    row = await GoalRepository().create(raw_text=GOAL_TEXT, draft=_complete_draft())
    restored = GoalDraft.from_row(await GoalRepository().get(row.id))
    assert restored.symbols == ["BTCUSDT"]
    assert not restored.missing()


async def test_a_corrupt_symbols_column_is_not_read_as_user_silence(db):
    """坏掉的 JSON **不能退回 None** —— None 的含义是"用户没说标的范围"，
    而真相是记录坏了。把后者说成前者，等于替一条烂记录编一个合理的解释。"""

    # 其余字段从 `GoalDraft` 自己的定义里长出来，**不手写** —— 手写的那版在
    # 加 `target_equity` 时悄悄烂掉了（`'_Row' object has no attribute ...`），
    # 而它烂掉的方式是"测别的东西时报一个跟本意无关的错"。
    row = SimpleNamespace(
        id="g1",
        raw_text=GOAL_TEXT,
        symbols="BTCUSDT",  # 不是 JSON —— 有人手改过库
        **{
            f.name: None
            for f in fields(GoalDraft)
            if f.name not in ("raw_text", "symbols")
        },
    )

    with pytest.raises(ValueError, match="不是合法 JSON"):
        GoalDraft.from_row(row)


async def test_messages_survive_a_restart_in_order(db):
    """**§6.5 验收第 4 条：会话重启后上下文不丢。** 这里真的把库关掉再开一次，
    而不是只查一遍 —— 只查一遍的话，缓存里的东西也能过。"""
    goal = await GoalRepository().create(raw_text=GOAL_TEXT, draft=_complete_draft())
    msgs = MessageRepository()
    await msgs.append(goal_id=goal.id, role="user", content=GOAL_TEXT)
    await msgs.append(goal_id=goal.id, role="assistant", content="你说的回撤上限是 5%，对吗？")
    await msgs.append(goal_id=goal.id, role="user", content="对")

    await database.close_db()
    await database.init_db()

    restored = await MessageRepository().list_for_goal(goal.id)
    assert [m.role for m in restored] == ["user", "assistant", "user"]
    assert restored[0].content == GOAL_TEXT
    assert restored[-1].content == "对"


async def test_messages_of_two_goals_do_not_mix(db):
    repo, msgs = GoalRepository(), MessageRepository()
    first = await repo.create(raw_text="第一个目标", draft=_complete_draft())
    second = await repo.create(raw_text="第二个目标", draft=_complete_draft())
    await msgs.append(goal_id=first.id, role="user", content="一")
    await msgs.append(goal_id=second.id, role="user", content="二")

    assert [m.content for m in await msgs.list_for_goal(first.id)] == ["一"]
    assert [m.content for m in await msgs.list_for_goal(second.id)] == ["二"]


async def test_open_goal_finds_the_one_still_running(db):
    """阶段 C 一次只跑一个目标（§8 第 3 条），所以"当前是哪个"就是这一句。"""
    repo = GoalRepository()
    old = await _activated()
    # 先激活再到期 —— 一个从没 active 过的目标谈不上"时间窗到了"，
    # 状态机拒得对（`proposed` 只能去 `active` / `aborted`）。
    await repo.end(old.id, status=GoalStatus.EXPIRED, reason="时间窗到了")
    fresh = await repo.create(raw_text="新目标", draft=_complete_draft())

    assert (await repo.open_goal()).id == fresh.id


async def test_the_original_words_are_kept_next_to_the_parse(db):
    """ADR-019 R1 的链头是"目标"，而目标就是**用户原话**。只留结构化那份的话，
    事后没人能判断当时到底是解析错了，还是用户自己说错了。"""
    draft = _complete_draft()
    raw = "我想用 1000 USDT 试水，两周内做到 1100，最多亏 5% 就停"
    row = await GoalRepository().create(
        raw_text=raw,
        draft=draft,
        parsed={"horizon_days": 14, "max_drawdown": "5%"},
    )
    assert row.raw_text == raw
    assert json.loads(row.parsed)["horizon_days"] == 14
    assert row.objective == draft.objective
