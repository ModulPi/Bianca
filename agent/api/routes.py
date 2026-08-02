from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from agent.api.schemas import (
    AgentStatusResponse,
    AgentTickRequest,
    AgentTickResponse,
    AnalysisRequest,
    AnalysisResponse,
    BalanceResponse,
    ConfirmPendingResponse,
    DecisionListResponse,
    DecisionLogItem,
    HealthResponse,
    MessageResponse,
    RiskEventItem,
    RiskEventListResponse,
    TickerResponse,
    TradeListResponse,
    TradeLogItem,
    TradeSignalResponse,
    UsageSummaryResponse,
)
from agent.cache.redis_client import redis_health
from agent.config import get_settings
from agent.confirmation.service import confirm_pending_signal
from agent.exchange.spot_demo import SpotDemoExchange, check_binance_demo
from agent.exchange._client import format_binance_error
from agent.graph.supervisor import run_agent_tick
from agent.llm.analyzer import check_llm
from agent.runner import get_runner
from agent.storage.database import get_engine, schema_mode
from agent.storage.json_utils import parse_json_field
from agent.storage.repository import DecisionRepository, RiskEventRepository, TradeRepository

router = APIRouter(prefix="/api/v1")


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()

    db_status = "ok"
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.exec_driver_sql("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        db_status = f"error: {exc}"

    binance = await check_binance_demo()
    llm = await check_llm(settings)
    redis = await redis_health()

    overall = "ok"
    if db_status != "ok" or binance["status"] == "error" or llm["status"] == "error":
        overall = "degraded"
    if redis.get("status") == "error":
        overall = "degraded"

    llm_status = llm["status"]
    llm_detail = llm.get("detail")
    if llm_status == "not_configured":
        llm_detail = llm_detail or "Set LLM_API_KEY in .env to enable P2"

    return HealthResponse(
        status=overall,
        database=db_status,
        database_backend=settings.database_backend,
        schema_mode=schema_mode(),
        redis=redis.get("status", "not_configured"),
        redis_detail=redis.get("detail"),
        binance_demo=binance["status"],
        binance_detail=binance.get("detail"),
        llm_provider=settings.llm_provider,
        llm=llm_status,
        llm_detail=llm_detail,
    )


@router.get("/agent/status", response_model=AgentStatusResponse)
async def agent_status() -> AgentStatusResponse:
    snap = await get_runner().get_snapshot()
    settings = get_settings()
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
        execution_mode=settings.resolved_execution_mode,
    )


@router.post("/agent/start", response_model=MessageResponse)
async def agent_start() -> MessageResponse:
    settings = get_settings()
    _require_llm(settings)
    runner = get_runner()
    if runner.running:
        return MessageResponse(message="Agent runner already running")
    try:
        await runner.start()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return MessageResponse(
        message=f"Agent runner started (interval={settings.agent_tick_interval}s)"
    )


@router.post("/agent/stop", response_model=MessageResponse)
async def agent_stop() -> MessageResponse:
    runner = get_runner()
    if not runner.running:
        return MessageResponse(message="Agent runner is not running")
    await runner.stop()
    return MessageResponse(message="Agent runner stopped")


def _require_binance(settings) -> None:
    if not settings.binance_configured:
        raise HTTPException(status_code=503, detail="BINANCE_API_KEY/SECRET not configured")


def _require_llm(settings) -> None:
    if not settings.llm_configured:
        raise HTTPException(
            status_code=503,
            detail="LLM not configured: set LLM_API_KEY (DeepSeek) or LLM_BASE_URL+LLM_MODEL (Ollama)",
        )


async def _resolve_market_data(body: AnalysisRequest) -> dict:
    settings = get_settings()
    if body.market_data is not None:
        data = body.market_data.model_dump(exclude_none=True)
        data.setdefault("symbol", settings.trade_symbol)
        if data.get("last") is None:
            raise HTTPException(status_code=422, detail="market_data.last is required when providing snapshot")
        return data

    _require_binance(settings)
    sym = settings.trade_symbol
    try:
        async with SpotDemoExchange(settings) as demo:
            ticker = await demo.fetch_ticker(sym)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=(
                f"{format_binance_error(exc)} "
                "Tip: pass market_data in POST body when Binance is unreachable."
            ),
        ) from exc
    return {
        "symbol": ticker.get("symbol", sym),
        "last": ticker.get("last"),
        "bid": ticker.get("bid"),
        "ask": ticker.get("ask"),
        "timestamp": ticker.get("timestamp"),
    }


