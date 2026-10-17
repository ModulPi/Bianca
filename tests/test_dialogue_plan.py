"""阶段 C-4：计划落库。

全程离线：经理是注入的假对象，不碰网络、不花钱。下面盯四件事：

1. **喂给经理的是生效值，不是用户口述值**（ADR-021 / ADR-033 / ADR-036）。
   这一点最容易悄悄坏掉 —— 坏掉时计划照样能生成，只是从第一行起就是错的。
2. **失败也留一行**，但**不挂到目标上**。`plans` 里有行 ≠ `goal.plan_id` 有值。
3. **已有计划就拒绝重规划**；没解出来时**允许重试**。
4. R1 要的完整 prompt / 工具调用，真的落进那几列了。
"""

from __future__ import annotations

import json

import pytest

from agent.config import Settings
from agent.dialogue import Dialogue, GoalStatus, goal_brief
from agent.dialogue.goal import effective_drawdown_limit
from agent.manager.loop import ManagerRun, ToolInvocation
from agent.manager.schemas import Plan, PlanStep
from agent.storage import database
from agent.storage.repository import (
    GoalRepository,
    MessageRepository,
    PlanRepository,
)

COMPLETE = "用 1000 USDT 做 BTCUSDT，两周内赚 10%，最多亏 5% 就停"


@pytest.fixture
async def db(tmp_path, monkeypatch):
    await database.close_db()
    url = f"sqlite+aiosqlite:///{tmp_path.as_posix()}/bianca.db"
    monkeypatch.setattr(database, "get_settings", lambda: Settings(database_url=url))
    await database.init_db()
    yield
    await database.close_db()


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


class _FakeManager:
    """只记下"喂进来的是什么"，返回预先摆好的 run。"""

    def __init__(self, run: ManagerRun | Exception) -> None:
        self.run = run
        self.goals: list[str] = []

    async def plan(self, goal: str, *, max_rounds: int = 4) -> ManagerRun:
        self.goals.append(goal)
        if isinstance(self.run, Exception):
            raise self.run
        return self.run


def _run(*, with_plan: bool = True, errors: list[str] | None = None) -> ManagerRun:
    plan = Plan(
        objective="在两周内把 1000 USDT 做到 1100",
        steps=[
            PlanStep(what="看一眼 BTCUSDT 最近 4 小时的 K 线", why="先确认方向", tool="get_klines"),
            PlanStep(what="分批建仓", why="单笔不超过上限"),
        ],
        stop_conditions=["回撤到 3% 停"],
        assumptions=["行情数据是连续的"],
        risks=["周末流动性薄"],
    )
    return ManagerRun(
        goal="已确认的目标：…",
        plan=plan if with_plan else None,
        plan_shape="json" if with_plan else "text",
        final_text='{"objective": "…"}' if with_plan else "我建议先看行情。",
        transcript=[
            {"role": "system", "content": "你是经理"},
            {"role": "user", "content": "已确认的目标：…"},
            {"role": "assistant", "content": '{"objective": "…"}'},
        ],
        invocations=[
            ToolInvocation(
                name="get_klines",
                arguments={"symbol": "BTCUSDT", "limit": 4},
                ok=True,
                result={"closes": [1, 2, 3]},
                round=1,
            ),
            ToolInvocation(
                name="get_ticker",
                arguments={"symbol": "BTCUSDT"},
                ok=False,
                error="连接超时",
                round=1,
            ),
        ],
        rounds=1,
        usage={"total_tokens": 321},
        errors=errors or [],
        shapes=["deepseek"],
    )


async def _active(settings: Settings) -> tuple[Dialogue, str]:
    d = Dialogue(settings=settings)
    started = await d.start(COMPLETE)
    await d.confirm(started.goal_id, equity=1000.0)
    return d, started.goal_id


async def _dialogue_with(
    settings: Settings, manager: _FakeManager
) -> tuple[Dialogue, str]:
    d = Dialogue(settings=settings, manager=manager)  # type: ignore[arg-type]
    started = await d.start(COMPLETE)
    await d.confirm(started.goal_id, equity=1000.0)
    return d, started.goal_id


# --- 喂给经理的是生效值（ADR-036 / ADR-033）-----------------------------------

async def test_the_brief_carries_the_frozen_drawdown_not_the_stated_one(db):
    """用户说 5%，配置是 3% —— 喂给经理的必须是 **3%**。

    拿 5% 去拆计划、而执行时会在 3% 停，那份计划从第一行起就是错的。
    这里同时钉住"用户说的那个数没有消失"：它留在注里，事后看得出是谁说的。
    """
    tight = Settings(_env_file=None, max_drawdown_pct=0.03)
    d, goal_id = await _active(tight)
    row = await GoalRepository().get(goal_id)

    brief = goal_brief(row)

    assert "回撤上限（生效）：3%" in brief
    assert "用户说的 5% 更宽" in brief
    assert "回撤上限（生效）：5%" not in brief


