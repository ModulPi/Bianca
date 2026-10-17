"""D-3：经理的执行决策接口（§6.6 D-2）。

钉的是三件事：**解不出来就 HOLD（而不是猜一个动作）**、**信号不完整也不替它补**、
以及**每一次决策都落库**（含 HOLD 与解析失败）。第三条是 R1 的链头之一 ——
没有它，事后能看到的只有成交，看不到"为什么没成交"。
"""

from __future__ import annotations

import json

import httpx
import pytest

from agent.config import Settings
from agent.manager.loop import Manager, Reply
from agent.manager.parser import (
    DEEPSEEK_CALL_BEGIN,
    DEEPSEEK_CALL_END,
    DEEPSEEK_CALLS_BEGIN,
    DEEPSEEK_CALLS_END,
    DEEPSEEK_SEP,
)
from agent.manager.schemas import Plan, PlanStep
from agent.manager.tools import ToolRegistry, ToolSpec
from agent.storage import database
from agent.storage.repository import DecisionRepository


def _deepseek_call(name: str, args: dict) -> str:
    return (
        f"{DEEPSEEK_CALLS_BEGIN}{DEEPSEEK_CALL_BEGIN}function{DEEPSEEK_SEP}{name}\n"
        "```json\n" + json.dumps(args, ensure_ascii=False) + "\n```\n"
        f"{DEEPSEEK_CALL_END}{DEEPSEEK_CALLS_END}"
    )


def _registry() -> ToolRegistry:
    reg = ToolRegistry()

    async def _drawdown(args: dict):
        return {"available": True, "drawdown_pct": 0.02}

    reg.register(
        ToolSpec(
            name="get_drawdown",
            description="读当前回撤",
            parameters={"type": "object", "properties": {}},
            handler=_drawdown,
        )
    )
    return reg


def _scripted(replies: list):
    seen: list[list[dict]] = []

    async def chat(messages):
        seen.append([dict(m) for m in messages])
        item = replies[min(len(seen) - 1, len(replies) - 1)]
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, Reply) else Reply(content=item)

    return chat, seen


def _manager(replies):
    chat, seen = _scripted(replies)
    return Manager(_registry(), settings=Settings(), chat=chat), seen


def _json(**over) -> str:
    body = {
        "action": "BUY",
        "symbol": "BTCUSDT",
        "amount": 12.5,
        "reason": "回撤还很浅，按计划建第一笔",
        "plan_step": 1,
        "deviation": None,
    }
    body.update(over)
    return json.dumps(body, ensure_ascii=False)


PLAN = Plan(
    objective="两周做到 +10%",
    steps=[
        PlanStep(what="先建 20% 仓位", why="回撤还有空间"),
        PlanStep(what="破 3% 就减半", why="保护本金"),
    ],
    stop_conditions=["回撤 5%"],
)


@pytest.fixture
async def db(tmp_path, monkeypatch):
    await database.close_db()
    url = f"sqlite+aiosqlite:///{tmp_path.as_posix()}/bianca.db"
    monkeypatch.setattr(database, "get_settings", lambda: Settings(database_url=url))
    await database.init_db()
    yield
    await database.close_db()


def _state() -> dict:
    return {
        "equity": 1000.0,
        "drawdown_pct": 0.02,
        "positions": [{"asset": "USDT", "quantity": 1000.0}],
        "market_data": {"symbol": "BTCUSDT", "last": 65000.0},
    }


# ---------------------------------------------------------------- 正常决策


@pytest.mark.asyncio
async def test_a_usable_signal_comes_back_and_is_logged(db):
    mgr, seen = _manager([_json()])

    run = await mgr.decide(
        "目标简报", PLAN, _state(), goal_id="g-1", plan_id="p-1"
    )

    assert run.decision.action == "BUY"
    assert run.decision.amount == pytest.approx(12.5)
    assert run.decision.plan_step == 1
    assert run.decision_id

    row = (await DecisionRepository().list_recent(limit=1))[0]
    assert row.id == run.decision_id
    assert row.goal_id == "g-1"
    assert row.plan_id == "p-1"
    assert row.plan_step == 1
    assert json.loads(row.parsed_signal)["action"] == "BUY"
    assert row.raw_output == _json()
    # 完整 messages，而不是一行摘要
    prompt = json.loads(row.prompt)
    assert prompt[0]["role"] == "system"
    assert "目标简报" in prompt[1]["content"]


