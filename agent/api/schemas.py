from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    database: str
    binance_demo: str
    binance_detail: str | None = None
    llm_provider: str
    llm: str
    llm_detail: str | None = None
    # ok / warming_up / error。没有 disabled：数据面停摆就是故障本身，
    # 不存在"健康地不采数据"。默认值偏向大声失败。
    market: str = "error"
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


class MarketSessionResponse(BaseModel):
    """**本进程**采集器的自述。本进程不是采集器时整个对象为 null。

    单独成类型而不是平铺进 `MarketStatusResponse`，是为了让"这条事实只在采集器
    所在的那个进程里成立"这件事写在类型上。采集器独立成进程后（ADR-017），
    这些字段在 API 进程里恒为 false/null —— 平铺时会读成"采集器没在跑"。
    """

    connected: bool
    bars_written_session: int = 0
    reconnects_session: int = 0
    gaps_filled: int = 0
    last_gap_check_at: str | None = None
    backfill_running: bool = False
    backfill_last: dict | None = None
    backfill_error: str | None = None
    last_error: str | None = None
    last_error_at: str | None = None
    last_write_at: str | None = None
    started_at: str | None = None


class MarketStatusResponse(BaseModel):
    """行情数据面状态。

    **顶层字段全部跨进程可信**（读的是行情库 + 内核锁），`session` 是本进程自述。
    判断"数据在不在流"看 `data_flowing` / `lag_seconds`；判断"采集器进程在不在"
    看 `collector_owner`。
    """

    database: str
    # 锁文件的绝对路径。暴露它是因为它由进程 CWD 解析 —— API 与计划任务的 CWD
    # 不一致时会指着两个不同的锁文件，摆出来才看得出这种错配。
    lock_file: str | None = None
    symbols: list[str]
    interval: str
    interval_error: str | None = None
    last_bar_open_time: int | None = None
    last_closed_bar_open_time: int | None = None
    lag_seconds: int | None = None
    bars_count_24h: int = 0
    bars_expected_24h: int | None = None
    # 最近 24h 应有而未落库的 bar 数。注意方向：这是"缺了多少"，
    # 而 session.gaps_filled 是"这个进程补了多少"，两者相反。
    gap_count_24h: int | None = None
    # self / other / none / unknown（unknown = 本平台问不出答案，如 POSIX 的劝告锁）
    collector_owner: str
    data_flowing: bool
    session: MarketSessionResponse | None = None


class BackfillResponse(BaseModel):
    status: str
    detail: str | None = None
    requests: int | None = None
    rows_received: int | None = None
    rows_inserted: int | None = None
    failed_chunks: int | None = None
    ranges: int | None = None
    duration_s: float | None = None
