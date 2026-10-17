from pydantic import BaseModel, Field, field_validator


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
    # 权益口径（含持仓浮亏）。None = 算不出来，不等于 0。
    daily_pnl: float | None = None
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
    goal_id: str | None = None
    plan_id: str | None = None
    # 经理自报对应计划的第几步。空本身是信息（HOLD / 偏离计划）。
    plan_step: int | None = None
    created_at: str


class DecisionListResponse(BaseModel):
    items: list[DecisionLogItem]
    total: int


class DecisionDetail(DecisionLogItem):
    """单条决策的完整依据。列表不返回这些大字段（prompt 可能有几千 token）。"""

    raw_output: str
    # 实际发出去的 messages（list）或旧数据里的整段字符串。两种形状都如实返回。
    prompt: list[dict] | dict | str | None = None
    prompt_variant: str | None = None
    market_data: dict | str | None = None


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
    # 归因链：这笔交易属于哪个目标 / 哪份计划。可以是空（旧数据、人工全平）。
    goal_id: str | None = None
    plan_id: str | None = None
    created_at: str


class TradeListResponse(BaseModel):
    items: list[TradeLogItem]
    total: int


class OrderItem(BaseModel):
    """订单账本条目。`status` 与 `filled` 是交易所的回执，不是我们的估计。"""

    id: str
    client_order_id: str
    trade_id: str | None = None
    decision_id: str | None = None
    symbol: str
    side: str
    order_type: str
    # amount_kind 必须一起给出：quote=计价币名义(USDT)，base=基础币数量。
    # 只看 requested_amount 无法判断量纲。
    amount_kind: str
    requested_amount: float
    status: str
    exchange_order_id: str | None = None
    filled_qty: float | None = None
    avg_price: float | None = None
    cost_quote: float | None = None
    # 交易所回执里的手续费原文。BUY 收基础币、SELL 收计价币，口径由交易所定。
    fee: str | None = None
    error: str | None = None
    created_at: str
    updated_at: str


class OrderListResponse(BaseModel):
    items: list[OrderItem]
    total: int


class OrderDetail(OrderItem):
    """订单详情：多出交易所原始回执。列表不返回（可能很大）。"""

    raw_response: str | None = None


class RiskEventItem(BaseModel):
    id: str
    event_type: str
    detail: dict
    related_trade_id: str | None = None
    created_at: str


class RiskEventListResponse(BaseModel):
    items: list[RiskEventItem]
    total: int


