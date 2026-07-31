from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from agent.api.schemas import (
    AgentStatusResponse,
    AnalysisRequest,
    AnalysisResponse,
    BalanceResponse,
    DecisionListResponse,
    DecisionLogItem,
    HealthResponse,
    MessageResponse,
    TickerResponse,
    TradeSignalResponse,
)
from agent.config import get_settings
from agent.exchange.spot_demo import SpotDemoExchange, check_binance_demo
from agent.exchange._client import format_binance_error
from agent.graph.analysis_agent import run_analysis_agent
from agent.llm.analyzer import check_llm
from agent.storage.database import get_engine
from agent.storage.repository import DecisionRepository

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

    overall = "ok"
    if db_status != "ok" or binance["status"] == "error" or llm["status"] == "error":
        overall = "degraded"

    llm_status = llm["status"]
    llm_detail = llm.get("detail")
    if llm_status == "not_configured":
        llm_detail = llm_detail or "Set LLM_API_KEY in .env to enable P2"

    return HealthResponse(
        status=overall,
        database=db_status,
        binance_demo=binance["status"],
        binance_detail=binance.get("detail"),
        llm_provider=settings.llm_provider,
        llm=llm_status,
        llm_detail=llm_detail,
    )


@router.get("/agent/status", response_model=AgentStatusResponse)
async def agent_status() -> AgentStatusResponse:
    settings = get_settings()
    return AgentStatusResponse(
        running=False,
        llm_auto_execute=settings.llm_auto_execute,
    )


@router.post("/agent/start", response_model=MessageResponse)
async def agent_start() -> MessageResponse:
    return MessageResponse(message="Agent runner will be implemented in P4")


@router.post("/agent/stop", response_model=MessageResponse)
async def agent_stop() -> MessageResponse:
    return MessageResponse(message="Agent runner will be implemented in P4")


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


@router.post("/analysis/run", response_model=AnalysisResponse)
async def run_analysis(body: AnalysisRequest | None = None) -> AnalysisResponse:
    settings = get_settings()
    _require_llm(settings)
    req = body or AnalysisRequest()
    market_data = await _resolve_market_data(req)

    result = await run_analysis_agent(market_data, settings=settings, persist=req.persist)

    return AnalysisResponse(
        signal=TradeSignalResponse(**result.signal.to_dict()),
        model_used=result.model_used,
        prompt_summary=result.prompt_summary,
        auto_execute=result.auto_execute,
        llm_auto_execute=settings.llm_auto_execute,
        decision_id=result.decision_id,
        raw_output=result.raw_output or None,
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
            parsed_signal=json.loads(row.parsed_signal),
            created_at=row.created_at,
        )
        for row in rows
    ]
    return DecisionListResponse(items=items, total=len(items))


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
