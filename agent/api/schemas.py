from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    database: str
    binance_demo: str
    binance_detail: str | None = None
    llm_provider: str
    llm: str
    llm_detail: str | None = None


class AgentStatusResponse(BaseModel):
    running: bool
    last_tick: str | None = None
    last_status: str | None = None
    last_error: str | None = None
    tick_count: int = 0
    daily_pnl: float = 0.0
    tick_interval: int = 300
    llm_auto_execute: bool = True
    session_id: str | None = None
    session_started_at: str | None = None
    execution_mode: str = "auto"


class MessageResponse(BaseModel):
    message: str


class BalanceResponse(BaseModel):
    total: dict[str, float]
    free: dict[str, float]
    used: dict[str, float]


class TickerResponse(BaseModel):
    symbol: str | None = None
    last: float | None = None
    bid: float | None = None
    ask: float | None = None
    timestamp: int | None = None


class MarketDataInput(BaseModel):
    symbol: str | None = None
    last: float | None = None
    bid: float | None = None
    ask: float | None = None
    timestamp: int | None = None


class AnalysisRequest(BaseModel):
    """Optional market snapshot; omit to fetch live ticker when Binance is reachable."""

    market_data: MarketDataInput | None = None
    persist: bool = True


class TradeSignalResponse(BaseModel):
    action: str
    symbol: str
    amount: float | None = None
    confidence: float
    reason: str


class AnalysisResponse(BaseModel):
    signal: TradeSignalResponse
    model_used: str
    prompt_summary: str
    auto_execute: bool
    llm_auto_execute: bool
    decision_id: str | None = None
    raw_output: str | None = None
    usage: dict | None = None


class DecisionLogItem(BaseModel):
    id: str
    model_used: str
    prompt_summary: str | None = None
    parsed_signal: dict
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    created_at: str


class DecisionListResponse(BaseModel):
    items: list[DecisionLogItem]
    total: int


class AgentTickRequest(BaseModel):
    market_data: MarketDataInput | None = None
    thread_id: str = "default"


class AgentTickResponse(BaseModel):
    status: str
    message: str | None = None
    llm_signal: dict | None = None
    risk_decision: dict | None = None
    order_result: dict | None = None
    trade_log_id: str | None = None
    decision_id: str | None = None


class TradeLogItem(BaseModel):
    id: str
    symbol: str
    side: str
    quantity: float | None = None
    price: float | None = None
    order_type: str | None = None
    status: str
    risk_decision: str | None = None
    risk_reason: str | None = None
    decision_reason: str
    llm_confidence: float | None = None
    external_order_id: str | None = None
    decision_id: str | None = None
    created_at: str


class TradeListResponse(BaseModel):
    items: list[TradeLogItem]
    total: int


class RiskEventItem(BaseModel):
    id: str
    event_type: str
    detail: dict
    related_trade_id: str | None = None
    created_at: str


class RiskEventListResponse(BaseModel):
    items: list[RiskEventItem]
    total: int


class UsageBucket(BaseModel):
    calls: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class UsageSummaryResponse(BaseModel):
    today: UsageBucket
    total: UsageBucket


class SessionAgentInfo(BaseModel):
    tick_count: int
    tick_interval_sec: int | None = None
    trading_style: str
    last_status: str | None = None


class SessionUsageInfo(BaseModel):
    llm_calls: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float = 0.0


class SessionTradesInfo(BaseModel):
    buy_filled: int
    sell_filled: int
    failed: int
    signal_only: int
    loop_closed: bool


class SessionPnLInfo(BaseModel):
    cash_flow_usdt: float
    realized_usdt: float
    unrealized_usdt: float
    total_usdt: float
    daily_pnl_legacy: float


class SessionPositionsInfo(BaseModel):
    base_asset: str
    base_free: float
    usdt_free: float
    mark_price: float | None = None


class SessionSummaryResponse(BaseModel):
    session_id: str
    started_at: str
    ended_at: str | None = None
    agent: SessionAgentInfo
    usage: SessionUsageInfo
    trades: SessionTradesInfo
    pnl: SessionPnLInfo
    positions: SessionPositionsInfo
    highlights: list[str] = []


class SessionListResponse(BaseModel):
    items: list[SessionSummaryResponse]
    total: int


class CheckpointThreadItem(BaseModel):
    thread_id: str
    checkpoint_count: int
    latest_checkpoint_id: str | None = None


class CheckpointThreadListResponse(BaseModel):
    items: list[CheckpointThreadItem]
    total: int


class CheckpointStateItem(BaseModel):
    checkpoint_id: str | None = None
    thread_id: str | None = None
    created_at: str | None = None
    next_nodes: list[str] = []
    metadata: dict = {}
    state: dict = {}


class CheckpointHistoryResponse(BaseModel):
    thread_id: str
    items: list[CheckpointStateItem]
    total: int


class PendingSignalItem(BaseModel):
    id: str
    strategy_id: str | None = None
    signal: dict
    status: str
    expires_at: str
    created_at: str
    session_id: str | None = None
    decision_id: str | None = None


class PendingSignalListResponse(BaseModel):
    items: list[PendingSignalItem]
    total: int


class ConfirmPendingResponse(BaseModel):
    status: str
    message: str | None = None
    trade_log_id: str | None = None


class StrategyCreateRequest(BaseModel):
    name: str
    type: str
    market: str = "spot"
    execution_mode: str = "auto"
    params: dict | None = None


class StrategyUpdateRequest(BaseModel):
    name: str | None = None
    execution_mode: str | None = None
    params: dict | None = None


class StrategyItem(BaseModel):
    id: str
    name: str
    type: str
    market: str
    execution_mode: str
    params: dict
    state: dict
    status: str
    created_at: str
    updated_at: str
    started_at: str | None = None
    stopped_at: str | None = None


class StrategyListResponse(BaseModel):
    items: list[StrategyItem]
    total: int


class StrategyTickResponse(BaseModel):
    status: str
    signal: dict | None = None
    reason: str | None = None
    trade_log_id: str | None = None
    pending_signal_id: str | None = None


class ValidationStatusResponse(BaseModel):
    id: str | None = None
    status: str
    can_enable_live: bool
    metrics: dict
    requirements: dict | None = None
    reasons: list[str] = []
    started_at: str | None = None
    validated_at: str | None = None
    trading_mode: str = "demo"
    telegram_configured: bool = False
    futures_enabled: bool = False


class NotifyStatusResponse(BaseModel):
    telegram_configured: bool
    notify_on_session_close: bool
    notify_on_risk_reject: bool


class TradingModeRequest(BaseModel):
    mode: str


class TradingModeResponse(BaseModel):
    mode: str
    can_enable_live: bool
    validation_status: str


class FuturesStatusResponse(BaseModel):
    enabled: bool
    message: str
