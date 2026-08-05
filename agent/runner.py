from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from agent.config import Settings, get_settings
from agent.degradation import clear_degradation, get_effective_execution_mode, record_tick_failure, record_tick_success
from agent.graph.supervisor import run_agent_tick
from agent.markets.registry import get_market_adapter
from agent.storage.repository import AgentConfigRepository
from agent.summary.aggregator import close_session

logger = logging.getLogger(__name__)


@dataclass
class WorkerSnapshot:
    symbol: str
    last_tick: str | None = None
    last_status: str | None = None
    last_error: str | None = None
    tick_count: int = 0


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
    trade_market: str = "crypto"
    symbols: list[str] = field(default_factory=list)
    workers: dict[str, WorkerSnapshot] = field(default_factory=dict)
    degraded: bool = False
    execution_mode: str = "auto"


class AgentRunner:
    """24×7 后台 Agent：多 symbol 并行 tick + 失败自动降级。"""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._snapshot = RunnerSnapshot()
        self._last_interim_snapshot: datetime | None = None

    @property
    def running(self) -> bool:
        return self._snapshot.running

    async def start(self) -> None:
        if self._snapshot.running:
            return
        settings = get_settings()
        if not settings.llm_configured:
            raise RuntimeError("LLM not configured")

        adapter = get_market_adapter(settings.trade_market, settings=settings)
        if not adapter.is_available():
            session = adapter.trading_session()
            raise RuntimeError(f"市场 {settings.trade_market} 不可用: {session.detail}")

        self._stop_event.clear()
        self._snapshot.running = True
        self._snapshot.session_id = str(uuid.uuid4())
        self._snapshot.session_started_at = datetime.now(UTC).isoformat()
        self._snapshot.tick_count = 0
        self._snapshot.trade_market = settings.trade_market
        self._snapshot.symbols = settings.resolved_agent_symbols[: settings.agent_max_parallel]
        self._snapshot.workers = {sym: WorkerSnapshot(symbol=sym) for sym in self._snapshot.symbols}
        self._last_interim_snapshot = None
        from agent.metrics import set_agent_running

        set_agent_running(True)
        from agent.validation.paper_gate import assert_demo_mode_for_trading, ensure_validation_running

        await assert_demo_mode_for_trading()
        await ensure_validation_running(settings=settings)
        if self._snapshot.session_id and self._snapshot.session_started_at:
            from agent.cache.redis_client import set_active_session

            await set_active_session(self._snapshot.session_id, self._snapshot.session_started_at)
        self._task = asyncio.create_task(self._loop(), name="bianca-agent-runner")
        if settings.market_stream_enabled:
            from agent.market.stream_manager import get_stream_manager

            await get_stream_manager().start(self._snapshot.symbols, settings)
        logger.info(
            "Agent runner started market=%s symbols=%s interval=%ss session=%s",
            settings.trade_market,
            self._snapshot.symbols,
            settings.agent_tick_interval,
            self._snapshot.session_id,
        )

    async def stop(self) -> None:
        if not self._snapshot.running and self._task is None:
            return
        self._stop_event.set()
        if get_settings().market_stream_enabled:
            from agent.market.stream_manager import get_stream_manager

            await get_stream_manager().stop()
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
            except Exception:  # noqa: BLE001
                logger.exception("Failed to persist session summary")
        self._snapshot.session_id = None
        self._snapshot.session_started_at = None
        self._snapshot.workers = {}
        from agent.metrics import set_agent_running

        set_agent_running(False)
        logger.info("Agent runner stopped")

    async def recover(self) -> None:
        """人工恢复：清除自动降级，回到 auto（需 .env EXECUTION_MODE=auto）。"""
        await clear_degradation()
        for sym in self._snapshot.symbols:
            await record_tick_success(sym)
        logger.info("Agent degradation cleared by operator")

    async def get_snapshot(self) -> RunnerSnapshot:
        settings = get_settings()
        pnl_repo = AgentConfigRepository()
        daily_pnl = await pnl_repo.get_daily_pnl()
        from agent.degradation import is_degraded

        degraded = await is_degraded()
        execution_mode = await get_effective_execution_mode(settings)
        return RunnerSnapshot(
            running=self._snapshot.running,
            last_tick=self._snapshot.last_tick,
            last_status=self._snapshot.last_status,
            last_error=self._snapshot.last_error,
            tick_count=self._snapshot.tick_count,
            daily_pnl=daily_pnl,
            tick_interval=settings.agent_tick_interval,
            llm_auto_execute=execution_mode != "signal_only",
            session_id=self._snapshot.session_id,
            session_started_at=self._snapshot.session_started_at,
            trade_market=settings.trade_market,
            symbols=list(self._snapshot.symbols),
            workers=dict(self._snapshot.workers),
            degraded=degraded,
            execution_mode=execution_mode,
        )

    async def _loop(self) -> None:
        settings = get_settings()
        interval = settings.agent_tick_interval
        try:
            while not self._stop_event.is_set():
                symbols = self._snapshot.symbols or settings.resolved_agent_symbols[: settings.agent_max_parallel]
                await asyncio.gather(
                    *[self._run_one_tick(settings, symbol) for symbol in symbols],
                    return_exceptions=True,
                )
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                    break
                except TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise
        finally:
            self._snapshot.running = False

    async def _run_one_tick(self, settings: Settings, symbol: str) -> None:
        worker = self._snapshot.workers.setdefault(symbol, WorkerSnapshot(symbol=symbol))
        try:
            thread_id = f"{self._snapshot.session_id}:{symbol}" if self._snapshot.session_id else symbol
            result = await run_agent_tick(
                settings=settings,
                thread_id=thread_id,
                session_id=self._snapshot.session_id,
                symbol=symbol,
            )
            now = datetime.now(UTC).isoformat()
            status = result.get("status")
            worker.last_tick = now
            worker.last_status = status
            worker.last_error = None
            worker.tick_count += 1
            self._snapshot.last_tick = now
            self._snapshot.last_status = status
            self._snapshot.last_error = None
            self._snapshot.tick_count += 1
            await record_tick_success(symbol)
            from agent.metrics import record_agent_tick

            record_agent_tick(status)
            logger.info("Agent tick %s #%s status=%s", symbol, worker.tick_count, status)
            await self._maybe_persist_interim_snapshot(settings)
            if settings.agent_stop_on_loop_closed:
                await self._maybe_stop_on_loop_closed(settings)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Agent tick failed symbol=%s", symbol)
            now = datetime.now(UTC).isoformat()
            worker.last_tick = now
            worker.last_error = str(exc)
            worker.tick_count += 1
            self._snapshot.last_tick = now
            self._snapshot.last_error = str(exc)
            self._snapshot.tick_count += 1
            await record_tick_failure(symbol, str(exc), settings=settings)
            from agent.metrics import record_agent_tick

            record_agent_tick("error")

    async def _maybe_persist_interim_snapshot(self, settings: Settings) -> None:
        if not self._snapshot.session_id or not self._snapshot.session_started_at:
            return
        interval = settings.session_snapshot_interval_minutes
        now = datetime.now(UTC)
        if self._last_interim_snapshot is not None:
            elapsed = (now - self._last_interim_snapshot).total_seconds() / 60
            if elapsed < interval:
                return
        from agent.summary.aggregator import save_interim_snapshot

        try:
            await save_interim_snapshot(
                session_id=self._snapshot.session_id,
                started_at=self._snapshot.session_started_at,
                tick_count=self._snapshot.tick_count,
                last_status=self._snapshot.last_status,
                settings=settings,
            )
            self._last_interim_snapshot = now
        except Exception:  # noqa: BLE001
            logger.exception("Failed to persist interim session snapshot")

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
            logger.info("Loop closed, stopping agent (AGENT_STOP_ON_LOOP_CLOSED=true)")
            self._stop_event.set()


_runner: AgentRunner | None = None


def get_runner() -> AgentRunner:
    global _runner
    if _runner is None:
        _runner = AgentRunner()
    return _runner
