from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from agent.api.schemas import (
    AgentStatusResponse,
    AgentTickRequest,
    AgentTickResponse,
    AnalysisRequest,
    AnalysisResponse,
    BackfillResponse,
    BalanceResponse,
    DecisionListResponse,
    DecisionLogItem,
    DrawdownResetRequest,
    DrawdownResetResponse,
    HealthResponse,
    MarketStatusResponse,
    MessageResponse,
    OrderItem,
    OrderListResponse,
    PortfolioResponse,
    RiskEventItem,
    RiskEventListResponse,
    TickerResponse,
    TradeListResponse,
    TradeLogItem,
    TradeSignalResponse,
    UsageSummaryResponse,
)
from agent.config import get_settings
from agent.exchange.spot_demo import SpotDemoExchange, check_binance_demo
from agent.exchange._client import format_binance_error
from agent.graph.supervisor import run_agent_tick
from agent.llm.analyzer import check_llm
from agent.market.collector import get_collector, market_health, market_status_detail
from agent.runner import get_runner
from agent.storage.database import get_engine
from agent.storage.repository import (
    DecisionRepository,
    EquityRepository,
    OrderRepository,
    RiskEventRepository,
    TradeRepository,
)

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
    # 行情采集器探活（风险 #1：静默死亡）。warming_up 不算故障。
    try:
        market, market_detail = await market_health()
    except Exception as exc:  # noqa: BLE001 — 探活自身失败也不能带崩 /health
        market, market_detail = "error", f"{type(exc).__name__}: {exc}"

    overall = "ok"
    if (
        db_status != "ok"
        or binance["status"] == "error"
        or llm["status"] == "error"
        or market == "error"
    ):
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
        market=market,
        market_detail=market_detail,
    )


@router.get("/agent/status", response_model=AgentStatusResponse)
async def agent_status() -> AgentStatusResponse:
    snap = await get_runner().get_snapshot()
    return AgentStatusResponse(
        running=snap.running,
        last_tick=snap.last_tick,
        last_status=snap.last_status,
        last_error=snap.last_error,
        tick_count=snap.tick_count,
        daily_pnl=snap.daily_pnl,
        tick_interval=snap.tick_interval,
        llm_auto_execute=snap.llm_auto_execute,
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
            return await demo.fetch_market_context(sym)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=(
                f"{format_binance_error(exc)} "
                "Tip: pass market_data in POST body when Binance is unreachable."
            ),
        ) from exc


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


@router.get("/orders", response_model=OrderListResponse)
async def list_orders(limit: int = 50) -> OrderListResponse:
    """订单账本：交易所实际收到与回了什么。

    与 `/trades` 的分工：`/trades` 是**决策**流水（含 HOLD 与被风控拦下的），
    `/orders` 是**真实提交**过的订单回执。对账与归因看这里。
    """
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 200")
    repo = OrderRepository()
    rows = await repo.list_recent(limit=limit)
    items = [
        OrderItem(
            id=r.id,
            client_order_id=r.client_order_id,
            trade_id=r.trade_id,
            decision_id=r.decision_id,
            symbol=r.symbol,
            side=r.side,
            order_type=r.order_type,
            amount_kind=r.amount_kind,
            requested_amount=r.requested_amount,
            status=r.status,
            exchange_order_id=r.exchange_order_id,
            filled_qty=r.filled_qty,
            avg_price=r.avg_price,
            cost_quote=r.cost_quote,
            error=r.error,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]
    return OrderListResponse(items=items, total=len(items))


@router.post("/portfolio/snapshot", response_model=PortfolioResponse)
async def capture_portfolio_snapshot() -> PortfolioResponse:
    """拉一次真实的持仓与权益并落库（余额取交易所、价格取**实盘**）。

    注意这是 POST：它会打交易所与实盘行情，并且**写一条快照**。
    只想看数就 GET /portfolio。
    """
    from agent.portfolio.service import capture

    try:
        view = await capture()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=format_binance_error(exc)) from exc
    data = view.as_dict()
    data.pop("unmapped_symbols", None)  # 已折进 notes，避免 GET/POST 两条路不一致
    return PortfolioResponse(**data)


@router.get("/portfolio", response_model=PortfolioResponse)
async def get_portfolio() -> PortfolioResponse:
    """最近一次快照（只读，不打交易所）。没有快照时返回 404 而不是编一个 0。"""
    row = await EquityRepository().latest()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="尚无权益快照。POST /api/v1/portfolio/snapshot 采集一次。",
        )
    return PortfolioResponse(
        captured_at=row.captured_at,
        equity=row.equity,
        cash_quote=row.cash_quote,
        holdings_value=row.holdings_value,
        unpriced=json.loads(row.unpriced),
        positions=json.loads(row.positions),
        gaps=json.loads(row.gaps),
        notes=json.loads(row.notes),
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
            detail=json.loads(r.detail),
            related_trade_id=r.related_trade_id,
            created_at=r.created_at,
        )
        for r in rows
    ]
    return RiskEventListResponse(items=items, total=len(items))


@router.post("/risk/reset-drawdown", response_model=DrawdownResetResponse)
async def reset_drawdown_endpoint(body: DrawdownResetRequest) -> DrawdownResetResponse:
    """把回撤峰值挪到当下，重新开始算（ADR-029 / ADR-030）。

    这是**唯一能让回撤线松开的动作**，所以它是 POST、要理由、要留痕。
    重置不会自愈也不删数据：历史快照一条不动，只是峰值改从这一刻往后看。

    重置前后的账都报出来 —— 只看重置后的值看不出这次削掉了多少保护。
    """
    from dataclasses import asdict

    from agent.portfolio.pnl import reset_drawdown

    # 只读一次、落库与返回共用同一份 —— 否则接口说的和库里记的会是两笔账
    result = await reset_drawdown(reason=body.reason)
    return DrawdownResetResponse(reason=body.reason.strip(), **asdict(result))


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
            parsed_signal=json.loads(row.parsed_signal),
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
        high_24h=ticker.get("high"),
        low_24h=ticker.get("low"),
        change_24h_pct=ticker.get("percentage"),
        volume_24h_quote_usdt=ticker.get("quoteVolume"),
    )


@router.get("/market/status", response_model=MarketStatusResponse)
async def market_status() -> MarketStatusResponse:
    """行情采集状态：lag_seconds 是最该盯的指标（> 数个周期即为异常）。"""
    detail = await market_status_detail(get_collector())
    return MarketStatusResponse(**detail)


@router.post("/market/backfill", response_model=BackfillResponse)
async def market_backfill() -> BackfillResponse:
    """手动触发历史回补。幂等可重入；已在跑则直接返回。"""
    result = await get_collector().backfill_history_now()
    return BackfillResponse(**result)
