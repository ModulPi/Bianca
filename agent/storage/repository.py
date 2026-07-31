from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.storage.database import get_session_factory
from agent.storage.models import DecisionLog


class DecisionRepository:
    async def save(
        self,
        *,
        decision_id: str,
        model_used: str,
        prompt_summary: str | None,
        raw_output: str,
        parsed_signal: dict,
        session: AsyncSession | None = None,
    ) -> DecisionLog:
        row = DecisionLog(
            id=decision_id,
            model_used=model_used,
            prompt_summary=prompt_summary,
            raw_output=raw_output,
            parsed_signal=json.dumps(parsed_signal, ensure_ascii=False),
            created_at=datetime.now(UTC).isoformat(),
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