@router.post("/agent/tick", response_model=AgentTickResponse)
async def agent_tick(body: AgentTickRequest | None = None) -> AgentTickResponse:
    settings = get_settings()
    _require_llm(settings)
    req = body or AgentTickRequest()

    market_data = None
    if req.market_data is not None:
        market_data = req.market_data.model_dump(exclude_none=True)
        market_data.setdefault("symbol", settings.trade_symbol)
        if market_data.get("last") is None:
            raise HTTPException(status_code=422, detail="market_data.last is required")

    try:
        result = await run_agent_tick(
            market_data=market_data,
            thread_id=req.thread_id,
            settings=settings,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return AgentTickResponse(
        status=result.get("status", "unknown"),
        message=result.get("message"),
        llm_signal=result.get("llm_signal"),
        risk_decision=result.get("risk_decision"),
        order_result=result.get("order_result"),
        trade_log_id=result.get("trade_log_id"),
        decision_id=result.get("decision_id"),
    )


@router.get("/trades", response_model=TradeListResponse)
async def list_trades(
    limit: int = 50,
    symbol: str | None = None,
    side: str | None = None,
    status: str | None = None,
) -> TradeListResponse:
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 200")
    repo = TradeRepository()
    rows = await repo.list_recent(
        limit=limit,
        symbol=symbol,
        side=side,
        status=status,
    )
    items = [
        TradeLogItem(
            id=r.id,
            symbol=r.symbol,
            side=r.side,
            quantity=r.quantity,
            price=r.price,
            order_type=r.order_type,
            status=r.status,
            risk_decision=r.risk_decision,
            risk_reason=r.risk_reason,
            decision_reason=r.decision_reason,
            llm_confidence=r.llm_confidence,
            external_order_id=r.external_order_id,
            decision_id=r.decision_id,
            created_at=r.created_at,
        )
        for r in rows
    ]
    return TradeListResponse(items=items, total=len(items))


@router.get("/trades/{trade_id}", response_model=TradeLogItem)
async def get_trade(trade_id: str) -> TradeLogItem:
    repo = TradeRepository()
    row = await repo.get_by_id(trade_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Trade not found")
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


@router.get("/risk/events", response_model=RiskEventListResponse)
async def list_risk_events(limit: int = 50) -> RiskEventListResponse:
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 200")
    repo = RiskEventRepository()
    rows = await repo.list_recent(limit=limit)
    items = [
        RiskEventItem(
            id=r.id,
            event_type=r.event_type,
            detail=parse_json_field(r.detail),
            related_trade_id=r.related_trade_id,
            created_at=r.created_at,
        )
        for r in rows
    ]
    return RiskEventListResponse(items=items, total=len(items))


@router.post("/analysis/run", response_model=AnalysisResponse)
async def run_analysis(body: AnalysisRequest | None = None) -> AnalysisResponse:
    settings = get_settings()
    _require_llm(settings)
    req = body or AnalysisRequest()
    market_data = await _resolve_market_data(req)

    from agent.graph.analysis_agent import run_analysis_agent

    result = await run_analysis_agent(market_data, settings=settings, persist=req.persist)

    return AnalysisResponse(
        signal=TradeSignalResponse(**result.signal.to_dict()),
        model_used=result.model_used,
        prompt_summary=result.prompt_summary,
        auto_execute=result.auto_execute,
        llm_auto_execute=settings.llm_auto_execute,
        decision_id=result.decision_id,
        raw_output=result.raw_output or None,
        usage=result.usage,
    )


@router.get("/decisions", response_model=DecisionListResponse)
async def list_decisions(limit: int = 50) -> DecisionListResponse:
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 200")
    repo = DecisionRepository()
    rows = await repo.list_recent(limit=limit)
    items = [
        DecisionLogItem(
            id=row.id,
            model_used=row.model_used,
            prompt_summary=row.prompt_summary,
            parsed_signal=parse_json_field(row.parsed_signal),
            prompt_tokens=row.prompt_tokens,
            completion_tokens=row.completion_tokens,
            total_tokens=row.total_tokens,
            created_at=row.created_at,
        )
        for row in rows
    ]
    return DecisionListResponse(items=items, total=len(items))


@router.get("/usage", response_model=UsageSummaryResponse)
async def usage_summary() -> UsageSummaryResponse:
    """Token 消耗汇总：today（UTC）+ total。"""
    repo = DecisionRepository()
    summary = await repo.usage_summary()
    return UsageSummaryResponse(**summary)


@router.get("/exchange/balance", response_model=BalanceResponse)
async def exchange_balance() -> BalanceResponse:
    settings = get_settings()
    _require_binance(settings)
    try:
        async with SpotDemoExchange(settings) as demo:
            balance = await demo.fetch_balance()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=format_binance_error(exc)) from exc
    return BalanceResponse(
        total={k: float(v) for k, v in balance.get("total", {}).items() if v},
        free={k: float(v) for k, v in balance.get("free", {}).items() if v},
        used={k: float(v) for k, v in balance.get("used", {}).items() if v},
    )


@router.get("/exchange/ticker", response_model=TickerResponse)
async def exchange_ticker(symbol: str | None = None) -> TickerResponse:
    settings = get_settings()
    _require_binance(settings)
    sym = symbol or settings.trade_symbol
    try:
        async with SpotDemoExchange(settings) as demo:
            ticker = await demo.fetch_ticker(sym)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=format_binance_error(exc)) from exc
    return TickerResponse(
        symbol=ticker.get("symbol"),
        last=ticker.get("last"),
        bid=ticker.get("bid"),
        ask=ticker.get("ask"),
        timestamp=ticker.get("timestamp"),
    )


@router.post("/strategies/{strategy_id}/confirm", response_model=ConfirmPendingResponse)
async def strategy_confirm(strategy_id: str) -> ConfirmPendingResponse:
    """US-M02：半自动确认。PoC 阶段 strategy_id 即 pending_signal_id。"""
    try:
        result = await confirm_pending_signal(strategy_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    state = result.get("state") or {}
    return ConfirmPendingResponse(
        status=str(result.get("status", "unknown")),
        message=state.get("message"),
        trade_log_id=state.get("trade_log_id"),
    )
