from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    database: str
    binance_demo: str
    binance_detail: str | None = None
    llm_provider: str
    llm: str
    llm_detail: str | None = None
    market: str = "disabled"
    market_detail: str | None = None


class AgentStatusResponse(BaseModel):
    running: bool
    last_tick: str | None = None
    last_status: str | None = None
    last_error: str | None = None
    tick_count: int = 0
    daily_pnl: float = 0.0
    tick_interval: int = 300
    llm_auto_execute: bool = True


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
    high_24h: float | None = None
    low_24h: float | None = None
    change_24h_pct: float | None = None
    volume_24h_quote_usdt: float | None = None


class MarketDataInput(BaseModel):
    symbol: str | None = None
    last: float | None = None
    bid: float | None = None
    ask: float | None = None
    timestamp: int | None = None
    high_24h: float | None = None
    low_24h: float | None = None
    change_24h_pct: float | None = None
    volume_24h_quote_usdt: float | None = None
    candles: list[dict] | None = None
    indicators: dict | None = None


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
    thread_id: str | None = None


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


class MarketStatusResponse(BaseModel):
    """行情采集状态：采集器快照 + 库内事实。"""

    collector_running: bool
    connected: bool
    symbols: list[str]
    interval: str
    last_bar_open_time: int | None = None
    last_closed_bar_open_time: int
    lag_seconds: int | None = None
    bars_written_session: int = 0
    bars_count_24h: int = 0
    gap_count_24h: int = 0
    reconnects_session: int = 0
    last_gap_check_at: str | None = None
    backfill_running: bool = False
    backfill_last: dict | None = None
    backfill_error: str | None = None
    last_error: str | None = None
    last_error_at: str | None = None
    last_write_at: str | None = None
    started_at: str | None = None
    database: str


class BackfillResponse(BaseModel):
    status: str
    detail: str | None = None
    requests: int | None = None
    rows_received: int | None = None
    rows_inserted: int | None = None
    failed_chunks: int | None = None
    ranges: int | None = None
    duration_s: float | None = None
