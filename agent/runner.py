from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from agent.config import Settings, get_settings
from agent.graph.goal_agent import run_goal_tick
from agent.graph.supervisor import _force_close_if_needed
from agent.portfolio.pnl import daily_pnl as compute_daily_pnl
from agent.storage.repository import GoalRepository

logger = logging.getLogger(__name__)


@dataclass
class RunnerSnapshot:
    running: bool = False
    last_tick: str | None = None
    last_status: str | None = None
    last_error: str | None = None
    tick_count: int = 0
    # None 表示**算不出来**（今天还没有权益基准），不是 0 —— 见 portfolio/pnl.py
    daily_pnl: float | None = None
    tick_interval: int = 300
    llm_auto_execute: bool = True


class AgentRunner:
    """后台循环：**只跑 active 目标**（§6.6 D-5）。

    没有 active 目标时不再跑旧的 `run_agent_tick()` —— 那条路径没有冻结的
    风控参数，后台自动交易会变成"没有边界地交易"（ADR-018 / ADR-021）。
    但**账户级破线巡检不停**（ADR-027）：目标结束后留下的仓位仍然需要止损。
    巡检不会开仓，只平已经破线的仓。
    """

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
        self._task = asyncio.create_task(self._loop(), name="bianca-agent-runner")
        logger.info("Agent runner started (interval=%ss)", settings.agent_tick_interval)

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
        logger.info("Agent runner stopped")

    async def get_snapshot(self) -> RunnerSnapshot:
        settings = get_settings()
        snapshot = await compute_daily_pnl()
        return RunnerSnapshot(
            running=self._snapshot.running,
            last_tick=self._snapshot.last_tick,
            last_status=self._snapshot.last_status,
            last_error=self._snapshot.last_error,
            tick_count=self._snapshot.tick_count,
            daily_pnl=snapshot.pnl,
            tick_interval=settings.agent_tick_interval,
            llm_auto_execute=settings.llm_auto_execute,
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
            goal = await GoalRepository().active_goal()
            if goal is None:
                patrol = await _force_close_if_needed(settings)
                result: dict = {"status": "no_active_goal"}
                if patrol:
                    result["force_close"] = patrol
            else:
                result = await run_goal_tick(goal_id=goal.id, settings=settings)
            self._snapshot.last_tick = datetime.now(UTC).isoformat()
            self._snapshot.last_status = result.get("status")
            self._snapshot.last_error = None
            self._snapshot.tick_count += 1
            logger.info("Agent tick #%s status=%s", self._snapshot.tick_count, self._snapshot.last_status)
        except Exception as exc:  # noqa: BLE001 — keep loop alive
            logger.exception("Agent tick failed")
            self._snapshot.last_tick = datetime.now(UTC).isoformat()
            self._snapshot.last_error = str(exc)
            self._snapshot.tick_count += 1


_runner: AgentRunner | None = None


def get_runner() -> AgentRunner:
    global _runner
    if _runner is None:
        _runner = AgentRunner()
    return _runner
