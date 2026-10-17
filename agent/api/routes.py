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
    DecisionDetail,
    DecisionListResponse,
    DecisionLogItem,
    DialogueReply,
    DialogueTextRequest,
    DrawdownResetRequest,
    DrawdownResetResponse,
    EquityHistoryResponse,
    EquityPoint,
    GoalItem,
    GoalListResponse,
    HealthResponse,
    KlineItem,
    KlineListResponse,
    MarketStatusResponse,
    MessageItem,
    MessageListResponse,
    MessageResponse,
    OrderDetail,
    OrderItem,
    OrderListResponse,
    PlanItem,
    PlanListResponse,
    PortfolioResponse,
    RiskEventItem,
    RiskEventListResponse,
    TickerResponse,
    TradeDetail,
    TradeListResponse,
    TradeLogItem,
    TradeSignalResponse,
    UsageSummaryResponse,
)
from agent.config import get_settings
from agent.dialogue import Dialogue, GoalNotFound, OpenGoalExists, Reply
from agent.exchange.spot_demo import SpotDemoExchange, check_binance_demo
from agent.exchange._client import format_binance_error
from agent.graph.supervisor import run_agent_tick
from agent.llm.analyzer import check_llm
from agent.market.collector import get_collector, market_health, market_status_detail
from agent.market.repository import KlineRepository
from agent.runner import get_runner
from agent.storage.database import get_engine
from agent.storage.repository import (
    DecisionRepository,
    EquityRepository,
    GoalRepository,
    MessageRepository,
    OrderRepository,
    PlanRepository,
    RiskEventRepository,
    TradeRepository,
)

router = APIRouter(prefix="/api/v1")


def _check_page(limit: int, offset: int) -> None:
    """分页参数只有一种校验 —— 每条列表都要过，不能各写各的。"""
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 200")
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset must be >= 0")


def _json_or_none(text: str | None):
    """库里存的是 JSON 文本。空就是空，不编一个 {}。"""
    return json.loads(text) if text else None


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
    offset: int = 0,
    symbol: str | None = None,
    side: str | None = None,
    status: str | None = None,
    goal_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> TradeListResponse:
    _check_page(limit, offset)
    repo = TradeRepository()
    params = {
        "symbol": symbol,
        "side": side,
        "status": status,
        "goal_id": goal_id,
        "since": since,
        "until": until,
    }
    rows = await repo.list_recent(limit=limit, offset=offset, **params)
    items = [_trade_item(r) for r in rows]
    # total 必须与筛选条件同源：翻到第 2 页时它不能变成「本页条数」。
    total = await repo.count(**params)
    return TradeListResponse(items=items, total=total)


def _trade_item(r) -> TradeLogItem:
    return TradeLogItem(
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
        goal_id=r.goal_id,
        plan_id=r.plan_id,
        created_at=r.created_at,
    )


