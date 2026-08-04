from __future__ import annotations

from agent.api.health_service import build_health_response
from agent.api.schemas import (
    AgentStatusResponse,
    BalanceResponse,
    DashboardPositionItem,
    DashboardSnapshotResponse,
    HealthResponse,
    PendingSignalItem,
    RiskEventItem,
    SessionSummaryResponse,
    TickerResponse,
    TradeLogItem,
    TradingModeResponse,
    UsageSummaryResponse,
    ValidationStatusResponse,
    WorkerStatusItem,
    WorkerTokenUsageItem,
)
from agent.config import Settings, get_settings
from agent.exchange.quotes import (
    balance_to_response,
    fetch_exchange_balance,
    fetch_exchange_tickers,
    format_exchange_error,
    ticker_to_response,
)
from agent.llm.prompts import base_asset_for_symbol
from agent.runner import get_runner
from agent.storage.json_utils import parse_json_field
from agent.storage.repository import (
    DecisionRepository,
    PendingSignalRepository,
    RiskEventRepository,
    SessionSummaryRepository,
    TradeRepository,
)
from agent.summary.aggregator import build_session_summary
from agent.summary.serialize import session_row_to_summary
from agent.trading.mode import get_trading_mode
from agent.validation.paper_gate import evaluate_validation


def _trade_item(row) -> TradeLogItem:
    return TradeLogItem(
        id=row.id,
        symbol=row.symbol,
        side=row.side,
        quantity=row.quantity,
        price=row.price,
        order_type=row.order_type,
        status=row.status,
        risk_decision=row.risk_decision,
        risk_reason=row.risk_reason,
        decision_reason=row.decision_reason,
        llm_confidence=row.llm_confidence,
        external_order_id=row.external_order_id,
        decision_id=row.decision_id,
        created_at=row.created_at,
    )


def _pending_item(row) -> PendingSignalItem:
    return PendingSignalItem(
        id=row.id,
        strategy_id=row.strategy_id,
        signal=parse_json_field(row.signal_json),
        status=row.status,
        expires_at=row.expires_at,
        created_at=row.created_at,
        session_id=row.session_id,
        decision_id=row.decision_id,
    )


def _risk_item(row) -> RiskEventItem:
    return RiskEventItem(
        id=row.id,
        event_type=row.event_type,
        detail=parse_json_field(row.detail),
        related_trade_id=row.related_trade_id,
        created_at=row.created_at,
    )


async def build_agent_status() -> AgentStatusResponse:
    snap = await get_runner().get_snapshot()
    workers = [
        WorkerStatusItem(
            symbol=w.symbol,
            last_tick=w.last_tick,
            last_status=w.last_status,
            last_error=w.last_error,
            tick_count=w.tick_count,
        )
        for w in snap.workers.values()
    ]
    return AgentStatusResponse(
        running=snap.running,
        last_tick=snap.last_tick,
        last_status=snap.last_status,
        last_error=snap.last_error,
        tick_count=snap.tick_count,
        daily_pnl=snap.daily_pnl,
        tick_interval=snap.tick_interval,
        llm_auto_execute=snap.llm_auto_execute,
        session_id=snap.session_id,
        session_started_at=snap.session_started_at,
        execution_mode=snap.execution_mode,
        trade_market=snap.trade_market,
        symbols=snap.symbols,
        workers=workers,
        degraded=snap.degraded,
    )


async def build_trading_mode_response() -> TradingModeResponse:
    mode = await get_trading_mode()
    validation = await evaluate_validation()
    return TradingModeResponse(
        mode=mode,
        can_enable_live=bool(validation.get("can_enable_live")),
        validation_status=validation.get("status", "none"),
    )


async def build_validation_response() -> ValidationStatusResponse:
    result = await evaluate_validation()
    mode = await get_trading_mode()
    cfg = get_settings()
    return ValidationStatusResponse(
        **result,
        trading_mode=mode,
        telegram_configured=cfg.telegram_configured,
        futures_enabled=cfg.futures_enabled,
    )


async def _resolve_session(agent: AgentStatusResponse) -> SessionSummaryResponse | None:
    settings = get_settings()
    if agent.running and agent.session_id and agent.session_started_at:
        data = await build_session_summary(
            session_id=agent.session_id,
            started_at=agent.session_started_at,
            ended_at=None,
            tick_count=agent.tick_count,
            last_status=agent.last_status,
            settings=settings,
        )
        return SessionSummaryResponse(**data)
    row = await SessionSummaryRepository().get_latest()
    if row is None:
        return None
    return SessionSummaryResponse(**session_row_to_summary(row))


