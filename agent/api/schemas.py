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
    daily_pnl: float = 0.0
    llm_auto_execute: bool = True
    message: str = "Agent runner not implemented yet (P4)"


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


class DecisionLogItem(BaseModel):
    id: str
    model_used: str
    prompt_summary: str | None = None
    parsed_signal: dict
    created_at: str


class DecisionListResponse(BaseModel):
    items: list[DecisionLogItem]
    total: int
