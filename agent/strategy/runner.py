from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from agent.config import get_settings
from agent.storage.repository import StrategyRepository
from agent.strategy.engine import run_strategy_tick

logger = logging.getLogger(__name__)


class StrategyRunner:
    """Background loop: tick all running strategies on interval."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="bianca-strategy-runner")
        logger.info("Strategy runner started")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Strategy runner stopped")

    async def _loop(self) -> None:
        settings = get_settings()
        interval = settings.agent_tick_interval
        repo = StrategyRepository()
        try:
            while not self._stop.is_set():
                running = await repo.list_running()
                for row in running:
                    try:
                        result = await run_strategy_tick(row.id, settings=settings)
                        logger.info("Strategy %s tick: %s", row.name, result.get("status"))
                    except Exception:  # noqa: BLE001
                        logger.exception("Strategy tick failed: %s", row.id)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=interval)
                    break
                except TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise


_strategy_runner: StrategyRunner | None = None


def get_strategy_runner() -> StrategyRunner:
    global _strategy_runner
    if _strategy_runner is None:
        _strategy_runner = StrategyRunner()
    return _strategy_runner
