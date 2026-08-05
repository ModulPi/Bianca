from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from agent.config import get_settings
from agent.storage.constants import (
    DEFAULT_AGENT_STRATEGY_ID,
    DEFAULT_AGENT_STRATEGY_NAME,
)
from agent.storage.database import get_session_factory, schema_mode
from agent.storage.models import (
    AgentConfigRow,
    DecisionLog,
    PaperValidationRow,
    PendingSignalRow,
    PositionRow,
    RiskEvent,
    SessionSummaryRow,
    StrategyRow,
    TradeLog,
)


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
        if schema_mode() == "mvp":
            settings = get_settings()
            row.strategy_id = DEFAULT_AGENT_STRATEGY_ID
            row.strategy_name = DEFAULT_AGENT_STRATEGY_NAME
            row.execution_mode = settings.resolved_execution_mode
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

    async def count_failed_since(self, since_iso: str) -> int:
        stmt = (
            select(func.count())
            .select_from(TradeLog)
            .where(TradeLog.created_at >= since_iso, TradeLog.status == "failed")
        )
        factory = get_session_factory()
        async with factory() as db:
            result = await db.execute(stmt)
            return int(result.scalar_one())


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

    async def count_recent(self, *, since_iso: str, event_type: str | None = None) -> int:
        stmt = select(func.count()).select_from(RiskEvent).where(RiskEvent.created_at >= since_iso)
        if event_type:
            stmt = stmt.where(RiskEvent.event_type == event_type)
        factory = get_session_factory()
        async with factory() as db:
            result = await db.execute(stmt)
            return int(result.scalar_one())


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

    async def get_config_value(self, key: str) -> str | None:
        row = await self._get_row(key)
        return row.value if row else None

    async def set_config_value(self, key: str, value: str) -> None:
        await self._set_row(key, value)


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


class PendingSignalRepository:
    async def create(
        self,
        *,
        signal: dict,
        market_data: dict,
        decision_id: str | None,
        session_id: str | None,
        ttl_minutes: int,
        strategy_id: str | None = None,
    ) -> PendingSignalRow:
        from datetime import timedelta

        pending_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        expires = now + timedelta(minutes=ttl_minutes)
        row = PendingSignalRow(
            id=pending_id,
            strategy_id=strategy_id,
            signal_json=json.dumps(signal, ensure_ascii=False),
            market_data_json=json.dumps(market_data, ensure_ascii=False),
            decision_id=decision_id,
            session_id=session_id,
            status="pending",
            expires_at=expires.isoformat(),
            created_at=now.isoformat(),
        )
        factory = get_session_factory()
        async with factory() as db:
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row

    async def get_by_id(self, pending_id: str) -> PendingSignalRow | None:
        factory = get_session_factory()
        async with factory() as db:
            return await db.get(PendingSignalRow, pending_id)

    async def list_pending(self, limit: int = 50) -> list[PendingSignalRow]:
        stmt = (
            select(PendingSignalRow)
            .where(PendingSignalRow.status == "pending")
            .order_by(PendingSignalRow.created_at.desc())
            .limit(limit)
        )
        factory = get_session_factory()
        async with factory() as db:
            result = await db.execute(stmt)
            return list(result.scalars().all())

    async def update_status(self, pending_id: str, status: str) -> PendingSignalRow | None:
        factory = get_session_factory()
        async with factory() as db:
            row = await db.get(PendingSignalRow, pending_id)
            if row is None:
                return None
            row.status = status
            await db.commit()
            await db.refresh(row)
            return row

    async def expire_stale(self) -> int:
        now = _utc_now()
        factory = get_session_factory()
        async with factory() as db:
            stmt = select(PendingSignalRow).where(
                PendingSignalRow.status == "pending",
                PendingSignalRow.expires_at < now,
            )
            result = await db.execute(stmt)
            rows = list(result.scalars().all())
            for row in rows:
                row.status = "expired"
            await db.commit()
            return len(rows)


