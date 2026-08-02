from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from agent.config import Settings, get_settings
from agent.graph.supervisor import run_agent_tick
from agent.storage.repository import AgentConfigRepository
from agent.summary.aggregator import close_session

logger = logging.getLogger(__name__)


@dataclass
class RunnerSnapshot:
    running: bool = False
    last_tick: str | None = None
    last_status: str | None = None
    last_error: str | None = None
    tick_count: int = 0
    daily_pnl: float = 0.0
    tick_interval: int = 300
    llm_auto_execute: bool = True
    session_id: str | None = None
    session_started_at: str | None = None


class AgentRunner:
    """Background asyncio loop that triggers LangGraph agent ticks."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._snapshot = RunnerSnapshot()

    @property
    def running(self) -> bool:
        return self._snapshot.running

    async def start(self) -> None:
        if self._snapshot.running:
            return
        settings = get_settings()
        if not settings.llm_configured:
            raise RuntimeError("LLM not configured")

        self._stop_event.clear()
        self._snapshot.running = True
        self._snapshot.session_id = str(uuid.uuid4())
        self._snapshot.session_started_at = datetime.now(UTC).isoformat()
        self._snapshot.tick_count = 0
        from agent.validation.paper_gate import assert_demo_mode_for_trading, ensure_validation_running

        await assert_demo_mode_for_trading()
        await ensure_validation_running(settings=settings)
        if self._snapshot.session_id and self._snapshot.session_started_at:
            from agent.cache.redis_client import set_active_session

            await set_active_session(self._snapshot.session_id, self._snapshot.session_started_at)
        self._task = asyncio.create_task(self._loop(), name="bianca-agent-runner")
        logger.info(
            "Agent runner started (interval=%ss, session=%s)",
            settings.agent_tick_interval,
            self._snapshot.session_id,
        )

    async def stop(self) -> None:
        if not self._snapshot.running and self._task is None:
            return
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._snapshot.running = False
        if self._snapshot.session_id:
            from agent.cache.redis_client import clear_active_session

            await clear_active_session(self._snapshot.session_id)
        if self._snapshot.session_id and self._snapshot.session_started_at:
            try:
                await close_session(
                    session_id=self._snapshot.session_id,
                    started_at=self._snapshot.session_started_at,
                    tick_count=self._snapshot.tick_count,
                    last_status=self._snapshot.last_status,
                    settings=get_settings(),
                )
            except Exception:  # noqa: BLE001 — stop must not fail
                logger.exception("Failed to persist session summary")
        self._snapshot.session_id = None
        self._snapshot.session_started_at = None
        logger.info("Agent runner stopped")

    async def get_snapshot(self) -> RunnerSnapshot:
        settings = get_settings()
        pnl_repo = AgentConfigRepository()
        daily_pnl = await pnl_repo.get_daily_pnl()
        return RunnerSnapshot(
            running=self._snapshot.running,
            last_tick=self._snapshot.last_tick,
            last_status=self._snapshot.last_status,
            last_error=self._snapshot.last_error,
            tick_count=self._snapshot.tick_count,
            daily_pnl=daily_pnl,
            tick_interval=settings.agent_tick_interval,
            llm_auto_execute=settings.llm_auto_execute,
            session_id=self._snapshot.session_id,
            session_started_at=self._snapshot.session_started_at,
        )

    async def _loop(self) -> None:
        settings = get_settings()
        interval = settings.agent_tick_interval
        try:
            while not self._stop_event.is_set():
                await self._run_one_tick(settings)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                    break
                except TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise
        finally:
            self._snapshot.running = False

    async def _run_one_tick(self, settings: Settings) -> None:
        try:
            result = await run_agent_tick(
                settings=settings,
                thread_id=self._snapshot.session_id or "default",
                session_id=self._snapshot.session_id,
            )
            self._snapshot.last_tick = datetime.now(UTC).isoformat()
            self._snapshot.last_status = result.get("status")
            self._snapshot.last_error = None
            self._snapshot.tick_count += 1
            logger.info("Agent tick #%s status=%s", self._snapshot.tick_count, self._snapshot.last_status)
            await self._maybe_stop_on_loop_closed(settings)
        except Exception as exc:  # noqa: BLE001 — keep loop alive
            logger.exception("Agent tick failed")
            self._snapshot.last_tick = datetime.now(UTC).isoformat()
            self._snapshot.last_error = str(exc)
            self._snapshot.tick_count += 1

    async def _maybe_stop_on_loop_closed(self, settings: Settings) -> None:
        if not self._snapshot.session_id or not self._snapshot.session_started_at:
            return
        from agent.summary.aggregator import build_session_summary

        try:
            summary = await build_session_summary(
                session_id=self._snapshot.session_id,
                started_at=self._snapshot.session_started_at,
                ended_at=None,
                tick_count=self._snapshot.tick_count,
                last_status=self._snapshot.last_status,
                settings=settings,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to evaluate loop closure")
            return
        if summary["trades"].get("loop_closed"):
            logger.info("PoC loop closed (>=1 BUY + >=1 SELL filled), stopping agent")
            self._stop_event.set()


_runner: AgentRunner | None = None


def get_runner() -> AgentRunner:
    global _runner
    if _runner is None:
        _runner = AgentRunner()
    return _runner
