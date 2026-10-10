from sqlalchemy import Float, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TradeLog(Base):
    __tablename__ = "trade_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    side: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    order_type: Mapped[str | None] = mapped_column(String, nullable=True)
    llm_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision_reason: Mapped[str] = mapped_column(Text, nullable=False)
    risk_decision: Mapped[str | None] = mapped_column(String, nullable=True)
    risk_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    decision_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    # ---- 可归因性（ADR-019 / ADR-031）----
    # 决策时刻的**完整输入**。LLM 驱动的交易是行情上下文，强制平仓是风控输入
    # （持仓 + 高点 + 回撤）—— 后者没有 LLM，但同样得答得出"当时依据了什么"。
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    # R3：决策时刻手里有什么。**刻意留一份副本而不是只存 captured_at 去查快照表** ——
    # 快照是每 tick 一张的流水，可能被轮转清理；"这一笔依据的是哪个状态"必须钉在这一笔上。
    equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    positions: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 留位：目标与计划是阶段 C 的实体，现在没有。可空，届时回填，不阻塞现在。
    goal_id: Mapped[str | None] = mapped_column(String, nullable=True)
    plan_id: Mapped[str | None] = mapped_column(String, nullable=True)


Index("idx_trade_logs_created", TradeLog.created_at.desc())
Index("idx_trade_logs_side_status", TradeLog.side, TradeLog.status)


class RiskEvent(Base):
    __tablename__ = "risk_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    related_trade_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


Index("idx_risk_events_time", RiskEvent.created_at.desc())


class DecisionLog(Base):
    __tablename__ = "decision_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    model_used: Mapped[str] = mapped_column(String, nullable=False)
    # 人类看一眼的一行摘要。**不是**归因依据 —— 依据是下面那两列。
    prompt_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_output: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_signal: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    # ---- 可归因性（ADR-019 / ADR-031）----
    # **实际发出去**的完整 messages。只存 raw_output 的话，"模型是看着什么说的"
    # 就永远查不到 —— 而那是全自动模式下唯一的查错处。
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 用的是哪一份 prompt（normal / brevity）。重试那次是被催着简短作答的，
    # 回答质量与首次不同 —— 光看 raw_output 判断不出来，得单独记。
    prompt_variant: Mapped[str | None] = mapped_column(String, nullable=True)
    market_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    goal_id: Mapped[str | None] = mapped_column(String, nullable=True)
    plan_id: Mapped[str | None] = mapped_column(String, nullable=True)


class AgentConfigRow(Base):
    __tablename__ = "agent_config"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class OrderLog(Base):
    """订单账本：交易所实际收到了什么、回了什么。

    与 `trade_logs` 分开的理由：trade_logs 记的是**决策**，可能根本没有订单
    （HOLD、被风控拦下）。订单回执才是对账与事后归因（ADR-019 R1/R3）的唯一依据。

    `client_order_id` 是幂等键 —— 由决策 id 派生而非随机，交易所据此去重。
    唯一约束是硬保证：同一决策不可能在库里留下两笔单。
    """

    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    client_order_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    trade_id: Mapped[str | None] = mapped_column(String, nullable=True)
    decision_id: Mapped[str | None] = mapped_column(String, nullable=True)

    symbol: Mapped[str] = mapped_column(String, nullable=False)
    side: Mapped[str] = mapped_column(String, nullable=False)
    order_type: Mapped[str] = mapped_column(String, nullable=False)

    # amount 的口径必须与数值一起落库：BUY 是计价币名义（USDT），SELL 是基础币数量。
    # 现状里这个口径只活在 prompt 的一句话里，落库后无从分辨 —— 归因时
    # "amount=25 到底是 25 USDT 还是 25 BTC" 是个致命的歧义。
    amount_kind: Mapped[str] = mapped_column(String, nullable=False)
    requested_amount: Mapped[float] = mapped_column(Float, nullable=False)

    status: Mapped[str] = mapped_column(String, nullable=False)
    exchange_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    filled_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_quote: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee: Mapped[str | None] = mapped_column(Text, nullable=True)

    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


Index("idx_orders_created", OrderLog.created_at.desc())
Index("idx_orders_trade", OrderLog.trade_id)


class EquitySnapshotLog(Base):
    """某一时刻的账户权益（阶段 A-2）。

    为什么要落库而不是每次现算：日亏熔断需要**当天开始时的权益**做基准，
    而"开始时"是个已经过去的时刻 —— 不落库就只能拿现金流近似（旧实现正是如此，
    它把买入当成亏损）。有了这张表，日盈亏才是"现在的权益减今天的第一个快照"。

    持仓与账本的差异（账外持仓）一并存 JSON：事后归因时要知道，
    当时的那个权益数字背后有没有"不属于我们的钱"。
    """

    __tablename__ = "equity_snapshots"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    captured_at: Mapped[str] = mapped_column(String, nullable=False)

    cash_quote: Mapped[float] = mapped_column(Float, nullable=False)
    holdings_value: Mapped[float] = mapped_column(Float, nullable=False)
    equity: Mapped[float] = mapped_column(Float, nullable=False)

    # 取不到价的持仓 symbol。非空意味着 equity 只是下界，不是完整值。
    unpriced: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    positions: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    gaps: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="[]")


Index("idx_equity_captured", EquitySnapshotLog.captured_at.desc())