def _build_positions(
    balance: BalanceResponse | None,
    tickers: list[TickerResponse],
    symbols: list[str],
) -> list[DashboardPositionItem]:
    if balance is None:
        return []
    ticker_map = {t.symbol: t for t in tickers if t.symbol}
    target_symbols = symbols or [t.symbol for t in tickers if t.symbol]
    rows: list[DashboardPositionItem] = []
    for symbol in target_symbols:
        if not symbol:
            continue
        base = base_asset_for_symbol(symbol)
        free = balance.free.get(base, 0.0)
        used = balance.used.get(base, 0.0)
        ticker = ticker_map.get(symbol)
        mark = ticker.last if ticker else None
        notional = free * mark if mark is not None else None
        rows.append(
            DashboardPositionItem(
                symbol=symbol,
                base=base,
                free=free,
                used=used,
                mark=mark,
                notional_usdt=notional,
            )
        )
    return rows


async def _worker_token_usage(
    started_at: str | None,
    ended_at: str | None,
    symbols: list[str],
) -> list[WorkerTokenUsageItem]:
    if not started_at:
        return []
    rows = await DecisionRepository().list_since(started_at, ended_at)
    buckets: dict[str, dict[str, int]] = {sym: {"llm_calls": 0, "total_tokens": 0} for sym in symbols}
    for row in rows:
        signal = parse_json_field(row.parsed_signal)
        sym = str(signal.get("symbol") or "unknown")
        if sym not in buckets:
            buckets[sym] = {"llm_calls": 0, "total_tokens": 0}
        buckets[sym]["llm_calls"] += 1
        buckets[sym]["total_tokens"] += row.total_tokens or 0
    return [
        WorkerTokenUsageItem(symbol=sym, llm_calls=data["llm_calls"], total_tokens=data["total_tokens"])
        for sym, data in sorted(buckets.items())
    ]


async def _fetch_balance(settings: Settings) -> tuple[BalanceResponse | None, str | None]:
    if not settings.binance_configured:
        return None, "BINANCE_API_KEY/SECRET not configured"
    try:
        raw = await fetch_exchange_balance(settings)
        return BalanceResponse(**balance_to_response(raw)), None
    except Exception as exc:  # noqa: BLE001
        return None, format_exchange_error(exc)


async def _fetch_tickers(
    settings: Settings,
    symbols: list[str],
) -> tuple[list[TickerResponse], str | None]:
    if not settings.binance_configured:
        return [], "BINANCE_API_KEY/SECRET not configured"
    try:
        raw_list = await fetch_exchange_tickers(settings, symbols)
        return [TickerResponse(**ticker_to_response(t)) for t in raw_list], None
    except Exception as exc:  # noqa: BLE001
        return [], format_exchange_error(exc)


async def build_dashboard_snapshot() -> DashboardSnapshotResponse:
    settings = get_settings()
    agent = await build_agent_status()
    health: HealthResponse = await build_health_response()
    trading_mode = await build_trading_mode_response()
    validation = await build_validation_response()
    usage = UsageSummaryResponse(**(await DecisionRepository().usage_summary()))
    session = await _resolve_session(agent)

    symbols = agent.symbols or settings.resolved_agent_symbols
    balance, balance_error = await _fetch_balance(settings)
    tickers, tickers_error = await _fetch_tickers(settings, symbols)
    positions = _build_positions(balance, tickers, symbols)

    trade_repo = TradeRepository()
    open_rows = await trade_repo.list_recent(limit=20, status="submitted")
    filled_rows = await trade_repo.list_recent(limit=10, status="filled")
    chart_rows = await trade_repo.list_recent(limit=100)
    pending_rows = await PendingSignalRepository().list_pending(limit=50)
    risk_rows = await RiskEventRepository().list_recent(limit=10)

    worker_usage = await _worker_token_usage(
        agent.session_started_at if agent.running else (session.started_at if session else None),
        None if agent.running else (session.ended_at if session else None),
        symbols,
    )

    return DashboardSnapshotResponse(
        agent=agent,
        trading_mode=trading_mode,
        validation=validation,
        health=health,
        usage=usage,
        session=session,
        balance=balance,
        balance_error=balance_error,
        positions=positions,
        tickers=tickers,
        tickers_error=tickers_error,
        open_trades=[_trade_item(r) for r in open_rows],
        recent_filled=[_trade_item(r) for r in filled_rows],
        chart_trades=[_trade_item(r) for r in chart_rows],
        pending_signals=[_pending_item(r) for r in pending_rows],
        risk_events=[_risk_item(r) for r in risk_rows],
        worker_token_usage=worker_usage,
    )
