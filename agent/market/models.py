"""行情库（market.db）的数据模型。

与业务库（bianca.db）分离：分钟级 append-only 写入不应与 Agent 的事务型表
争抢同一个库文件的锁（ADR-008）。

字段直接对应 Binance 原始 WS 事件（实测确认，见设计文档 §6），不用 ccxt 的
6 列 OHLCV —— 后者会丢掉 x / n / q / V / Q。
"""

from __future__ import annotations

from sqlalchemy import Float, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class MarketBase(DeclarativeBase):
    pass


class Kline(MarketBase):
    """分钟级 K 线。主键 (time, symbol, interval) 使回补天然幂等。"""

    __tablename__ = "klines"

    time: Mapped[int] = mapped_column(Integer, primary_key=True)  # bar 开始时间, epoch ms
    symbol: Mapped[str] = mapped_column(String, primary_key=True)
    interval: Mapped[str] = mapped_column(String, primary_key=True)

    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)

    volume: Mapped[float] = mapped_column(Float, nullable=False)  # 基础币成交量 (k.v)
    quote_volume: Mapped[float | None] = mapped_column(Float, nullable=True)  # 计价币成交额 (k.q)
    trades: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 成交笔数 (k.n)
    taker_buy_base: Mapped[float | None] = mapped_column(Float, nullable=True)  # 主动买基础币 (k.V)
    taker_buy_quote: Mapped[float | None] = mapped_column(Float, nullable=True)  # 主动买计价币 (k.Q)


Index("idx_klines_symbol_interval_time", Kline.symbol, Kline.interval, Kline.time.desc())


class IndicatorSnapshot(MarketBase):
    """指标快照 —— 可复现性锚点（ADR-011）。

    每 tick 一条，与 Agent 决策 1:1。context_digest 存实际交给 LLM 的摘要原文，
    因为指标序列本身可从 klines 重算，而 ticker 的 bid/ask 等不可重现。
    """

    __tablename__ = "indicator_snapshots"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    as_of: Mapped[int] = mapped_column(Integer, nullable=False)  # 对应 bar 时间, epoch ms
    window: Mapped[str] = mapped_column(String, nullable=False)
    bar_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metrics: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    context_digest: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[str] = mapped_column(String, nullable=False)


Index("idx_indicator_snapshots_time", IndicatorSnapshot.created_at.desc())
Index("idx_indicator_snapshots_symbol", IndicatorSnapshot.symbol, IndicatorSnapshot.as_of.desc())