@pytest.mark.asyncio
async def test_the_prompt_shows_the_plan_and_the_state(db):
    mgr, seen = _manager([_json()])

    await mgr.decide("目标简报", PLAN, _state())

    user = seen[0][1]["content"]
    assert "1. 先建 20% 仓位" in user
    assert "回撤还有空间" in user
    assert "1000.0" in user
    assert "计划（指导，不是脚本）" in user


@pytest.mark.asyncio
async def test_hold_needs_no_symbol_or_amount(db):
    mgr, _ = _manager([json.dumps({"action": "HOLD", "reason": "行情不明，先不动"})])

    run = await mgr.decide("目标简报", PLAN, _state())

    assert run.decision.action == "HOLD"
    assert run.decision.symbol is None


@pytest.mark.asyncio
async def test_the_action_is_case_normalised_but_nothing_else_is(db):
    mgr, _ = _manager([_json(action="buy")])

    run = await mgr.decide("目标简报", PLAN, _state())

    assert run.decision.action == "BUY"


@pytest.mark.asyncio
async def test_tool_calls_come_before_the_signal(db):
    mgr, seen = _manager([_deepseek_call("get_drawdown", {}), _json()])

    run = await mgr.decide("目标简报", PLAN, _state())

    assert run.used_tools == ["get_drawdown"]
    assert run.invocations[0].ok is True
    assert run.rounds == 2
    # 工具结果回进了上下文，模型不是"凭记忆"给的信号
    assert any("get_drawdown" in m.get("content", "") for m in run.transcript)


# ---------------------------------------------------------------- 解不出来 = HOLD


@pytest.mark.asyncio
async def test_garbage_becomes_hold_and_is_not_retried(db):
    """**关键**：执行阶段不给第二次机会 —— 多问一轮，等于敞口在无人决策下多留一轮。"""
    mgr, seen = _manager(["我觉得可以买一点，但没想好多少"])

    run = await mgr.decide("目标简报", PLAN, _state())

    assert run.decision.action == "HOLD"
    assert "按 HOLD" in run.decision.reason
    assert run.parse_error
    assert run.rounds == 1
    assert len(seen) == 1

    row = (await DecisionRepository().list_recent(limit=1))[0]
    assert json.loads(row.parsed_signal)["action"] == "HOLD"
    assert row.raw_output == "我觉得可以买一点，但没想好多少"


@pytest.mark.asyncio
async def test_a_buy_without_an_amount_is_never_filled_in(db):
    """**关键**：没给 amount 就拿上限补上，是系统替模型下单。"""
    mgr, _ = _manager([_json(amount=None)])

    run = await mgr.decide("目标简报", PLAN, _state())

    assert run.decision.action == "HOLD"
    assert "amount" in run.decision.reason
    assert run.parse_error and "amount" in run.parse_error


@pytest.mark.asyncio
async def test_a_buy_without_a_symbol_is_refused_the_same_way(db):
    mgr, _ = _manager([_json(symbol="")])

    run = await mgr.decide("目标简报", PLAN, _state())

    assert run.decision.action == "HOLD"
    assert "symbol" in run.decision.reason


@pytest.mark.asyncio
async def test_an_out_of_range_plan_step_is_not_accepted(db):
    """计划第几步从 1 开始；`0` 说明它没真的对上某一步。"""
    mgr, _ = _manager([_json(plan_step=0)])

    run = await mgr.decide("目标简报", PLAN, _state())

    assert run.decision.action == "HOLD"
    assert run.parse_error


@pytest.mark.asyncio
async def test_a_dead_model_still_leaves_a_hold_and_a_row(db):
    mgr, _ = _manager([httpx.ConnectError("connection refused")])

    run = await mgr.decide("目标简报", PLAN, _state())

    assert run.decision.action == "HOLD"
    assert "跟模型说话失败" in run.decision.reason
    rows = await DecisionRepository().list_recent(limit=5)
    assert len(rows) == 1
    assert rows[0].goal_id is None


@pytest.mark.asyncio
async def test_run_out_of_rounds_is_still_a_hold(db):
    mgr, _ = _manager([_deepseek_call("get_drawdown", {})])

    run = await mgr.decide("目标简报", PLAN, _state(), max_rounds=2)

    assert run.decision.action == "HOLD"
    assert any("轮" in e for e in run.errors)


@pytest.mark.asyncio
async def test_an_unwritable_decision_log_stops_the_turn(db):
    """决策写不进去就不该继续 —— 一个没法复盘的自动交易比不做更糟。"""
    class Broken:
        async def save(self, **_kwargs):
            raise RuntimeError("库锁住了")

    mgr, _ = _manager([_json()])

    with pytest.raises(RuntimeError):
        await mgr.decide("目标简报", PLAN, _state(), decisions=Broken())