class StrategyRepository:
    async def create(
        self,
        *,
        name: str,
        strategy_type: str,
        execution_mode: str,
        params: dict,
        market: str = "spot",
    ) -> StrategyRow:
        sid = str(uuid.uuid4())
        now = _utc_now()
        row = StrategyRow(
            id=sid,
            name=name,
            type=strategy_type,
            market=market,
            execution_mode=execution_mode,
            params_json=json.dumps(params, ensure_ascii=False),
            state_json="{}",
            status="created",
            created_at=now,
            updated_at=now,
        )
        factory = get_session_factory()
        async with factory() as db:
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row

    async def get_by_id(self, strategy_id: str) -> StrategyRow | None:
        factory = get_session_factory()
        async with factory() as db:
            return await db.get(StrategyRow, strategy_id)

    async def list_all(self, limit: int = 50) -> list[StrategyRow]:
        stmt = select(StrategyRow).order_by(StrategyRow.updated_at.desc()).limit(limit)
        factory = get_session_factory()
        async with factory() as db:
            result = await db.execute(stmt)
            return list(result.scalars().all())

    async def list_running(self) -> list[StrategyRow]:
        stmt = select(StrategyRow).where(StrategyRow.status == "running")
        factory = get_session_factory()
        async with factory() as db:
            result = await db.execute(stmt)
            return list(result.scalars().all())

    async def update(
        self,
        strategy_id: str,
        *,
        name: str | None = None,
        params: dict | None = None,
        execution_mode: str | None = None,
        status: str | None = None,
        started_at: str | None = None,
        stopped_at: str | None = None,
    ) -> StrategyRow | None:
        factory = get_session_factory()
        async with factory() as db:
            row = await db.get(StrategyRow, strategy_id)
            if row is None:
                return None
            if name is not None:
                row.name = name
            if params is not None:
                row.params_json = json.dumps(params, ensure_ascii=False)
            if execution_mode is not None:
                row.execution_mode = execution_mode
            if status is not None:
                row.status = status
            if started_at is not None:
                row.started_at = started_at
            if stopped_at is not None:
                row.stopped_at = stopped_at
            row.updated_at = _utc_now()
            await db.commit()
            await db.refresh(row)
            return row

    async def update_state(self, strategy_id: str, state: dict) -> None:
        factory = get_session_factory()
        async with factory() as db:
            row = await db.get(StrategyRow, strategy_id)
            if row is None:
                return
            row.state_json = json.dumps(state, ensure_ascii=False)
            row.updated_at = _utc_now()
            await db.commit()


class PaperValidationRepository:
    async def create(self, *, started_at: str, strategy_id: str | None = None) -> PaperValidationRow:
        vid = str(uuid.uuid4())
        now = _utc_now()
        row = PaperValidationRow(
            id=vid,
            strategy_id=strategy_id,
            started_at=started_at,
            validated_at=None,
            status="running",
            metrics_json=json.dumps(
                {
                    "cumulative_hours": 0.0,
                    "sessions": 0,
                    "loop_closed_sessions": 0,
                    "buy_filled_total": 0,
                    "sell_filled_total": 0,
                }
            ),
            created_at=now,
        )
        factory = get_session_factory()
        async with factory() as db:
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row

    async def get_active(self) -> PaperValidationRow | None:
        stmt = (
            select(PaperValidationRow)
            .where(PaperValidationRow.status.in_(("running", "passed")))
            .order_by(PaperValidationRow.created_at.desc())
            .limit(1)
        )
        factory = get_session_factory()
        async with factory() as db:
            result = await db.execute(stmt)
            return result.scalar_one_or_none()

    async def get_latest(self) -> PaperValidationRow | None:
        stmt = select(PaperValidationRow).order_by(PaperValidationRow.created_at.desc()).limit(1)
        factory = get_session_factory()
        async with factory() as db:
            result = await db.execute(stmt)
            return result.scalar_one_or_none()

    async def update_metrics(self, validation_id: str, metrics: dict) -> None:
        factory = get_session_factory()
        async with factory() as db:
            row = await db.get(PaperValidationRow, validation_id)
            if row is None:
                return
            row.metrics_json = json.dumps(metrics, ensure_ascii=False)
            await db.commit()

    async def mark_passed(self, validation_id: str) -> None:
        factory = get_session_factory()
        async with factory() as db:
            row = await db.get(PaperValidationRow, validation_id)
            if row is None:
                return
            row.status = "passed"
            row.validated_at = _utc_now()
            await db.commit()

    async def reset(self) -> PaperValidationRow:
        factory = get_session_factory()
        async with factory() as db:
            stmt = select(PaperValidationRow).where(PaperValidationRow.status == "running")
            result = await db.execute(stmt)
            for row in result.scalars().all():
                row.status = "cancelled"
            await db.commit()
        return await self.create(started_at=_utc_now())


