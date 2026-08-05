from sqlalchemy import Float, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from agent.storage.json_column import JsonText


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
    strategy_id: Mapped[str | None] = mapped_column(String, nullable=True)
    strategy_name: Mapped[str | None] = mapped_column(String, nullable=True)
    execution_mode: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


Index("idx_trade_logs_created", TradeLog.created_at.desc())
Index("idx_trade_logs_side_status", TradeLog.side, TradeLog.status)


class RiskEvent(Base):
    __tablename__ = "risk_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[str] = mapped_column(JsonText, nullable=False, default="{}")
    related_trade_id: Mapped[str | None] = mapped_column(String, nullable=True)
    related_strategy_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


Index("idx_risk_events_time", RiskEvent.created_at.desc())


class DecisionLog(Base):
    __tablename__ = "decision_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    model_used: Mapped[str] = mapped_column(String, nullable=False)
    prompt_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_output: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_signal: Mapped[str] = mapped_column(JsonText, nullable=False, default="{}")
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class AgentConfigRow(Base):
    __tablename__ = "agent_config"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class SessionSummaryRow(Base):
    __tablename__ = "session_summaries"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    started_at: Mapped[str] = mapped_column(String, nullable=False)
    ended_at: Mapped[str | None] = mapped_column(String, nullable=True)
    tick_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trading_style: Mapped[str] = mapped_column(String, nullable=False, default="conservative")
    usage_json: Mapped[str] = mapped_column(JsonText, nullable=False, default="{}")
    trades_json: Mapped[str] = mapped_column(JsonText, nullable=False, default="{}")
    pnl_json: Mapped[str] = mapped_column(JsonText, nullable=False, default="{}")
    positions_json: Mapped[str] = mapped_column(JsonText, nullable=False, default="{}")
    loop_closed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


Index("idx_session_summaries_started", SessionSummaryRow.started_at.desc())


class PendingSignalRow(Base):
    __tablename__ = "pending_signals"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    strategy_id: Mapped[str | None] = mapped_column(String, nullable=True)
    signal_json: Mapped[str] = mapped_column(JsonText, nullable=False)
    market_data_json: Mapped[str] = mapped_column(JsonText, nullable=False)
    decision_id: Mapped[str | None] = mapped_column(String, nullable=True)
    session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    expires_at: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


Index("idx_pending_signals_status", PendingSignalRow.status, PendingSignalRow.expires_at)


class StrategyRow(Base):
    __tablename__ = "strategies"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    market: Mapped[str] = mapped_column(String, nullable=False, default="spot")
    execution_mode: Mapped[str] = mapped_column(String, nullable=False, default="auto")
    params_json: Mapped[str] = mapped_column(JsonText, nullable=False, default="{}")
    state_json: Mapped[str] = mapped_column(JsonText, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String, nullable=False, default="created")
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    stopped_at: Mapped[str | None] = mapped_column(String, nullable=True)


Index("idx_strategies_status", StrategyRow.status)


class PaperValidationRow(Base):
    __tablename__ = "paper_validations"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    strategy_id: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[str] = mapped_column(String, nullable=False)
    validated_at: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="running")
    metrics_json: Mapped[str] = mapped_column(JsonText, nullable=False, default="{}")
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class PositionRow(Base):
    __tablename__ = "positions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String, nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    market: Mapped[str] = mapped_column(String, nullable=False, default="spot")
    quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    current_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    unrealized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True, default=0)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True, default=0)
    leverage: Mapped[int | None] = mapped_column(Integer, nullable=True, default=1)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


Index("idx_positions_strategy_symbol", PositionRow.strategy_id, PositionRow.symbol, unique=True)
