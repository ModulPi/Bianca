from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.storage.database import get_session_factory
from agent.storage.models import (
    AgentConfigRow,
    DecisionLog,
    EquitySnapshotLog,
    OrderLog,
    RiskEvent,
    TradeLog,
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def decision_context(
    market_data: dict | None,
    *,
    equity: float | None = None,
    positions: list[dict] | None = None,
    highs: dict | None = None,
    drawdown_pct: float | None = None,
    extra: dict | None = None,
) -> dict:
    """决策时刻的完整记录。**所有路径都用这一个形状。**

    统一形状的意义在事后：HOLD 那些轮、被风控拦下的那些轮、强制平仓那些轮，
    全都在同一张表里用同一种结构回答"当时看到了什么、手里有什么"。形状随路径变
    的话，查的时候得先猜这一行是哪条路写的。

    `market_data` 恒在（哪怕为空），`portfolio` 只在知道时才有 —— 没有就**不写**，
    而不是写成一堆 0：0 和"不知道"在归因里是两件事。
    """
    record: dict = {"market_data": market_data or {}}
    portfolio: dict = {}
    if equity is not None:
        portfolio["equity"] = equity
    if positions is not None:
        portfolio["positions"] = positions
    if highs is not None:
        portfolio["highs"] = highs
    if drawdown_pct is not None:
        portfolio["drawdown_pct"] = drawdown_pct
    if portfolio:
        record["portfolio"] = portfolio
    if extra:
        record.update(extra)
    return record


def _as_json(value: dict | list | str | None) -> str | None:
    """归因字段统一走这里。

    `None` 保持 `None` 而不是 `"{}"` / `"null"` —— 前者是"没记"，后者看起来像
    "记了一个空对象"，事后分不出来。已经序列化过的字符串原样透传，
    免得调用方传什么都要拆一次。
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


class DecisionRepository:
    async def save(
        self,
        *,
        decision_id: str,
        model_used: str,
        prompt_summary: str | None,
        raw_output: str,
        parsed_signal: dict,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        prompt: list[dict[str, str]] | str | None = None,
        prompt_variant: str | None = None,
        market_data: dict | str | None = None,
        goal_id: str | None = None,
        plan_id: str | None = None,
        session: AsyncSession | None = None,
    ) -> DecisionLog:
        """落一条决策记录。

        `prompt` 与 `market_data` 是归因依据（ADR-019/ADR-031），**不是可选装饰**：
        `prompt_summary`（一行）只是给人扫一眼的，靠它答不出"模型当时被怎么问的、
        看着什么说的"。传 dict/list 会自动序列化。
        """
        row = DecisionLog(
            id=decision_id,
            model_used=model_used,
            prompt_summary=prompt_summary,
            raw_output=raw_output,
            parsed_signal=json.dumps(parsed_signal, ensure_ascii=False),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            prompt=_as_json(prompt),
            prompt_variant=prompt_variant,
            market_data=_as_json(market_data),
            goal_id=goal_id,
            plan_id=plan_id,
            created_at=_utc_now(),
        )
        if session is not None:
            session.add(row)
            await session.commit()
            return row

        factory = get_session_factory()
        async with factory() as db:
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row

    async def list_recent(self, limit: int = 50, session: AsyncSession | None = None) -> list[DecisionLog]:
        stmt = select(DecisionLog).order_by(DecisionLog.created_at.desc()).limit(limit)

        if session is not None:
            result = await session.execute(stmt)
            return list(result.scalars().all())

        factory = get_session_factory()
        async with factory() as db:
            result = await db.execute(stmt)
            return list(result.scalars().all())

    async def get_by_id(
        self, decision_id: str, session: AsyncSession | None = None
    ) -> DecisionLog | None:
        stmt = select(DecisionLog).where(DecisionLog.id == decision_id)

        if session is not None:
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

        factory = get_session_factory()
        async with factory() as db:
            result = await db.execute(stmt)
            return result.scalar_one_or_none()

    async def usage_summary(self) -> dict[str, dict[str, int]]:
        """汇总 token 消耗：today（UTC 对齐 created_at）+ total。

        calls 计入全部决策行（含调用失败/超时降级为 HOLD 的）；token 各列
        仅统计有 usage 的成功调用，用 COALESCE 容错 NULL。
        """
        today = datetime.now(UTC).date().isoformat()

        def _agg(where=None):
            stmt = select(
                func.count(DecisionLog.id),
                func.coalesce(func.sum(DecisionLog.prompt_tokens), 0),
                func.coalesce(func.sum(DecisionLog.completion_tokens), 0),
                func.coalesce(func.sum(DecisionLog.total_tokens), 0),
            )
            if where is not None:
                stmt = stmt.where(where)
            return stmt

        factory = get_session_factory()
        async with factory() as db:
            total = (await db.execute(_agg())).one()
            today_row = (
                await db.execute(_agg(DecisionLog.created_at.startswith(today)))
            ).one()

        return {
            "total": {
                "calls": int(total[0]),
                "prompt_tokens": int(total[1]),
                "completion_tokens": int(total[2]),
                "total_tokens": int(total[3]),
            },
            "today": {
                "calls": int(today_row[0]),
                "prompt_tokens": int(today_row[1]),
                "completion_tokens": int(today_row[2]),
                "total_tokens": int(today_row[3]),
            },
        }


class TradeRepository:
    async def save_signal(
        self,
        *,
        trade_id: str,
        signal: dict,
        market_data: dict,
        status: str,
        risk_decision: str,
        risk_reason: str | None = None,
        decision_id: str | None = None,
        quantity: float | None = None,
        price: float | None = None,
        external_order_id: str | None = None,
        order_type: str | None = None,
        context: dict | str | None = None,
        equity: float | None = None,
        positions: list[dict] | str | None = None,
        goal_id: str | None = None,
        plan_id: str | None = None,
    ) -> TradeLog:
        """落一条交易/信号记录。

        `context` 提供**额外的**顶层键（典型是 `decision_context()` 产出的 `portfolio`），
        `market_data` 恒以参数为准。两者合并后的形状对所有路径都一致。
        **不传 context 时整份 `market_data` 也会落库** —— 以前这里只取 `symbol` 与
        `last`、其余全丢（A1），不能留那条静默丢失的路径：少写一列没人会发现，
        少写一整份上下文就是 R2 不成立。
        """
        if isinstance(context, str):
            context_json: str | None = context  # 已经序列化过，原样透传
        else:
            merged = {**(context or {}), "market_data": market_data or {}}
            context_json = _as_json(merged)

        row = TradeLog(
            id=trade_id,
            symbol=signal.get("symbol") or market_data.get("symbol", "BTCUSDT"),
            side=signal.get("action", "HOLD"),
            quantity=quantity,
            price=price or market_data.get("last"),
            order_type=order_type,
            llm_confidence=signal.get("confidence"),
            decision_reason=signal.get("reason", ""),
            risk_decision=risk_decision,
            risk_reason=risk_reason,
            external_order_id=external_order_id,
            decision_id=decision_id,
            status=status,
            created_at=_utc_now(),
            context=context_json,
            equity=equity,
            positions=_as_json(positions),
            goal_id=goal_id,
            plan_id=plan_id,
        )
        factory = get_session_factory()
        async with factory() as db:
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row

    async def update_status(
        self,
        trade_id: str,
        *,
        status: str,
        risk_decision: str | None = None,
        risk_reason: str | None = None,
        quantity: float | None = None,
        price: float | None = None,
        external_order_id: str | None = None,
        order_type: str | None = None,
    ) -> None:
        factory = get_session_factory()
        async with factory() as db:
            row = await db.get(TradeLog, trade_id)
            if row is None:
                return
            row.status = status
            if risk_decision is not None:
                row.risk_decision = risk_decision
            if risk_reason is not None:
                row.risk_reason = risk_reason
            if quantity is not None:
                row.quantity = quantity
            if price is not None:
                row.price = price
            if external_order_id is not None:
                row.external_order_id = external_order_id
            if order_type is not None:
                row.order_type = order_type
            await db.commit()

    async def list_recent(
        self,
        limit: int = 50,
        *,
        symbol: str | None = None,
        side: str | None = None,
        status: str | None = None,
    ) -> list[TradeLog]:
        stmt = select(TradeLog)
        if symbol:
            stmt = stmt.where(TradeLog.symbol == symbol)
        if side:
            stmt = stmt.where(TradeLog.side == side.upper())
        if status:
            stmt = stmt.where(TradeLog.status == status)
        stmt = stmt.order_by(TradeLog.created_at.desc()).limit(limit)
        factory = get_session_factory()
        async with factory() as db:
            result = await db.execute(stmt)
            return list(result.scalars().all())

    async def get_by_id(self, trade_id: str) -> TradeLog | None:
        factory = get_session_factory()
        async with factory() as db:
            return await db.get(TradeLog, trade_id)


class RiskEventRepository:
    async def save(
        self,
        *,
        event_type: str,
        detail: dict,
        related_trade_id: str | None = None,
    ) -> RiskEvent:
        row = RiskEvent(
            id=str(uuid.uuid4()),
            event_type=event_type,
            detail=json.dumps(detail, ensure_ascii=False),
            related_trade_id=related_trade_id,
            created_at=_utc_now(),
        )
        factory = get_session_factory()
        async with factory() as db:
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row

    async def list_recent(self, limit: int = 50) -> list[RiskEvent]:
        stmt = select(RiskEvent).order_by(RiskEvent.created_at.desc()).limit(limit)
        factory = get_session_factory()
        async with factory() as db:
            result = await db.execute(stmt)
            return list(result.scalars().all())


class AgentConfigRepository:
    """通用键值配置表。

    **日盈亏不再存在这里。** 这里曾经有一个 `get_daily_pnl()`，由现金流累加
    （买入即记亏损），并且在新的一天无条件返回 `0.0` —— 那个 0.0 会被日亏熔断
    读成"今天没亏"，熔断静默失效。现在日盈亏是权益口径，由 `agent/portfolio/pnl.py`
    从权益快照算出来；算不出来时是 `None`（未知），不是 0。
    """

    async def _get_row(self, key: str) -> AgentConfigRow | None:
        factory = get_session_factory()
        async with factory() as db:
            return await db.get(AgentConfigRow, key)

    async def _set_row(self, key: str, value: str) -> None:
        factory = get_session_factory()
        async with factory() as db:
            row = await db.get(AgentConfigRow, key)
            if row is None:
                row = AgentConfigRow(key=key, value=value)
                db.add(row)
            else:
                row.value = value
            await db.commit()

    async def get(self, key: str) -> str | None:
        row = await self._get_row(key)
        return None if row is None else row.value

    async def set(self, key: str, value: str) -> None:
        await self._set_row(key, value)


class OrderRepository:
    """订单账本读写（见 `agent/storage/models.py::OrderLog`）。"""

    async def create_pending(
        self,
        *,
        order_id: str,
        client_order_id: str,
        symbol: str,
        side: str,
        order_type: str,
        amount_kind: str,
        requested_amount: float,
        trade_id: str | None = None,
        decision_id: str | None = None,
    ) -> OrderLog:
        """写前登记。**必须先写库再提交**，否则崩在提交与落库之间就无从查证。"""
        now = _utc_now()
        row = OrderLog(
            id=order_id,
            client_order_id=client_order_id,
            trade_id=trade_id,
            decision_id=decision_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            amount_kind=amount_kind,
            requested_amount=requested_amount,
            status="pending",
            created_at=now,
            updated_at=now,
        )
        factory = get_session_factory()
        async with factory() as db:
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row

    async def find_by_client_id(self, client_order_id: str) -> OrderLog | None:
        stmt = select(OrderLog).where(OrderLog.client_order_id == client_order_id)
        factory = get_session_factory()
        async with factory() as db:
            return (await db.execute(stmt)).scalar_one_or_none()

    async def get_by_id(self, order_id: str) -> OrderLog | None:
        factory = get_session_factory()
        async with factory() as db:
            return await db.get(OrderLog, order_id)

    async def _apply(self, order_id: str, **fields: object) -> OrderLog | None:
        factory = get_session_factory()
        async with factory() as db:
            row = await db.get(OrderLog, order_id)
            if row is None:
                return None
            for key, value in fields.items():
                setattr(row, key, value)
            row.updated_at = _utc_now()
            await db.commit()
            await db.refresh(row)
            return row

    async def mark_submitted(
        self,
        order_id: str,
        *,
        exchange_order_id: str | None,
        raw_response: str | None,
    ) -> OrderLog | None:
        return await self._apply(
            order_id,
            status="submitted",
            exchange_order_id=exchange_order_id,
            raw_response=raw_response,
        )

    async def apply_final(self, order_id: str, update: object) -> OrderLog | None:
        """落定终态。`update` 是 `agent.trading.orders.OrderUpdate`。"""
        return await self._apply(
            order_id,
            status=update.status,
            exchange_order_id=update.exchange_order_id,
            filled_qty=update.filled_qty,
            avg_price=update.avg_price,
            cost_quote=update.cost_quote,
            fee=update.fee,
        )

    async def mark_failed(self, order_id: str, *, status: str, error: str) -> OrderLog | None:
        return await self._apply(order_id, status=status, error=error)

    async def record_error(self, order_id: str, *, error: str) -> OrderLog | None:
        """只记错误，**不改进状态** —— 用于"这一轮没成、下一轮还要再试"的情形。

        与 mark_failed 的区别是它不落终态：一笔单还没提交出去、只是这次回查失败，
        记成 `unknown` 会让它永远不能再试（`unknown` 是终态）。
        """
        return await self._apply(order_id, error=error)

    async def list_recent(self, limit: int = 50) -> list[OrderLog]:
        stmt = select(OrderLog).order_by(OrderLog.created_at.desc()).limit(limit)
        factory = get_session_factory()
        async with factory() as db:
            return list((await db.execute(stmt)).scalars().all())


class EquityRepository:
    """权益快照读写（见 `agent/storage/models.py::EquitySnapshotLog`）。"""

    async def save(
        self,
        *,
        cash_quote: float,
        holdings_value: float,
        equity: float,
        positions: list[dict],
        unpriced: list[str],
        gaps: list[dict],
        notes: list[str],
        captured_at: str | None = None,
    ) -> EquitySnapshotLog:
        row = EquitySnapshotLog(
            id=uuid.uuid4().hex,
            captured_at=captured_at or _utc_now(),
            cash_quote=cash_quote,
            holdings_value=holdings_value,
            equity=equity,
            positions=json.dumps(positions, ensure_ascii=False),
            unpriced=json.dumps(unpriced, ensure_ascii=False),
            gaps=json.dumps(gaps, ensure_ascii=False),
            notes=json.dumps(notes, ensure_ascii=False),
        )
        factory = get_session_factory()
        async with factory() as db:
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row

    async def latest(self) -> EquitySnapshotLog | None:
        stmt = select(EquitySnapshotLog).order_by(EquitySnapshotLog.captured_at.desc()).limit(1)
        factory = get_session_factory()
        async with factory() as db:
            return (await db.execute(stmt)).scalar_one_or_none()

    async def first_since(self, since_iso: str) -> EquitySnapshotLog | None:
        """某时刻（含）以来的第一条 —— 用作"今天开始时"的权益基准。"""
        stmt = (
            select(EquitySnapshotLog)
            .where(EquitySnapshotLog.captured_at >= since_iso)
            .order_by(EquitySnapshotLog.captured_at.asc())
            .limit(1)
        )
        factory = get_session_factory()
        async with factory() as db:
            return (await db.execute(stmt)).scalar_one_or_none()

    async def peak(self, since_iso: str | None = None) -> EquitySnapshotLog | None:
        """权益快照里的**最高**一张 —— 回撤的基准（ADR-029）。

        `since_iso` 之后的才参与，用来实现"回撤触发后显式重置"：重置点之前的峰值
        不再作数（否则重置毫无意义，峰值永远还是那个旧高点）。

        没有快照就返回 None。调用方必须把它当"算不出回撤"处理，**不能当 0**。
        """
        stmt = select(EquitySnapshotLog)
        if since_iso:
            stmt = stmt.where(EquitySnapshotLog.captured_at >= since_iso)
        stmt = stmt.order_by(EquitySnapshotLog.equity.desc()).limit(1)
        factory = get_session_factory()
        async with factory() as db:
            return (await db.execute(stmt)).scalar_one_or_none()

    async def list_recent(self, limit: int = 50) -> list[EquitySnapshotLog]:
        stmt = select(EquitySnapshotLog).order_by(EquitySnapshotLog.captured_at.desc()).limit(limit)
        factory = get_session_factory()
        async with factory() as db:
            return list((await db.execute(stmt)).scalars().all())
