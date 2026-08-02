from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.storage.database import get_session_factory
from agent.storage.models import AgentConfigRow, DecisionLog, RiskEvent, SessionSummaryRow, TradeLog


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


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
        session: AsyncSession | None = None,
    ) -> DecisionLog:
        row = DecisionLog(
            id=decision_id,
            model_used=model_used,
            prompt_summary=prompt_summary,
            raw_output=raw_output,
            parsed_signal=json.dumps(parsed_signal, ensure_ascii=False),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
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

    async def list_since(
        self,
        started_at: str,
        ended_at: str | None = None,
    ) -> list[DecisionLog]:
        stmt = select(DecisionLog).where(DecisionLog.created_at >= started_at)
        if ended_at:
            stmt = stmt.where(DecisionLog.created_at <= ended_at)
        stmt = stmt.order_by(DecisionLog.created_at.asc())
        factory = get_session_factory()
        async with factory() as db:
            result = await db.execute(stmt)
            return list(result.scalars().all())

    async def usage_summary_in_window(
        self, started_at: str, ended_at: str | None = None
    ) -> dict[str, int]:
        rows = await self.list_since(started_at, ended_at)
        return {
            "calls": len(rows),
            "prompt_tokens": sum(r.prompt_tokens or 0 for r in rows),
            "completion_tokens": sum(r.completion_tokens or 0 for r in rows),
            "total_tokens": sum(r.total_tokens or 0 for r in rows),
        }

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
    ) -> TradeLog:
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

    async def list_since(
        self,
        started_at: str,
        ended_at: str | None = None,
    ) -> list[TradeLog]:
        stmt = select(TradeLog).where(TradeLog.created_at >= started_at)
        if ended_at:
            stmt = stmt.where(TradeLog.created_at <= ended_at)
        stmt = stmt.order_by(TradeLog.created_at.asc())
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
    _PNL_KEY = "daily_pnl"
    _PNL_DATE_KEY = "daily_pnl_date"

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

    async def get_daily_pnl(self) -> float:
        today = datetime.now(UTC).date().isoformat()
        date_row = await self._get_row(self._PNL_DATE_KEY)
        if date_row is None or date_row.value != today:
            await self._set_row(self._PNL_DATE_KEY, today)
            await self._set_row(self._PNL_KEY, "0")
            return 0.0
        pnl_row = await self._get_row(self._PNL_KEY)
        if pnl_row is None:
            return 0.0
        try:
            return float(pnl_row.value)
        except ValueError:
            return 0.0

    async def set_daily_pnl(self, pnl: float) -> None:
        today = datetime.now(UTC).date().isoformat()
        await self._set_row(self._PNL_DATE_KEY, today)
        await self._set_row(self._PNL_KEY, str(pnl))


class SessionSummaryRepository:
    async def save(
        self,
        *,
        session_id: str,
        started_at: str,
        ended_at: str | None,
        tick_count: int,
        trading_style: str,
        usage_json: dict,
        trades_json: dict,
        pnl_json: dict,
        positions_json: dict,
        loop_closed: bool,
    ) -> SessionSummaryRow:
        row = SessionSummaryRow(
            id=session_id,
            started_at=started_at,
            ended_at=ended_at,
            tick_count=tick_count,
            trading_style=trading_style,
            usage_json=json.dumps(usage_json, ensure_ascii=False),
            trades_json=json.dumps(trades_json, ensure_ascii=False),
            pnl_json=json.dumps(pnl_json, ensure_ascii=False),
            positions_json=json.dumps(positions_json, ensure_ascii=False),
            loop_closed=1 if loop_closed else 0,
            created_at=_utc_now(),
        )
        factory = get_session_factory()
        async with factory() as db:
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row

    async def get_by_id(self, session_id: str) -> SessionSummaryRow | None:
        factory = get_session_factory()
        async with factory() as db:
            return await db.get(SessionSummaryRow, session_id)

    async def list_recent(self, limit: int = 20, offset: int = 0) -> list[SessionSummaryRow]:
        stmt = (
            select(SessionSummaryRow)
            .order_by(SessionSummaryRow.started_at.desc())
            .offset(offset)
            .limit(limit)
        )
        factory = get_session_factory()
        async with factory() as db:
            result = await db.execute(stmt)
            return list(result.scalars().all())

    async def get_latest(self) -> SessionSummaryRow | None:
        stmt = (
            select(SessionSummaryRow)
            .order_by(SessionSummaryRow.created_at.desc())
            .limit(1)
        )
        factory = get_session_factory()
        async with factory() as db:
            result = await db.execute(stmt)
            return result.scalar_one_or_none()
