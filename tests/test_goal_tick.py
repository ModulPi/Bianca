"""D-4：一轮目标 tick（§6.6 D-5）。

这里钉的是**顺序**与**收口条件**，不是单个函数：

  · 终止判据在经理之前，触发就收尾，不再问"你想做什么"；
  · 破线先平仓再转 `aborted`；
  · 达成要**全平落定之后**才写 `achieved` —— 仓位还在时写，等于说假话；
  · 预算只拦开仓，不拦保护；
  · 后台循环只跑 active 目标，没有目标时不再跑旧的自动交易路径。

全程用临时库、脚本化模型、假交易接口，不出网。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from agent.config import Settings
from agent.dialogue.goal import GoalDraft
from agent.graph import goal_agent
from agent.manager.loop import Manager, Reply
from agent.manager.schemas import Plan, PlanStep
from agent.manager.tools import ToolRegistry
from agent.portfolio.valuation import Position
from agent.risk.close import ORIGIN_GOAL_ACHIEVED
from agent.risk.engine import PortfolioInputs
from agent.runner import AgentRunner
from agent.storage import database
from agent.storage.repository import (
    DecisionRepository,
    GoalRepository,
    OrderRepository,
    PlanRepository,
)


@pytest.fixture
async def db(tmp_path, monkeypatch):
    await database.close_db()
    url = f"sqlite+aiosqlite:///{tmp_path.as_posix()}/bianca.db"
    monkeypatch.setattr(database, "get_settings", lambda: Settings(database_url=url))
    await database.init_db()
    yield
    await database.close_db()


def _settings(**over) -> Settings:
    return Settings(_env_file=None, **over)


async def _active_goal(
    *,
    equity: float = 1000.0,
    target_return_pct: float = 0.10,
    target_equity: float | None = None,
    horizon_hours: float = 48.0,
) -> str:
    text = "用 1000 USDT 做 BTCUSDT，赚 10%，最多亏 5%"
    row = await GoalRepository().create(
        raw_text=text,
        draft=GoalDraft(
            raw_text=text,
            symbols=["BTCUSDT"],
            target_return_pct=target_return_pct,
            target_equity=target_equity,
            max_drawdown_pct=0.05,
            horizon_hours=horizon_hours,
        ),
    )
    await GoalRepository().activate(row.id, equity=equity, settings=_settings())
    return row.id


def _inputs(
    *,
    equity: float | None = 1000.0,
    drawdown_pct: float | None = 0.01,
    with_position: bool = True,
) -> PortfolioInputs:
    positions = (
        (Position(asset="BTC", quantity=0.01, price=65000.0, value_quote=650.0),)
        if with_position
        else ()
    )
    return PortfolioInputs(
        equity=equity,
        positions=positions,
        drawdown_pct=drawdown_pct,
        peak_equity=1000.0,
        highs={"BTC": 66000.0},
    )


def _scripted(replies: list):
    seen: list[list[dict]] = []

    async def chat(messages):
        seen.append([dict(m) for m in messages])
        item = replies[min(len(seen) - 1, len(replies) - 1)]
        return item if isinstance(item, Reply) else Reply(content=item)

    return chat, seen


def _manager(replies: list) -> tuple[Manager, list]:
    chat, seen = _scripted(replies)
    return Manager(ToolRegistry(), settings=_settings(), chat=chat), seen


def _json(**over) -> str:
    body = {
        "action": "BUY",
        "symbol": "BTCUSDT",
        "amount": 12.5,
        "reason": "回撤很浅，按计划建第一笔",
        "plan_step": 1,
        "deviation": None,
    }
    body.update(over)
    return json.dumps(body, ensure_ascii=False)


@pytest.fixture(autouse=True)
def _no_snapshot(monkeypatch):
    """每轮 tick 会采权益快照；单测里它属于网络，换成空操作。"""
    calls: list[int] = []

    async def fake():
        calls.append(1)

    monkeypatch.setattr(goal_agent, "_capture_equity_snapshot", fake)
    return calls


@pytest.fixture
def seams(monkeypatch):
    """把 tick 的三个外部动作换成可观测的假实现。"""
    seen: dict[str, object] = {}

    async def fake_patrol(settings, *, params=None, goal_id=None, plan_id=None):
        seen["patrol"] = {"params": params, "goal_id": goal_id, "plan_id": plan_id}
        return []

    async def fake_close(**kwargs):
        seen["close"] = kwargs
        return []

    async def fake_risk(state, *, settings=None):
        seen["risk"] = state
        return {
            **state,
            "trade_log_id": "t-1",
            "risk_decision": {"approved": True, "reason": "ok", "rule": None},
            "status": "risk_approved",
        }

    async def fake_execute(state, *, settings=None):
        seen["execute"] = state
        return {
            **state,
            "status": "filled",
            "message": "订单已提交",
            "order_result": {"status": "filled"},
        }

    monkeypatch.setattr(goal_agent, "_force_close_if_needed", fake_patrol)
    monkeypatch.setattr(goal_agent, "force_close", fake_close)
    monkeypatch.setattr(goal_agent, "run_risk_agent", fake_risk)
    monkeypatch.setattr(goal_agent, "run_execute_agent", fake_execute)
    return seen


MARKET = {"symbol": "BTCUSDT", "last": 65000.0, "change_pct": 0.01}


# ---------------------------------------------------------------- 终止：到期


@pytest.mark.asyncio
async def test_deadline_expires_the_goal_without_trading(db, seams):
    goal_id = await _active_goal(horizon_hours=1.0)
    mgr, seen_model = _manager([_json()])

    result = await goal_agent.run_goal_tick(
        goal_id=goal_id,
        settings=_settings(),
        manager=mgr,
        inputs=_inputs(),
        now=datetime.now(UTC) + timedelta(days=2),
    )

    assert result["status"] == "expired"
    assert (await GoalRepository().get(goal_id)).status == "expired"
    # 到期不强制清仓；经理也没被问过
    assert "close" not in seams
    assert "patrol" not in seams
    assert seen_model == []


@pytest.mark.asyncio
async def test_a_goal_that_is_not_active_is_never_traded(db, seams):
    row = await GoalRepository().create(
        raw_text="还没确认",
        draft=GoalDraft(raw_text="x"),
    )
    mgr, seen_model = _manager([_json()])

    result = await goal_agent.run_goal_tick(
        goal_id=row.id, settings=_settings(), manager=mgr, inputs=_inputs()
    )

    assert result["status"] == "goal_not_active"
    assert seen_model == []
    assert not any(k in seams for k in ("close", "patrol", "risk", "execute"))


# ---------------------------------------------------------------- 终止：破线


@pytest.mark.asyncio
async def test_drawdown_closes_first_and_then_aborts(db, seams, monkeypatch):
    goal_id = await _active_goal()
    mgr, seen_model = _manager([_json()])
    order: list[str] = []

    async def patrol(settings, *, params=None, goal_id=None, plan_id=None):
        order.append("close")
        seams["patrol"] = {"params": params, "goal_id": goal_id, "plan_id": plan_id}
        return []

    original_end = goal_agent.apply_termination

    async def end(*args, **kwargs):
        order.append("end")
        return await original_end(*args, **kwargs)

    monkeypatch.setattr(goal_agent, "_force_close_if_needed", patrol)
    monkeypatch.setattr(goal_agent, "apply_termination", end)

    result = await goal_agent.run_goal_tick(
        goal_id=goal_id,
        settings=_settings(),
        manager=mgr,
        inputs=_inputs(equity=940.0, drawdown_pct=0.06),
    )

    assert result["status"] == "aborted"
    assert (await GoalRepository().get(goal_id)).status == "aborted"
    # **先平仓、再转终态** —— 反过来会留下"已中止、仓位还在"的中间态
    assert order == ["close", "end"]
    assert seams["patrol"]["goal_id"] == goal_id
    assert seams["patrol"]["params"].max_drawdown_pct == pytest.approx(0.05)
    # 已经终止，不再问经理
    assert seen_model == []


# ---------------------------------------------------------------- 终止：达成


@pytest.mark.asyncio
async def test_achievement_closes_everything_before_marking_it(db, seams, monkeypatch):
    goal_id = await _active_goal(equity=1000.0, target_return_pct=0.10)
    mgr, seen_model = _manager([_json()])

    async def closes_all(**kwargs):
        seams["close"] = kwargs
        return [{"symbol": "BTCUSDT", "status": "filled"}]

    monkeypatch.setattr(goal_agent, "force_close", closes_all)

    result = await goal_agent.run_goal_tick(
        goal_id=goal_id,
        settings=_settings(),
        manager=mgr,
        inputs=_inputs(equity=1100.0),
    )

    assert result["status"] == "achieved"
    assert (await GoalRepository().get(goal_id)).status == "achieved"
    close = seams["close"]
    assert close["close_all"] is True
    assert close["origin"] == ORIGIN_GOAL_ACHIEVED
    assert close["goal_id"] == goal_id
    assert close["plan_id"] is None
    assert seen_model == []


@pytest.mark.asyncio
async def test_achievement_is_not_written_while_a_position_may_remain(db, seams, monkeypatch):
    """平仓单 `unknown` = 可能成交也可能没有。**不能拿 achieved 盖住它。**"""
    goal_id = await _active_goal(equity=1000.0, target_return_pct=0.10)
    mgr, _ = _manager([_json()])

    async def uncertain(**kwargs):
        seams["close"] = kwargs
        return [{"symbol": "BTCUSDT", "status": "unknown"}]

    monkeypatch.setattr(goal_agent, "force_close", uncertain)

    result = await goal_agent.run_goal_tick(
        goal_id=goal_id,
        settings=_settings(),
        manager=mgr,
        inputs=_inputs(equity=1100.0),
    )

    assert result["status"] == "achieved_close_pending"
    assert (await GoalRepository().get(goal_id)).status == "active"


@pytest.mark.asyncio
async def test_achievement_waits_when_the_close_switch_is_off(db, seams):
    mgr, _ = _manager([_json()])
    cfg = _settings(force_close_enabled=False)
    # 目标激活时冻结的是配置里的开关；用同一份配置激活。
    row = await GoalRepository().create(
        raw_text="x",
        draft=GoalDraft(
            raw_text="x",
            symbols=["BTCUSDT"],
            target_return_pct=0.10,
            max_drawdown_pct=0.05,
            horizon_hours=48.0,
        ),
    )
    await GoalRepository().activate(row.id, equity=1000.0, settings=cfg)

    result = await goal_agent.run_goal_tick(
        goal_id=row.id, settings=cfg, manager=mgr, inputs=_inputs(equity=1100.0)
    )

    assert result["status"] == "achieved_close_pending"
    assert "force_close_enabled" in result["message"]
    assert (await GoalRepository().get(row.id)).status == "active"
    assert "close" not in seams


# ---------------------------------------------------------------- 决策 → 风控 → 执行


@pytest.mark.asyncio
async def test_a_buy_goes_through_goal_risk_and_execution(db, seams):
    goal_id = await _active_goal()
    mgr, seen_model = _manager([_json(amount=12.5, plan_step=1, deviation=None)])

    result = await goal_agent.run_goal_tick(
        goal_id=goal_id,
        settings=_settings(),
        manager=mgr,
        inputs=_inputs(),
        market_data=MARKET,
    )

    assert result["status"] == "filled"
    assert result["decision_id"]
    # 风控拿到的是目标快照里的边界（不是当前 .env 的）
    risk_state = seams["risk"]
    assert risk_state["risk_params"]["max_trade_amount"] == pytest.approx(50.0)
    assert risk_state["goal_id"] == goal_id
    assert risk_state["decision_id"] == result["decision_id"]
    signal = risk_state["llm_signal"]
    assert signal["plan_step"] == 1
    assert signal["deviation"] is None
    # 执行拿到的是风控后的同一份 state
    assert seams["execute"]["trade_log_id"] == "t-1"

    row = (await DecisionRepository().list_recent(limit=1))[0]
    assert row.goal_id == goal_id
    assert row.plan_step == 1
    assert json.loads(row.parsed_signal)["action"] == "BUY"


@pytest.mark.asyncio
async def test_hold_does_not_touch_risk_or_execution(db, seams):
    goal_id = await _active_goal()
    mgr, _ = _manager([json.dumps({"action": "HOLD", "reason": "在等回踩"})])

    result = await goal_agent.run_goal_tick(
        goal_id=goal_id, settings=_settings(), manager=mgr, inputs=_inputs()
    )

    assert result["status"] == "hold"
    assert "risk" not in seams
    assert "execute" not in seams
    row = (await DecisionRepository().list_recent(limit=1))[0]
    assert json.loads(row.parsed_signal)["action"] == "HOLD"
    assert row.goal_id == goal_id


@pytest.mark.asyncio
async def test_a_risk_rejection_stops_before_execution(db, seams, monkeypatch):
    goal_id = await _active_goal()
    mgr, _ = _manager([_json()])

    async def reject(state, *, settings=None):
        seams["risk"] = state
        return {
            **state,
            "trade_log_id": "t-2",
            "risk_decision": {"approved": False, "reason": "超过单笔上限", "rule": "max_trade_amount"},
            "status": "risk_rejected",
        }

    monkeypatch.setattr(goal_agent, "run_risk_agent", reject)

    result = await goal_agent.run_goal_tick(
        goal_id=goal_id,
        settings=_settings(),
        manager=mgr,
        inputs=_inputs(),
        market_data=MARKET,
    )

    assert result["status"] == "risk_rejected"
    assert result["risk_reason"] == "超过单笔上限"
    assert "execute" not in seams


@pytest.mark.asyncio
async def test_no_price_means_no_order(db, seams):
    """拿不到行情就不下单 —— 不拿"大概的价"去凑风控的名义金额。"""
    goal_id = await _active_goal()
    mgr, _ = _manager([_json()])

    result = await goal_agent.run_goal_tick(
        goal_id=goal_id,
        settings=_settings(),
        manager=mgr,
        inputs=_inputs(),
        market_data=None,
    )

    assert result["status"] == "market_data_unavailable"
    assert "risk" not in seams
    assert "execute" not in seams


@pytest.mark.asyncio
async def test_the_plan_is_loaded_and_shown_to_the_manager(db, seams):
    goal_id = await _active_goal()
    attempt = await PlanRepository().create(
        goal_id=goal_id,
        plan=Plan(
            objective="两周做到 +10%",
            steps=[PlanStep(what="先建 20% 仓位", why="回撤还有空间")],
            stop_conditions=["回撤 5%"],
        ).model_dump_json(),
        plan_shape="json",
        final_text="{}",
        transcript=[],
        invocations=[],
        shapes=[],
        usage=None,
        errors=[],
        rounds=1,
    )
    await GoalRepository().attach_plan(goal_id, attempt.id)
    mgr, seen_model = _manager([_json()])

    await goal_agent.run_goal_tick(
        goal_id=goal_id,
        settings=_settings(),
        manager=mgr,
        inputs=_inputs(),
        market_data=MARKET,
    )

    user = seen_model[0][1]["content"]
    assert "1. 先建 20% 仓位" in user
    assert "回撤 5%" in user


# ---------------------------------------------------------------- 预算（ADR-041）


@pytest.mark.asyncio
async def test_the_daily_budget_blocks_new_orders_but_not_protection(db, seams):
    goal_id = await _active_goal()
    mgr, seen_model = _manager([_json()])

    result = await goal_agent.run_goal_tick(
        goal_id=goal_id,
        settings=_settings(max_orders_per_day=0),
        manager=mgr,
        inputs=_inputs(),
    )

    assert result["status"] == "budget_exhausted"
    assert "上限 0" in result["message"]
    # 经理没被叫；保护巡检照跑
    assert seen_model == []
    assert "patrol" in seams
    assert "risk" not in seams


async def _order(status: str) -> None:
    repo = OrderRepository()
    order = await repo.create_pending(
        order_id=f"o-{status}-{datetime.now(UTC).timestamp()}",
        client_order_id=f"c-{status}-{datetime.now(UTC).timestamp()}",
        symbol="BTCUSDT",
        side="buy",
        order_type="market",
        amount_kind="quote",
        requested_amount=10.0,
    )
    await repo.mark_failed(order.id, status=status, error="测试造数")


@pytest.mark.asyncio
async def test_budget_counts_orders_that_might_have_reached_the_exchange(db):
    since = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    # pending：只是登记，按 ADR-041 不计
    await OrderRepository().create_pending(
        order_id="o-pending",
        client_order_id="c-pending",
        symbol="BTCUSDT",
        side="buy",
        order_type="market",
        amount_kind="quote",
        requested_amount=10.0,
    )
    await _order("submitted")
    await _order("unknown")
    await _order("rejected")
    await _order("canceled")

    # submitted + unknown = 2；rejected/canceled/pending 都不算
    assert await OrderRepository().count_maybe_reached_since(since) == 2


@pytest.mark.asyncio
async def test_a_used_up_budget_counts_unknown_as_used(db, seams):
    goal_id = await _active_goal()
    await _order("unknown")
    mgr, seen_model = _manager([_json()])

    result = await goal_agent.run_goal_tick(
        goal_id=goal_id,
        settings=_settings(max_orders_per_day=1),
        manager=mgr,
        inputs=_inputs(),
    )

    assert result["status"] == "budget_exhausted"
    assert seen_model == []


@pytest.mark.asyncio
async def test_active_goal_ignores_a_newer_proposed_goal(db):
    """后台只认 active —— `proposed` 连截止时刻和冻结参数都还没有。"""
    active_id = await _active_goal()
    await GoalRepository().create(raw_text="后来又说了一个", draft=GoalDraft(raw_text="新目标"))

    row = await GoalRepository().active_goal()

    assert row is not None
    assert row.id == active_id


# ---------------------------------------------------------------- 后台循环分支


@pytest.mark.asyncio
async def test_runner_only_runs_active_goals(monkeypatch):
    calls: dict[str, object] = {}

    async def no_goal():
        return None

    async def patrol(settings, **kwargs):
        calls["patrol"] = settings
        return [{"symbol": "BTCUSDT", "status": "filled"}]

    async def never(**kwargs):  # pragma: no cover — 有目标时才该被调用
        calls["goal_tick"] = kwargs
        raise AssertionError("没有 active 目标时不该跑目标 tick")

    monkeypatch.setattr(GoalRepository, "active_goal", staticmethod(no_goal))
    monkeypatch.setattr("agent.runner._force_close_if_needed", patrol)
    monkeypatch.setattr("agent.runner.run_goal_tick", never)

    runner = AgentRunner()
    await runner._run_one_tick(_settings())

    assert runner._snapshot.last_status == "no_active_goal"
    assert "patrol" in calls
    assert "goal_tick" not in calls


@pytest.mark.asyncio
async def test_runner_hands_an_active_goal_to_the_goal_tick(monkeypatch):
    calls: dict[str, object] = {}

    async def active():
        return SimpleNamespace(id="g-1")

    async def goal_tick(**kwargs):
        calls["goal_tick"] = kwargs
        return {"status": "hold"}

    async def patrol(settings, **kwargs):  # pragma: no cover
        raise AssertionError("有 active 目标时，巡检由 run_goal_tick 负责")

    monkeypatch.setattr(GoalRepository, "active_goal", staticmethod(active))
    monkeypatch.setattr("agent.runner._force_close_if_needed", patrol)
    monkeypatch.setattr("agent.runner.run_goal_tick", goal_tick)

    runner = AgentRunner()
    await runner._run_one_tick(_settings())

    assert runner._snapshot.last_status == "hold"
    assert calls["goal_tick"]["goal_id"] == "g-1"
