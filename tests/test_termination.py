"""D-2：终止判据（§6.6 D-1）。

这一层最重要的不是"三种条件都写得出来"，而是**判不了时不要装成判过了**：
算不出权益、算不出回撤、算不出目标，都只能是"继续执行"，不能是"结束"，
也不能是"没触发所以安全"。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from agent.config import Settings
from agent.dialogue.goal import GoalDraft, GoalStatus
from agent.dialogue.termination import (
    GoalTerms,
    TerminationKind,
    apply_termination,
    evaluate_termination,
    target_equity_of,
)
from agent.storage import database
from agent.storage.repository import GoalRepository

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def _terms(**over) -> GoalTerms:
    base = {
        "deadline_at": "2026-10-20T00:00:00+00:00",
        "baseline_equity": 1000.0,
        "target_return_pct": 0.10,
        "target_equity": None,
        "max_drawdown_pct": 0.05,
    }
    base.update(over)
    return GoalTerms(**base)


def _eval(terms=None, *, equity=1050.0, drawdown_pct=0.01, now=NOW):
    return evaluate_termination(
        terms or _terms(), equity=equity, drawdown_pct=drawdown_pct, now=now
    )


@pytest.fixture
async def db(tmp_path, monkeypatch):
    await database.close_db()
    url = f"sqlite+aiosqlite:///{tmp_path.as_posix()}/bianca.db"
    monkeypatch.setattr(database, "get_settings", lambda: Settings(database_url=url))
    await database.init_db()
    yield
    await database.close_db()


# ---------------------------------------------------------------- 到期


def test_before_the_deadline_nothing_terminates():
    verdict = _eval()

    assert verdict.terminates is False
    assert verdict.status is None
    assert verdict.reason == "未触发任何终止条件"


def test_the_deadline_itself_counts_as_expired():
    """`now >= deadline` —— 截止时刻那一秒已经在窗外了，不能多跑一轮。"""
    verdict = _eval(_terms(deadline_at="2026-10-18T12:00:00+00:00"))

    assert verdict.kind is TerminationKind.DEADLINE
    assert verdict.status is GoalStatus.EXPIRED


def test_deadlines_are_compared_in_their_own_timezone():
    """+08:00 的 20:00 就是 UTC 的 12:00 —— 不能因为字符串不同就判成没到。"""
    verdict = _eval(_terms(deadline_at="2026-10-18T20:00:00+08:00"))

    assert verdict.status is GoalStatus.EXPIRED


def test_a_deadline_without_a_timezone_is_refused():
    """猜时区等于猜截止时刻。宁可炸，不要静默按本地时间算。"""
    with pytest.raises(ValueError) as exc:
        _eval(_terms(deadline_at="2026-10-20T00:00:00"))

    assert "时区" in str(exc.value)


def test_the_deadline_beats_everything_else():
    """窗都关了才发现"其实早达成" —— 按到期结，不按达成结。"""
    verdict = _eval(equity=9999.0, drawdown_pct=0.99, now=NOW, terms=_terms(
        deadline_at="2026-10-01T00:00:00+00:00"
    ))

    assert verdict.status is GoalStatus.EXPIRED


# ---------------------------------------------------------------- 达成


def test_achieved_by_return_when_there_is_no_absolute_target():
    verdict = _eval(equity=1100.0)

    assert verdict.kind is TerminationKind.ACHIEVED
    assert verdict.status is GoalStatus.ACHIEVED
    assert verdict.target_equity == pytest.approx(1100.0)


def test_just_below_the_target_is_not_achieved():
    assert _eval(equity=1099.99).status is None


def test_the_absolute_target_wins_over_the_percentage():
    """ADR-040：用户说「做到 1150」，基准 1000、收益率 50% 时，
    按收益率算会是 1500 —— 那是个用户从没说过的目标。"""
    verdict = _eval(_terms(target_equity=1150.0, target_return_pct=0.50), equity=1150.0)

    assert verdict.status is GoalStatus.ACHIEVED
    assert verdict.target_equity == pytest.approx(1150.0)
    # 两个口径不一致时都要出现在理由里，否则事后对不上账
    assert "1500.00" in verdict.reason
    assert "1150.00" in verdict.reason


def test_when_the_two_ways_agree_no_noise_is_added():
    verdict = _eval(_terms(target_equity=1100.0, target_return_pct=0.10), equity=1100.0)

    assert "按收益率算" not in verdict.reason


def test_no_target_source_means_achievement_cannot_be_judged():
    verdict = _eval(_terms(target_equity=None, baseline_equity=None))

    assert verdict.status is None
    assert "算不出目标权益" in verdict.reason


def test_unknown_current_equity_is_not_zero():
    """权益拿不到时不能算成"还差得远"，也不能算成"已经达成"。"""
    verdict = _eval(equity=None)

    assert verdict.status is None
    assert "当前权益" in verdict.reason


def test_achievement_beats_the_drawdown_breaker():
    """已经站上目标、随后又回撤越线时，结局不是 `aborted` —— 目标确实达成过。"""
    verdict = _eval(equity=1100.0, drawdown_pct=0.90)

    assert verdict.status is GoalStatus.ACHIEVED


# ---------------------------------------------------------------- 破线


def test_drawdown_at_the_limit_aborts():
    verdict = _eval(drawdown_pct=0.05)

    assert verdict.kind is TerminationKind.DRAWDOWN
    assert verdict.status is GoalStatus.ABORTED
    assert "5.00%" in verdict.reason


def test_drawdown_just_below_the_limit_does_not_abort():
    assert _eval(drawdown_pct=0.0499).status is None


def test_unknown_drawdown_is_not_a_breach():
    verdict = _eval(drawdown_pct=None)

    assert verdict.status is None
    assert "回撤" in verdict.reason


def test_target_equity_of_prefers_the_stated_number():
    terms = _terms(target_equity=1200.0, target_return_pct=0.50)

    assert target_equity_of(terms) == pytest.approx(1200.0)


# ---------------------------------------------------------------- 从库里那一行读


def _row(**over) -> SimpleNamespace:
    snapshot = {
        "max_trade_amount": 50.0,
        "daily_loss_limit": 100.0,
        "stop_loss_pct": 0.10,
        "stop_lookback_hours": 24.0,
        "max_drawdown_pct": 0.05,
        "max_position_pct": 0.50,
        "force_close_enabled": True,
    }
    base = {
        "id": "g-1",
        "deadline_at": "2026-10-20T00:00:00+00:00",
        "baseline_equity": 1000.0,
        "target_return_pct": 0.10,
        "target_equity": None,
        "risk_snapshot": json.dumps(snapshot),
    }
    base.update(over)
    return SimpleNamespace(**base)


def test_terms_are_read_from_the_goal_row():
    terms = GoalTerms.from_row(_row())

    assert terms.deadline_at == "2026-10-20T00:00:00+00:00"
    assert terms.max_drawdown_pct == pytest.approx(0.05)
    assert terms.baseline_equity == pytest.approx(1000.0)


def test_an_active_goal_without_a_deadline_is_refused():
    """缺截止时刻的 active 目标永远不会到期 —— 宁可炸，也不要静默永不过期。"""
    with pytest.raises(ValueError) as exc:
        GoalTerms.from_row(_row(deadline_at=None))

    assert "deadline_at" in str(exc.value)


def test_a_broken_risk_snapshot_is_refused():
    with pytest.raises(ValueError):
        GoalTerms.from_row(_row(risk_snapshot="not json"))

    with pytest.raises(ValueError) as exc:
        GoalTerms.from_row(_row(risk_snapshot=json.dumps({"max_drawdown_pct": 0.05})))

    assert "stop_loss_pct" in str(exc.value)


# ---------------------------------------------------------------- 落成终态


async def _active_goal() -> str:
    row = await GoalRepository().create(
        raw_text="用 1000 USDT 做 BTCUSDT，两周赚 10%，最多亏 5%",
        draft=GoalDraft(
            raw_text="x",
            symbols=["BTCUSDT"],
            target_return_pct=0.10,
            max_drawdown_pct=0.05,
            horizon_hours=48.0,
        ),
    )
    await GoalRepository().activate(row.id, equity=1000.0, settings=Settings())
    return row.id


@pytest.mark.asyncio
async def test_applying_a_verdict_ends_the_goal_with_its_reason(db):
    goal_id = await _active_goal()
    verdict = _eval(_terms(deadline_at="2026-10-01T00:00:00+00:00"))

    row = await apply_termination(verdict, goal_id=goal_id)

    assert row.status == GoalStatus.EXPIRED.value
    assert row.ended_at
    assert row.end_reason == verdict.reason


@pytest.mark.asyncio
async def test_a_non_terminating_verdict_cannot_end_a_goal(db):
    goal_id = await _active_goal()

    with pytest.raises(ValueError) as exc:
        await apply_termination(_eval(), goal_id=goal_id)

    # 不能只是"碰巧也抛了个 ValueError" —— 要能分清是**这一步**拒绝的
    assert "没有终止任何东西" in str(exc.value)
    assert (await GoalRepository().get(goal_id)).status == GoalStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_the_state_machine_still_applies(db):
    """proposed 只能去 active/aborted —— 一个从没执行过的目标不能"到期"。
    `end()` 里的 `check_transition` 必须对终止判据同样生效。"""
    row = await GoalRepository().create(raw_text="随便说说")

    with pytest.raises(ValueError):
        await apply_termination(
            _eval(_terms(deadline_at="2026-10-01T00:00:00+00:00")),
            goal_id=row.id,
        )

    assert (await GoalRepository().get(row.id)).status == GoalStatus.PROPOSED.value