class DrawdownResetRequest(BaseModel):
    """重置回撤基准。**reason 必填** —— 这是唯一能让回撤线松开的操作（ADR-030）。"""

    reason: str = Field(min_length=1, description="为什么要重置；会写进 risk_events")

    @field_validator("reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        """光有长度不够：`"   "` 也满足 `min_length=1`，但它不是理由。

        不放行的话它会在 `reset_drawdown()` 里抛 `ValueError` 变成 500 ——
        一个参数问题长得像服务端故障，排障时会往错的方向查。
        """
        if not value.strip():
            raise ValueError("理由不能是空白")
        return value.strip()


class DrawdownResetResponse(BaseModel):
    """重置**前后**都报出来。

    只报重置后的值看不出这次操作削掉了多少保护 —— 而"削掉了多少"正是事后
    要复盘的东西。字段与 `risk_events` 里那条 `drawdown_reset` 一一对应。
    """

    reset_at: str
    reason: str
    peak_before: float | None = None
    current_equity: float | None = None
    drawdown_before: float | None = None
    previous_reset_at: str | None = None


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


class PositionItem(BaseModel):
    asset: str
    quantity: float
    price: float | None = None
    # None 表示取不到价 —— 不是"值 0"。此时该持仓不计入权益。
    value_quote: float | None = None


class TradeDetail(TradeLogItem):
    """单笔交易的完整依据（ADR-019 R1/R2/R3）。

    `context` 是决策时刻的输入：LLM 路径是行情上下文，强制平仓路径是
    持仓 + 高点 + 回撤。它可能很大，所以只在详情返回。
    """

    context: dict | str | None = None
    equity: float | None = None
    positions: list[PositionItem] | None = None


class HoldingGapItem(BaseModel):
    """交易所持仓与本地账本归因之间的差。差分不为零**不代表出错**：
    账户在 agent 上线前就有余额，那部分本来就解释不了。"""

    asset: str
    exchange_qty: float
    attributed_qty: float
    unexplained_qty: float


class PortfolioResponse(BaseModel):
    captured_at: str | None = None
    equity: float
    cash_quote: float
    holdings_value: float
    # 非空意味着 equity 只是下界 —— 有持仓取不到价，没被算进去。
    unpriced: list[str] = []
    positions: list[PositionItem] = []
    gaps: list[HoldingGapItem] = []
    # 无法参与归因的 symbol 与其它说明都折在这里：快照表只有这一列自由文本，
    # 单独开一个字段会在 GET 路径上恒为空，那是个会骗人的字段。
    notes: list[str] = []


class DialogueTextRequest(BaseModel):
    """用户说的一句话。空串不放行 —— 「说了一句空话」不是一种意图。"""

    text: str

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text 不能是空的")
        return value


class DialogueReply(BaseModel):
    """对话层一轮的结果。`text` 是**回给用户看的话**，其余是给程序看的。

    注意 `plan_id` 的语义：它是**这一次规划尝试**的 `plans` 行（成功失败都有），
    不等于「目标已经有计划」。后者看目标行的 `plan_id`。
    """

    goal_id: str
    status: str
    text: str
    changed: list[str] = []
    unclear: list[str] = []
    activated: bool = False
    plan_id: str | None = None


# ---------------------------------------------------------------- 目标 / 消息 / 计划（阶段 F-1）


class GoalItem(BaseModel):
    """一个目标的完整生命周期。**空 = 用户没说，不是默认值。**"""

    id: str
    status: str
    raw_text: str
    parsed: dict | None = None
    objective: str | None = None
    symbols: list[str] | None = None
    target_return_pct: float | None = None
    target_equity: float | None = None
    max_drawdown_pct: float | None = None
    horizon_hours: float | None = None
    autonomy: str | None = None
    stated_capital: float | None = None
    baseline_equity: float | None = None
    risk_snapshot: dict | None = None
    plan_id: str | None = None
    created_at: str
    confirmed_at: str | None = None
    deadline_at: str | None = None
    ended_at: str | None = None
    end_reason: str | None = None


class GoalListResponse(BaseModel):
    items: list[GoalItem]
    total: int


class MessageItem(BaseModel):
    id: str
    goal_id: str
    role: str
    content: str
    created_at: str


class MessageListResponse(BaseModel):
    items: list[MessageItem]
    total: int


class PlanItem(BaseModel):
    """一次规划尝试。**失败也返回** —— `plan = null` 就是没解出来。"""

    id: str
    goal_id: str
    plan: dict | None = None
    plan_shape: str
    final_text: str
    invocations: list = []
    shapes: list[str] = []
    usage: dict | None = None
    errors: list[str] = []
    rounds: int = 0
    created_at: str
    # 只有详情且显式 include_transcript 才返回。
    transcript: list | None = None


class PlanListResponse(BaseModel):
    items: list[PlanItem]
    total: int


# ---------------------------------------------------------------- 权益历史 / K 线（阶段 F-1）


class EquityPoint(BaseModel):
    id: str
    captured_at: str
    equity: float
    cash_quote: float
    holdings_value: float


class EquityHistoryResponse(BaseModel):
    items: list[EquityPoint]
    total: int


class KlineItem(BaseModel):
    """`klines` 的一行。`time` 是 bar 开始时间（epoch ms），不是 ISO 字符串。"""

    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float | None = None
    trades: int | None = None
    taker_buy_base: float | None = None
    taker_buy_quote: float | None = None


class KlineListResponse(BaseModel):
    symbol: str
    interval: str
    items: list[KlineItem]
    # 该 symbol+interval 在库里的总行数（不是本页条数）。
    total: int
    source: str