async def test_a_tighter_user_limit_needs_no_explanation(db):
    """用户说 2%、配置 5% —— 生效值就是 2%，**不该**冒出一句「取更紧的那个」。

    那句话只在配置顶住了用户的时候才对。反着说会让用户以为他的 2% 被系统改宽了。
    """
    d = Dialogue(settings=Settings(_env_file=None))
    started = await d.start("用 1000 USDT 做 BTCUSDT，两周内赚 10%，最多亏 2% 就停")
    await d.confirm(started.goal_id, equity=1000.0)
    row = await GoalRepository().get(started.goal_id)

    brief = goal_brief(row)

    assert "回撤上限（生效）：2%" in brief
    assert "更宽" not in brief


async def test_the_brief_carries_the_baseline_equity(db, settings):
    """收益率的分母是激活那一刻的实际权益（ADR-033），不是用户口述的本金。"""
    d, goal_id = await _active(settings)
    row = await GoalRepository().get(goal_id)

    brief = goal_brief(row)

    assert "基准权益：1000 USDT" in brief
    assert "ADR-033" in brief


async def test_the_brief_states_risk_bounds_with_the_right_unit(db, settings):
    """量纲写错一次，经理就拿一个差 100 倍的边界去做计划。"""
    d, goal_id = await _active(settings)
    row = await GoalRepository().get(goal_id)

    brief = goal_brief(row)

    assert "单笔上限：50 USDT" in brief
    assert "每日亏损上限：100 USDT" in brief
    assert "最大仓位：50%" in brief


async def test_the_manager_receives_the_brief_not_the_raw_text(db, settings):
    """经理拿到的是结构化生效值；用户原话只作为最后一行的引用附上。"""
    manager = _FakeManager(_run())
    d, goal_id = await _dialogue_with(settings, manager)

    await d.plan(goal_id)

    assert len(manager.goals) == 1
    brief = manager.goals[0]
    assert "回撤上限（生效）" in brief
    assert "基准权益" in brief
    assert brief.rstrip().endswith(f"用户原话：「{COMPLETE}」")


# --- 成功：落一行、挂上、念出来 ------------------------------------------------

async def test_a_successful_plan_is_persisted_and_attached(db, settings):
    manager = _FakeManager(_run())
    d, goal_id = await _dialogue_with(settings, manager)

    reply = await d.plan(goal_id)

    assert reply.plan_id is not None
    goal = await GoalRepository().get(goal_id)
    assert goal.plan_id == reply.plan_id

    attempt = await PlanRepository().get(reply.plan_id)
    assert attempt is not None
    assert attempt.goal_id == goal_id
    assert attempt.plan_shape == "json"
    assert json.loads(attempt.plan)["objective"] == "在两周内把 1000 USDT 做到 1100"


async def test_the_plan_is_read_back_from_the_row_not_from_memory(db, settings):
    """念给用户听的，就是事后翻记录看到的那一份。"""
    manager = _FakeManager(_run())
    d, goal_id = await _dialogue_with(settings, manager)

    reply = await d.plan(goal_id)

    assert "计划出来了" in reply.text
    assert "1. 看一眼 BTCUSDT 最近 4 小时的 K 线" in reply.text
    assert "依据：先确认方向" in reply.text
    assert "工具：get_klines" in reply.text
    assert "回撤到 3% 停" in reply.text
    assert "行情数据是连续的" in reply.text
    assert "周末流动性薄" in reply.text
    assert reply.plan_id in reply.text


async def test_the_plan_attempt_is_recorded_as_a_message(db, settings):
    """计划也是这一轮对话里说过的话，重启之后得能翻到。"""
    manager = _FakeManager(_run())
    d, goal_id = await _dialogue_with(settings, manager)

    reply = await d.plan(goal_id)

    said = [m.content for m in await MessageRepository().list_for_goal(goal_id)]
    assert reply.text in said


# --- 失败：留行，但不挂 --------------------------------------------------------

async def test_a_failed_plan_leaves_an_attempt_but_does_not_attach(db, settings):
    """没解出计划时：`plans` 里有行、`goal.plan_id` 还是空的。

    这两件事必须分得开。拿最后一段散文顶上去，会让「有 plan_id」看起来像
    「有计划」，而阶段 D 会照着它执行。
    """
    manager = _FakeManager(_run(with_plan=False, errors=["4 轮之内没有拿到结构化计划"]))
    d, goal_id = await _dialogue_with(settings, manager)

    reply = await d.plan(goal_id)

    assert reply.plan_id is not None
    goal = await GoalRepository().get(goal_id)
    assert goal.plan_id is None

    attempt = await PlanRepository().get(reply.plan_id)
    assert attempt.plan is None
    assert attempt.plan_shape == "text"
    assert "4 轮之内没有拿到结构化计划" in json.loads(attempt.errors)

    assert "没有挂到目标上" in reply.text
    assert "4 轮之内没有拿到结构化计划" in reply.text