class KlineRepository:
    _INSERT_SQL = text(
        """
        INSERT INTO klines (time, symbol, interval, open, high, low, close, volume, trades)
        VALUES (:time, :symbol, :interval, :open, :high, :low, :close, :volume, :trades)
        ON CONFLICT (time, symbol, interval) DO NOTHING
        """
    )

    async def get_latest_time(self, symbol: str, interval: str) -> datetime | None:
        if schema_mode() != "mvp":
            return None
        stmt = text(
            """
            SELECT time FROM klines
            WHERE symbol = :symbol AND interval = :interval
            ORDER BY time DESC
            LIMIT 1
            """
        )
        factory = get_session_factory()
        async with factory() as db:
            result = await db.execute(stmt, {"symbol": symbol, "interval": interval})
            row = result.first()
            if row is None:
                return None
            value = row[0]
            if isinstance(value, datetime):
                return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
            return datetime.fromisoformat(str(value)).astimezone(UTC)

    async def insert_bars(self, bars: list) -> int:
        if schema_mode() != "mvp" or not bars:
            return 0
        payload = [
            {
                "time": bar.time,
                "symbol": bar.symbol,
                "interval": bar.interval,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "trades": bar.trades,
            }
            for bar in bars
        ]
        factory = get_session_factory()
        async with factory() as db:
            result = await db.execute(self._INSERT_SQL, payload)
            await db.commit()
            count = result.rowcount
            if count is None or count < 0:
                return len(bars)
            return count

    async def count(self, symbol: str | None = None, interval: str | None = None) -> int:
        if schema_mode() != "mvp":
            return 0
        clauses = ["1=1"]
        params: dict[str, str] = {}
        if symbol:
            clauses.append("symbol = :symbol")
            params["symbol"] = symbol
        if interval:
            clauses.append("interval = :interval")
            params["interval"] = interval
        stmt = text(f"SELECT COUNT(*) FROM klines WHERE {' AND '.join(clauses)}")
        factory = get_session_factory()
        async with factory() as db:
            result = await db.execute(stmt, params)
            return int(result.scalar_one())


class PositionRepository:
    _UPSERT_SQL = text(
        """
        INSERT INTO positions (
            id, strategy_id, symbol, market, quantity, entry_price, current_price,
            unrealized_pnl, realized_pnl, leverage, created_at, updated_at
        )
        VALUES (
            :id, :strategy_id, :symbol, :market, :quantity, :entry_price, :current_price,
            0, 0, 1, :created_at, :updated_at
        )
        ON CONFLICT (strategy_id, symbol) DO UPDATE SET
            quantity = EXCLUDED.quantity,
            current_price = EXCLUDED.current_price,
            updated_at = EXCLUDED.updated_at
        """
    )

    async def upsert(
        self,
        *,
        strategy_id: str,
        symbol: str,
        quantity: float,
        current_price: float | None = None,
        market: str = "spot",
        updated_at: str | None = None,
    ) -> None:
        if schema_mode() != "mvp":
            return

        now = updated_at or _utc_now()
        entry = float(current_price or 0)
        factory = get_session_factory()
        async with factory() as db:
            existing = await db.execute(
                select(PositionRow.id).where(
                    PositionRow.strategy_id == strategy_id,
                    PositionRow.symbol == symbol,
                )
            )
            row_id = existing.scalar_one_or_none() or str(uuid.uuid4())
            await db.execute(
                self._UPSERT_SQL,
                {
                    "id": row_id,
                    "strategy_id": strategy_id,
                    "symbol": symbol,
                    "market": market,
                    "quantity": quantity,
                    "entry_price": entry,
                    "current_price": current_price,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            await db.commit()

    async def list_by_strategy(
        self, strategy_id: str, *, min_qty: float = 1e-8
    ) -> list[PositionRow]:
        if schema_mode() != "mvp":
            return []

        stmt = (
            select(PositionRow)
            .where(PositionRow.strategy_id == strategy_id)
            .where(PositionRow.quantity >= min_qty)
            .order_by(PositionRow.updated_at.desc())
        )
        factory = get_session_factory()
        async with factory() as db:
            result = await db.execute(stmt)
            return list(result.scalars().all())

    async def list_recent(self, limit: int = 50) -> list[PositionRow]:
        if schema_mode() != "mvp":
            return []

        stmt = select(PositionRow).order_by(PositionRow.updated_at.desc()).limit(limit)
        factory = get_session_factory()
        async with factory() as db:
            result = await db.execute(stmt)
            return list(result.scalars().all())