@router.get("/trades/{trade_id}", response_model=TradeDetail)
async def get_trade(trade_id: str) -> TradeDetail:
    repo = TradeRepository()
    row = await repo.get_by_id(trade_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Trade not found")
    return TradeDetail(
        **_trade_item(row).model_dump(),
        context=_json_or_none(row.context),
        equity=row.equity,
        positions=_json_or_none(row.positions),
    )


def _order_item(r) -> OrderItem:
    return OrderItem(
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
        fee=r.fee,
        error=r.error,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


@router.get("/orders", response_model=OrderListResponse)
async def list_orders(
    limit: int = 50,
    offset: int = 0,
    symbol: str | None = None,
    side: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> OrderListResponse:
    """订单账本：交易所实际收到与回了什么。

    与 `/trades` 的分工：`/trades` 是**决策**流水（含 HOLD 与被风控拦下的），
    `/orders` 是**真实提交**过的订单回执。对账与归因看这里。
    """
    _check_page(limit, offset)
    repo = OrderRepository()
    params = {"symbol": symbol, "side": side, "since": since, "until": until}
    rows = await repo.list_recent(limit=limit, offset=offset, **params)
    return OrderListResponse(
        items=[_order_item(r) for r in rows],
        total=await repo.count(**params),
    )


@router.get("/orders/{order_id}", response_model=OrderDetail)
async def get_order(order_id: str) -> OrderDetail:
    row = await OrderRepository().get_by_id(order_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return OrderDetail(
        **_order_item(row).model_dump(),
        raw_response=row.raw_response,
    )


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
async def list_risk_events(
    limit: int = 50,
    offset: int = 0,
    event_type: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> RiskEventListResponse:
    _check_page(limit, offset)
    repo = RiskEventRepository()
    params = {"event_type": event_type, "since": since, "until": until}
    rows = await repo.list_recent(limit=limit, offset=offset, **params)
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
    return RiskEventListResponse(items=items, total=await repo.count(**params))


@router.get("/equity/history", response_model=EquityHistoryResponse)
async def equity_history(
    limit: int = 500,
    since: str | None = None,
    until: str | None = None,
) -> EquityHistoryResponse:
    """权益快照序列（按时间正序），用于画曲线。

    `total` 是同条件下的总快照数；`items` 是**最近** `limit` 条翻正。
    """
    if limit < 1 or limit > 5000:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 5000")
    repo = EquityRepository()
    rows = await repo.list_ascending(limit=limit, since=since, until=until)
    return EquityHistoryResponse(
        items=[
            EquityPoint(
                id=r.id,
                captured_at=r.captured_at,
                equity=r.equity,
                cash_quote=r.cash_quote,
                holdings_value=r.holdings_value,
            )
            for r in rows
        ],
        total=await repo.count(since=since, until=until),
    )


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


def _decision_item(row) -> DecisionLogItem:
    return DecisionLogItem(
        id=row.id,
        model_used=row.model_used,
        prompt_summary=row.prompt_summary,
        parsed_signal=json.loads(row.parsed_signal),
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        total_tokens=row.total_tokens,
        goal_id=row.goal_id,
        plan_id=row.plan_id,
        plan_step=row.plan_step,
        created_at=row.created_at,
    )


@router.get("/decisions", response_model=DecisionListResponse)
async def list_decisions(
    limit: int = 50,
    offset: int = 0,
    goal_id: str | None = None,
    plan_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> DecisionListResponse:
    _check_page(limit, offset)
    repo = DecisionRepository()
    params = {"goal_id": goal_id, "plan_id": plan_id, "since": since, "until": until}
    rows = await repo.list_recent(limit=limit, offset=offset, **params)
    return DecisionListResponse(
        items=[_decision_item(row) for row in rows],
        total=await repo.count(**params),
    )


@router.get("/decisions/{decision_id}", response_model=DecisionDetail)
async def get_decision(decision_id: str) -> DecisionDetail:
    """单条决策的完整依据 —— 包括实际发出去的 prompt 与原始回答。

    只存一行摘要时，「模型当时看着什么说的」就永远查不到；这条路径是那个答案。
    """
    row = await DecisionRepository().get_by_id(decision_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Decision not found")
    return DecisionDetail(
        **_decision_item(row).model_dump(),
        raw_output=row.raw_output,
        prompt=_json_or_none(row.prompt),
        prompt_variant=row.prompt_variant,
        market_data=_json_or_none(row.market_data),
    )


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


@router.get("/market/klines", response_model=KlineListResponse)
async def market_klines(
    symbol: str,
    interval: str = "1m",
    limit: int = 120,
    end: int | None = None,
) -> KlineListResponse:
    """从行情库读最近 `limit` 根 K 线（按时间正序）。

    `end` 是 epoch ms：只看某时刻（含）之前的 bar。没有它就看最新的。
    `total` 是该 symbol+interval 在库里的总行数，不是本页条数。
    """
    if limit < 1 or limit > 1000:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 1000")
    repo = KlineRepository()
    rows = await repo.list_bars(symbol, interval, limit=limit, end_ms=end)
    return KlineListResponse(
        symbol=symbol.upper(),
        interval=interval,
        items=[
            KlineItem(
                time=int(r.time),
                open=r.open,
                high=r.high,
                low=r.low,
                close=r.close,
                volume=r.volume,
                quote_volume=r.quote_volume,
                trades=r.trades,
                taker_buy_base=r.taker_buy_base,
                taker_buy_quote=r.taker_buy_quote,
            )
            for r in rows
        ],
        total=await repo.count_bars(symbol, interval),
        source="market.db",
    )


@router.post("/market/backfill", response_model=BackfillResponse)
async def market_backfill() -> BackfillResponse:
    """手动触发历史回补。幂等可重入；已在跑则直接返回。"""
    result = await get_collector().backfill_history_now()
    return BackfillResponse(**result)


# ---- 对话层（阶段 C-5，ADR-032）----------------------------------------------
#
# **每条请求新建一个 `Dialogue`**：会话状态全在库里，进程内不留状态。
# 这样重启、多 worker 都不会让「当前目标是哪个」分叉。
#
# 这几个端点**不做额外的智能**，动作与 `Dialogue` 的方法一一对应。
# 业务上的「拒绝」（未确认就规划、已生效不让改）是 `Reply.text`，
# 不是 HTTP 错误 —— 它们是这一层正常的工作结果。


def _dialogue_reply(reply: Reply) -> DialogueReply:
    return DialogueReply(
        goal_id=reply.goal_id,
        status=reply.status.value,
        text=reply.text,
        changed=reply.changed,
        unclear=reply.unclear,
        activated=reply.activated,
        plan_id=reply.plan_id,
    )


def _dialogue_error(exc: ValueError) -> HTTPException:
    """业务异常 → HTTP。只分三种，**别把"拒绝"也塞进来**（见上）。"""
    if isinstance(exc, GoalNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, OpenGoalExists):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.post("/dialogue/start", response_model=DialogueReply)
async def dialogue_start(body: DialogueTextRequest) -> DialogueReply:
    """开一个目标。用户开口那一刻建行，状态恒为 `proposed`。"""
    try:
        reply = await Dialogue(settings=get_settings()).start(body.text)
    except ValueError as exc:
        raise _dialogue_error(exc) from exc
    return _dialogue_reply(reply)


@router.get("/dialogue/current", response_model=DialogueReply)
async def dialogue_current() -> DialogueReply:
    """当前未结束的目标，等价于一次 `resume()`。没有就 404 —— 不编一个空目标。"""
    row = await GoalRepository().open_goal()
    if row is None:
        raise HTTPException(status_code=404, detail="当前没有未结束的目标")
    try:
        reply = await Dialogue(settings=get_settings()).resume(row.id)
    except ValueError as exc:
        raise _dialogue_error(exc) from exc
    return _dialogue_reply(reply)


@router.get("/dialogue/{goal_id}", response_model=DialogueReply)
async def dialogue_show(goal_id: str) -> DialogueReply:
    """把当前目标重念一遍，**不读任何新话**。"""
    try:
        reply = await Dialogue(settings=get_settings()).resume(goal_id)
    except ValueError as exc:
        raise _dialogue_error(exc) from exc
    return _dialogue_reply(reply)


@router.post("/dialogue/{goal_id}/say", response_model=DialogueReply)
async def dialogue_say(goal_id: str, body: DialogueTextRequest) -> DialogueReply:
    """用户又说了一句。**这条路永远不会激活目标**（ADR-037）。"""
    try:
        reply = await Dialogue(settings=get_settings()).say(goal_id, body.text)
    except ValueError as exc:
        raise _dialogue_error(exc) from exc
    return _dialogue_reply(reply)


@router.post("/dialogue/{goal_id}/confirm", response_model=DialogueReply)
async def dialogue_confirm(goal_id: str) -> DialogueReply:
    """确认 → `active`。**唯一能让目标生效的入口**（ADR-037）。

    基准权益取**最近一次交易所快照**（ADR-033），不从请求体来 ——
    ADR-033 要的是交易所说的权益，不是调用方说的数。没有快照就 409，
    并指路去采一次：`POST /api/v1/portfolio/snapshot`。
    确认自身不打交易所，所以交易所不通时它仍然能用（那正是分开的理由）。
    """
    # 先判目标存在，再判快照：**id 给错了就是 id 错了**，
    # 不该因为"碰巧还没有快照"被报成另一件事。
    if await GoalRepository().get(goal_id) is None:
        raise HTTPException(status_code=404, detail=f"没有这个目标：{goal_id}")

    snapshot = await EquityRepository().latest()
    if snapshot is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "还没有权益快照，确认要以交易所的实际权益为基准（ADR-033）。"
                "先 POST /api/v1/portfolio/snapshot 采集一次。"
            ),
        )
    try:
        reply = await Dialogue(settings=get_settings()).confirm(
            goal_id,
            equity=snapshot.equity,
            equity_captured_at=snapshot.captured_at,
        )
    except ValueError as exc:
        raise _dialogue_error(exc) from exc
    return _dialogue_reply(reply)


@router.post("/dialogue/{goal_id}/plan", response_model=DialogueReply)
async def dialogue_plan(goal_id: str) -> DialogueReply:
    """让经理做一份计划并落库（C-4）。首次失败可以重试；已有计划就拒绝。"""
    try:
        reply = await Dialogue(settings=get_settings()).plan(goal_id)
    except ValueError as exc:
        raise _dialogue_error(exc) from exc
    return _dialogue_reply(reply)


# ---- 目标 / 消息 / 计划（阶段 F-1）--------------------------------------------
#
# 这是**只读面**：目标由对话层写，这里只把库里的事实读出来。
# 「空 = 用户没说」在这几层必须一路保持：JSON 空列返回 null，不返回 {}。


def _goal_item(row) -> GoalItem:
    return GoalItem(
        id=row.id,
        status=row.status,
        raw_text=row.raw_text,
        parsed=_json_or_none(row.parsed),
        objective=row.objective,
        symbols=_json_or_none(row.symbols),
        target_return_pct=row.target_return_pct,
        target_equity=row.target_equity,
        max_drawdown_pct=row.max_drawdown_pct,
        horizon_hours=row.horizon_hours,
        autonomy=row.autonomy,
        stated_capital=row.stated_capital,
        baseline_equity=row.baseline_equity,
        risk_snapshot=_json_or_none(row.risk_snapshot),
        plan_id=row.plan_id,
        created_at=row.created_at,
        confirmed_at=row.confirmed_at,
        deadline_at=row.deadline_at,
        ended_at=row.ended_at,
        end_reason=row.end_reason,
    )


def _plan_item(row, *, transcript: list | None = None) -> PlanItem:
    return PlanItem(
        id=row.id,
        goal_id=row.goal_id,
        plan=_json_or_none(row.plan),
        plan_shape=row.plan_shape,
        final_text=row.final_text,
        invocations=json.loads(row.invocations),
        shapes=json.loads(row.shapes),
        usage=_json_or_none(row.usage),
        errors=json.loads(row.errors),
        rounds=row.rounds,
        created_at=row.created_at,
        transcript=transcript,
    )


@router.get("/goals", response_model=GoalListResponse)
async def list_goals(
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
) -> GoalListResponse:
    _check_page(limit, offset)
    repo = GoalRepository()
    rows = await repo.list_recent(limit=limit, offset=offset, status=status)
    return GoalListResponse(
        items=[_goal_item(r) for r in rows],
        total=await repo.count(status=status),
    )


@router.get("/goals/{goal_id}", response_model=GoalItem)
async def get_goal(goal_id: str) -> GoalItem:
    row = await GoalRepository().get(goal_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"没有这个目标：{goal_id}")
    return _goal_item(row)


@router.get("/goals/{goal_id}/messages", response_model=MessageListResponse)
async def list_goal_messages(
    goal_id: str,
    limit: int = 200,
    offset: int = 0,
) -> MessageListResponse:
    """该目标的全部消息，按时间正序。这是「会话重启后上下文不丢」的那份记录。"""
    _check_page(limit, offset)
    if await GoalRepository().get(goal_id) is None:
        raise HTTPException(status_code=404, detail=f"没有这个目标：{goal_id}")
    repo = MessageRepository()
    rows = await repo.list_for_goal(goal_id, limit=limit, offset=offset)
    return MessageListResponse(
        items=[
            MessageItem(
                id=r.id,
                goal_id=r.goal_id,
                role=r.role,
                content=r.content,
                created_at=r.created_at,
            )
            for r in rows
        ],
        total=await repo.count_for_goal(goal_id),
    )


@router.get("/goals/{goal_id}/plans", response_model=PlanListResponse)
async def list_goal_plans(goal_id: str) -> PlanListResponse:
    """该目标的所有规划尝试（成功失败都返回），按时间倒序。"""
    if await GoalRepository().get(goal_id) is None:
        raise HTTPException(status_code=404, detail=f"没有这个目标：{goal_id}")
    rows = await PlanRepository().for_goal(goal_id)
    return PlanListResponse(items=[_plan_item(r) for r in rows], total=len(rows))


@router.get("/plans/{plan_id}", response_model=PlanItem)
async def get_plan(plan_id: str, include_transcript: bool = False) -> PlanItem:
    """单次规划的详情。

    transcript 是实际发出去的全部 messages，可能很大 —— 默认不返回，
    要的人显式带上 `include_transcript=1`。
    """
    row = await PlanRepository().get(plan_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"没有这次规划：{plan_id}")
    transcript = json.loads(row.transcript) if include_transcript else None
    return _plan_item(row, transcript=transcript)