async def test_a_failed_attempt_can_be_retried(db, settings):
    """失败是**可以重试的动作** —— 这正是不把它塞进 `confirm()` 的理由。"""
    manager = _FakeManager(_run(with_plan=False))
    d, goal_id = await _dialogue_with(settings, manager)
    await d.plan(goal_id)

    manager.run = _run(with_plan=True)
    reply = await d.plan(goal_id)

    assert len(manager.goals) == 2
    assert (await GoalRepository().get(goal_id)).plan_id == reply.plan_id
    assert len(await PlanRepository().for_goal(goal_id)) == 2


async def test_a_crash_still_leaves_an_attempt(db, settings):
    """经理自己抛异常也不能让"这次尝试发生过"无据可查。"""
    manager = _FakeManager(RuntimeError("端点没配"))
    d, goal_id = await _dialogue_with(settings, manager)

    reply = await d.plan(goal_id)

    assert reply.plan_id is not None
    assert (await GoalRepository().get(goal_id)).plan_id is None
    attempt = await PlanRepository().get(reply.plan_id)
    assert attempt.plan is None
    assert any("端点没配" in err for err in json.loads(attempt.errors))


# --- 拒绝 ---------------------------------------------------------------------

async def test_planning_a_proposed_goal_is_refused(db, settings):
    """还没确认就没有生效值可喂 —— 规划必须先等确认。"""
    manager = _FakeManager(_run())
    d = Dialogue(settings=settings, manager=manager)  # type: ignore[arg-type]
    started = await d.start(COMPLETE)

    reply = await d.plan(started.goal_id)

    assert reply.plan_id is None
    assert manager.goals == []
    assert "还不能规划" in reply.text
    assert (await GoalRepository().get(started.goal_id)).plan_id is None


async def test_an_existing_plan_is_not_replaced(db, settings):
    """计划一旦挂上就是执行依据，不能悄悄换一份（R1）。"""
    manager = _FakeManager(_run())
    d, goal_id = await _dialogue_with(settings, manager)
    first = await d.plan(goal_id)

    reply = await d.plan(goal_id)

    assert len(manager.goals) == 1
    assert reply.plan_id is None
    assert "不重规划" in reply.text
    assert (await GoalRepository().get(goal_id)).plan_id == first.plan_id
    assert len(await PlanRepository().for_goal(goal_id)) == 1


async def test_refusals_are_still_recorded(db, settings):
    """拒绝也记账 —— 不让记录里出现"用户从没提过这个要求"。"""
    manager = _FakeManager(_run())
    d, goal_id = await _dialogue_with(settings, manager)
    await d.plan(goal_id)

    reply = await d.plan(goal_id)

    said = [m.content for m in await MessageRepository().list_for_goal(goal_id)]
    assert reply.text in said


# --- R1：完整 prompt 与工具调用 ------------------------------------------------

async def test_the_full_transcript_is_persisted(db, settings):
    """R1 要的是**实际发出去的完整 messages**，不是一句摘要。"""
    manager = _FakeManager(_run())
    d, goal_id = await _dialogue_with(settings, manager)

    reply = await d.plan(goal_id)

    attempt = await PlanRepository().get(reply.plan_id)
    transcript = json.loads(attempt.transcript)
    assert transcript[0] == {"role": "system", "content": "你是经理"}
    assert transcript[-1] == {"role": "assistant", "content": '{"objective": "…"}'}


async def test_failed_tool_calls_are_persisted_too(db, settings):
    """`ok=False` 那条也得留 —— 它是「没跑成」的证据。"""
    manager = _FakeManager(_run())
    d, goal_id = await _dialogue_with(settings, manager)

    reply = await d.plan(goal_id)

    attempt = await PlanRepository().get(reply.plan_id)
    invocations = json.loads(attempt.invocations)
    assert [call["name"] for call in invocations] == ["get_klines", "get_ticker"]
    assert invocations[1]["ok"] is False
    assert invocations[1]["error"] == "连接超时"
    assert json.loads(attempt.usage) == {"total_tokens": 321}


async def test_the_plan_rows_are_orderable_per_goal(db, settings):
    """一个目标可以有多次尝试，最近一次排在最前。"""
    manager = _FakeManager(_run(with_plan=False))
    d, goal_id = await _dialogue_with(settings, manager)
    first = await d.plan(goal_id)
    manager.run = _run(with_plan=True)
    second = await d.plan(goal_id)

    attempts = await PlanRepository().for_goal(goal_id)

    assert [a.id for a in attempts] == [second.plan_id, first.plan_id]
