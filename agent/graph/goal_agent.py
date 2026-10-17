"""一轮目标 tick：终止判据 → 保护 → 经理决策 → 风控 → 执行（§6.6 / D-4）。

这一层是阶段 D 的收口。它只做编排，不做判断：

  · **什么时候停**由 `evaluate_termination()` 判（代码，不看模型）；
  · **能不能过闸**由 `run_risk_agent()` 判（目标快照里的参数）；
  · **做什么**才轮到经理（只读工具 + 一个信号）；
  · **成交没有**由交易所回执说了算（`run_execute_agent()`）。

顺序不能换。先终止再决策，是因为"这一轮已经到了终点"的时候，再问模型
想做什么就是在为一个已经结束的目标开新仓；先保护再开仓，是因为同一轮里
刚建仓就被平掉没有任何意义。

**预算上限（ADR-041）不拦保护动作。** 当日额度用完时不再问经理、不再开新仓，
但破线巡检照跑 —— 把止损挡在"今天额度用完了"后面，是最糟的一种省钱。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from agent.config import Settings, get_settings
from agent.dialogue.session import goal_brief
from agent.dialogue.termination import (
    GoalTerms,
    TerminationKind,
    TerminationVerdict,
    apply_termination,
    evaluate_termination,
    target_equity_of,
)
from agent.exchange.spot_demo import SpotDemoExchange
from agent.graph.execute_agent import run_execute_agent
from agent.graph.risk_agent import run_risk_agent
from agent.graph.state import TradeState
from agent.graph.supervisor import _capture_equity_snapshot, _force_close_if_needed
from agent.manager.loop import Manager
from agent.manager.schemas import Plan
from agent.portfolio.valuation import positions_to_rows
from agent.risk.close import ORIGIN_GOAL_ACHIEVED, force_close
from agent.risk.engine import PortfolioInputs, load_portfolio_inputs
from agent.risk.params import RiskParams
from agent.storage.models import GoalLog
from agent.storage.repository import GoalRepository, OrderRepository, PlanRepository

logger = logging.getLogger(__name__)

#: 达成清仓后算"真的平掉了"的状态。`partial` 还剩仓位，`unknown` 可能成交、
#: 也可能没有 —— 两种都不能拿"Achieved"去盖住一个还存在的持仓。
_FULLY_CLOSED = "filled"


async def run_goal_tick(
    *,
    goal_id: str | None = None,
    settings: Settings | None = None,
    market_data: dict[str, Any] | None = None,
    manager: Manager | None = None,
    inputs: PortfolioInputs | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """跑一轮 active 目标。没有 active 目标时**什么都不做**。

    返回的 dict 是给 runner / API 看的一轮结论，至少含 `status`。
    每个分支都写清"这一轮为什么是这个结局"，不做静默空转。
    """
    cfg = settings or get_settings()
    goals = GoalRepository()

    row = await goals.get(goal_id) if goal_id else await goals.active_goal()
    if row is None:
        return _result("no_active_goal", message="现在没有正在执行的目标")
    if row.status != "active":
        # 手动 tick 也不许在一个已结束/未确认的目标上交易。
        return _result(
            "goal_not_active",
            row,
            message=f"目标 {row.id} 的状态是 {row.status}，不执行",
        )

    terms = GoalTerms.from_row(row)
    params = RiskParams.from_snapshot(json.loads(row.risk_snapshot or ""))
    now = _aware_now(now)

    # 每轮先采一张权益快照：终止判据和风控看的是同一份"现在"。
    # 采集失败只记日志（同 supervisor）—— 拿不到新快照时判据会看到"未知"，
    # 而"未知"不会触发达成或破线，只会让这一轮更保守。
    await _capture_equity_snapshot()
    if inputs is None:
        inputs = await load_portfolio_inputs(cfg)

    verdict = evaluate_termination(
        terms, equity=inputs.equity, drawdown_pct=inputs.drawdown_pct, now=now
    )
    common: dict[str, Any] = {
        "goal_id": row.id,
        "plan_id": row.plan_id,
        "equity": inputs.equity,
        "drawdown_pct": inputs.drawdown_pct,
    }

    # ---- ① 终止判据（代码）------------------------------------------------
    if verdict.terminates:
        return await _settle_termination(
            verdict,
            row=row,
            params=params,
            settings=cfg,
            common=common,
        )

    # ---- ② 账户级保护（与 LLM 说了什么无关，ADR-027）-----------------------
    patrol = await _force_close_if_needed(
        cfg, params=params, goal_id=row.id, plan_id=row.plan_id
    )

    # ---- ③ 预算（ADR-041）-------------------------------------------------
    used = await OrderRepository().count_maybe_reached_since(_day_start(now).isoformat())
    if used >= cfg.max_orders_per_day:
        return {
            **common,
            "status": "budget_exhausted",
            "force_close": patrol,
            "message": (
                f"今天已经提交 {used} 笔（上限 {cfg.max_orders_per_day}），"
                "本轮不再开新仓；破线巡检照常"
            ),
        }

    # ---- ④ 经理决策（只读工具 + 一个信号）---------------------------------
    plan = await _load_plan(row.plan_id)
    manager = manager or Manager(settings=cfg)
    state = {
        "equity": inputs.equity,
        "drawdown_pct": inputs.drawdown_pct,
        "peak_equity": inputs.peak_equity,
        "positions": positions_to_rows(inputs.positions),
        "market_data": market_data,
        "target_equity": target_equity_of(terms),
    }
    run = await manager.decide(
        goal_brief(row), plan, state, goal_id=row.id, plan_id=row.plan_id
    )
    decision = run.decision
    assert decision is not None  # decide() 的契约：永远有一个决定
    base = {
        **common,
        "decision_id": run.decision_id,
        "decision": decision.action,
        "force_close": patrol,
    }
    if decision.action == "HOLD":
        return {
            **base,
            "status": "hold",
            "message": decision.reason,
        }

    # ---- ⑤ 行情：**拿不到价格就不下单**，不拿猜测补 ------------------------
    market = await _market_for(decision.symbol, market_data, cfg)
    if market is None:
        return {
            **base,
            "status": "market_data_unavailable",
            "message": (
                f"拿不到 {decision.symbol} 的行情，这一轮不能给风控一个可信的名义金额；"
                "已按未成交处理"
            ),
        }

    signal = {
        "action": decision.action,
        "symbol": decision.symbol,
        "amount": decision.amount,
        "reason": decision.reason,
        # 偏离计划允许，但必须自报（ADR-039）—— 这两个字段跟着 trade_logs 落库。
        "plan_step": decision.plan_step,
        "deviation": decision.deviation,
    }
    trade_state: TradeState = {
        "llm_signal": signal,
        "market_data": market,
        "decision_id": run.decision_id,
        "goal_id": row.id,
        "plan_id": row.plan_id,
        "risk_params": asdict(params),
    }

    # ---- ⑥ 风控（目标快照里的参数，不可绕过，ADR-006 / 021）---------------
    risk_state = await run_risk_agent(trade_state, settings=cfg)
    verdict_risk = risk_state.get("risk_decision") or {}
    if not verdict_risk.get("approved"):
        return {
            **base,
            "status": "risk_rejected",
            "trade_log_id": risk_state.get("trade_log_id"),
            "risk_reason": verdict_risk.get("reason"),
            "message": verdict_risk.get("reason") or "风控拒绝",
        }

    # ---- ⑦ 执行（幂等 / 回查 / 对账）---------------------------------------
    executed = await run_execute_agent(risk_state, settings=cfg)
    return {
        **base,
        "status": executed.get("status"),
        "trade_log_id": executed.get("trade_log_id") or risk_state.get("trade_log_id"),
        "order_result": executed.get("order_result"),
        "message": executed.get("message"),
    }


async def _settle_termination(
    verdict: TerminationVerdict,
    *,
    row: GoalLog,
    params: RiskParams,
    settings: Settings,
    common: dict[str, Any],
) -> dict[str, Any]:
    """判定终止了 → 先按判据收尾，再转终态。"""
    info = _termination_info(verdict)

    if verdict.kind is TerminationKind.DEADLINE:
        # 到期不强制清仓：目标结束，但账户级巡检仍由 runner 每 tick 继续
        # （ADR-027）。把"目标结束"读成"仓位没人管了"是这里最容易犯的错。
        ended = await apply_termination(verdict, goal_id=row.id)
        return {
            **common,
            "status": ended.status,
            "termination": info,
            "message": verdict.reason,
        }

    if verdict.kind is TerminationKind.DRAWDOWN:
        # **先平仓、再转终态。** 转完终态这一轮就结束了；反过来会留下
        # 一个"已中止、仓位还在"的中间态，而下一轮不一定会这么快到。
        closes = await _force_close_if_needed(
            settings, params=params, goal_id=row.id, plan_id=row.plan_id
        )
        ended = await apply_termination(verdict, goal_id=row.id)
        return {
            **common,
            "status": ended.status,
            "termination": info,
            "force_close": closes,
            "message": verdict.reason,
        }

    # 达成：全平之后才允许写 achieved（ADR-038）。
    if not params.force_close_enabled:
        return {
            **common,
            "status": "achieved_close_pending",
            "termination": info,
            "message": (
                "目标已达成，但 force_close_enabled=false，无法执行清仓；"
                "在仓位还在的情况下不写 achieved（ADR-038）"
            ),
        }

    try:
        closes = await force_close(
            settings=settings,
            params=params,
            goal_id=row.id,
            plan_id=row.plan_id,
            close_all=True,
            origin=ORIGIN_GOAL_ACHIEVED,
        )
    except Exception as exc:  # noqa: BLE001 — 清仓自己崩了，不能假装已达成
        logger.exception("达成清仓失败")
        return {
            **common,
            "status": "achieved_close_pending",
            "termination": info,
            "message": f"目标已达成，但清仓时出错，暂不写 achieved：{exc}",
        }

    unfinished = [c for c in closes if c.get("status") != _FULLY_CLOSED]
    if unfinished:
        return {
            **common,
            "status": "achieved_close_pending",
            "termination": info,
            "force_close": closes,
            "message": (
                "目标已达成，但清仓没有全部落定（"
                + "、".join(
                    f"{c.get('symbol')}={c.get('status')}" for c in unfinished
                )
                + "），下一轮继续；在仓位还在的情况下不写 achieved"
            ),
        }

    ended = await apply_termination(verdict, goal_id=row.id)
    return {
        **common,
        "status": ended.status,
        "termination": info,
        "force_close": closes,
        "message": verdict.reason,
    }


async def _market_for(
    symbol: str | None, market_data: dict[str, Any] | None, settings: Settings
) -> dict[str, Any] | None:
    """拿到决策标的的行情。拿不到就返回 None，**绝不拿别的标的的价顶替**。"""
    if not symbol:
        return None
    if market_data and market_data.get("symbol") == symbol:
        return market_data
    if not settings.binance_configured:
        return None
    try:
        async with SpotDemoExchange(settings) as demo:
            return await demo.fetch_market_context(symbol)
    except Exception as exc:  # noqa: BLE001 — 行情拿不到时不下单，这是保守方向
        logger.warning("拉取 %s 行情失败，本轮不下单：%s", symbol, exc)
        return None


async def _load_plan(plan_id: str | None) -> Plan | None:
    """读挂到目标上的那份计划。**读不出来就当没有计划**，不阻止执行。

    计划是指导（ADR-039）：行在库里但解析不了，是记录出了问题，不是
    "这一轮不能交易"的理由；管理决策仍然要基于目标和当前状态做出。
    但会留日志 —— 静默地"当成没有计划"会让复盘时以为它本来就没计划。
    """
    if not plan_id:
        return None
    attempt = await PlanRepository().get(plan_id)
    if attempt is None or not attempt.plan:
        logger.warning("目标挂了 plan_id=%s，但 plans 表里没有可用计划", plan_id)
        return None
    try:
        return Plan.model_validate_json(attempt.plan)
    except ValueError as exc:
        logger.warning("计划 %s 解析失败，本轮按无计划执行：%s", plan_id, exc)
        return None


def _termination_info(verdict: TerminationVerdict) -> dict[str, Any]:
    return {
        "kind": verdict.kind.value if verdict.kind is not None else None,
        "status": verdict.status.value if verdict.status is not None else None,
        "reason": verdict.reason,
        "target_equity": verdict.target_equity,
    }


def _aware_now(now: datetime | None) -> datetime:
    value = now or datetime.now(UTC)
    if value.tzinfo is None:
        raise ValueError("now 必须带时区 —— 猜一个时区等于猜结算日")
    return value.astimezone(UTC)


def _day_start(now: datetime) -> datetime:
    """预算的"今天"按 UTC 算，与库里所有时间戳同一口径。"""
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _result(status: str, row: GoalLog | None = None, *, message: str) -> dict[str, Any]:
    return {
        "status": status,
        "goal_id": row.id if row is not None else None,
        "plan_id": row.plan_id if row is not None else None,
        "message": message,
    }
