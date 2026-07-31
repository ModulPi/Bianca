-- ============================================================
-- FnAgent — 数据库 DDL 脚本
-- DB: PostgreSQL 16 + TimescaleDB 2.x
-- 版本: v1.0 | 日期: 2026-07-28
-- ============================================================

-- 注意: 此脚本需要先 CREATE EXTENSION timescaledb;

BEGIN;

-- ============================================================
-- 1. 策略配置表
-- ============================================================
CREATE TABLE strategies (
    id              UUID            PRIMARY KEY,
    name            TEXT            NOT NULL,
    type            TEXT            NOT NULL CHECK (type IN ('grid', 'dca', 'trend')),
    market          TEXT            NOT NULL CHECK (market IN ('spot', 'futures_u', 'futures_coin')),
    execution_mode  TEXT            NOT NULL CHECK (execution_mode IN ('auto', 'semi_auto')),
    params          JSONB           NOT NULL DEFAULT '{}',
    status          TEXT            NOT NULL DEFAULT 'created' CHECK (status IN ('created', 'running', 'paused', 'stopped')),
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    started_at      TIMESTAMPTZ,
    stopped_at      TIMESTAMPTZ
);

CREATE INDEX idx_strategies_status ON strategies(status) WHERE status = 'running';

-- ============================================================
-- 2. 交易记录表
-- ============================================================
CREATE TABLE trade_logs (
    id                UUID            PRIMARY KEY,
    strategy_id       UUID            NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    strategy_name     TEXT            NOT NULL,
    symbol            TEXT            NOT NULL,
    side              TEXT            NOT NULL CHECK (side IN ('BUY', 'SELL')),
    order_type        TEXT            NOT NULL CHECK (order_type IN ('MARKET', 'LIMIT')),
    quantity          NUMERIC(20,8)   NOT NULL,
    price             NUMERIC(20,8),
    filled_qty        NUMERIC(20,8),
    avg_price         NUMERIC(20,8),
    fee               NUMERIC(20,8)   DEFAULT 0,
    fee_currency      TEXT,
    execution_mode    TEXT            NOT NULL CHECK (execution_mode IN ('auto', 'semi_auto')),
    risk_decision     TEXT            NOT NULL CHECK (risk_decision IN ('approved', 'rejected')),
    decision_reason   TEXT            NOT NULL,
    external_order_id TEXT,
    status            TEXT            NOT NULL CHECK (status IN ('submitted', 'partial', 'filled', 'canceled', 'failed')),
    created_at        TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_trade_logs_strategy_time ON trade_logs(strategy_id, created_at DESC);
CREATE INDEX idx_trade_logs_symbol       ON trade_logs(symbol, created_at DESC);
CREATE INDEX idx_trade_logs_created_at   ON trade_logs(created_at DESC);

-- ============================================================
-- 3. 持仓表
-- ============================================================
CREATE TABLE positions (
    id              UUID            PRIMARY KEY,
    strategy_id     UUID            NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    symbol          TEXT            NOT NULL,
    market          TEXT            NOT NULL CHECK (market IN ('spot', 'futures_u', 'futures_coin')),
    quantity        NUMERIC(20,8)   NOT NULL,
    entry_price     NUMERIC(20,8)   NOT NULL,
    current_price   NUMERIC(20,8),
    unrealized_pnl  NUMERIC(20,8)   DEFAULT 0,
    realized_pnl    NUMERIC(20,8)   DEFAULT 0,
    leverage        INTEGER         DEFAULT 1,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_positions_strategy_symbol ON positions(strategy_id, symbol);
CREATE        INDEX idx_positions_strategy         ON positions(strategy_id);

-- ============================================================
-- 4. 风控事件表
-- ============================================================
CREATE TABLE risk_events (
    id                  UUID            PRIMARY KEY,
    event_type          TEXT            NOT NULL CHECK (event_type IN ('stop_loss', 'daily_loss', 'drawdown', 'position_limit', 'leverage', 'circuit_breaker')),
    detail              JSONB           NOT NULL DEFAULT '{}',
    related_strategy_id UUID            REFERENCES strategies(id) ON DELETE SET NULL,
    related_trade_id    UUID            REFERENCES trade_logs(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_risk_events_time     ON risk_events(created_at DESC);
CREATE INDEX idx_risk_events_strategy ON risk_events(related_strategy_id);

-- ============================================================
-- 5. AI 分析报告表
-- ============================================================
CREATE TABLE analysis_reports (
    id          UUID            PRIMARY KEY,
    model_used  TEXT            NOT NULL,
    content     TEXT            NOT NULL,
    suggestions JSONB           DEFAULT '[]',
    confidence  NUMERIC(3,2)    CHECK (confidence >= 0.0 AND confidence <= 1.0),
    symbols     TEXT            NOT NULL,
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_analysis_reports_time ON analysis_reports(created_at DESC);

-- ============================================================
-- 6. K线数据表 (TimescaleDB Hypertable)
-- ============================================================
CREATE TABLE klines (
    time        TIMESTAMPTZ         NOT NULL,
    symbol      TEXT                NOT NULL,
    interval    TEXT                NOT NULL,
    open        DOUBLE PRECISION    NOT NULL,
    high        DOUBLE PRECISION    NOT NULL,
    low         DOUBLE PRECISION    NOT NULL,
    close       DOUBLE PRECISION    NOT NULL,
    volume      DOUBLE PRECISION    NOT NULL,
    trades      INTEGER,
    PRIMARY KEY (time, symbol, interval)
);

-- 转为 Hypertable
SELECT create_hypertable('klines', 'time',
    chunk_time_interval => INTERVAL '1 day'
);

-- 列式压缩配置
ALTER TABLE klines SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol, interval',
    timescaledb.compress_orderby = 'time DESC'
);

-- 7 天后自动压缩
SELECT add_compression_policy('klines', INTERVAL '7 days');

-- 连续聚合: 1h K线
CREATE MATERIALIZED VIEW klines_1h
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS bucket,
    symbol,
    FIRST(open, time) AS open,
    MAX(high)         AS high,
    MIN(low)          AS low,
    LAST(close, time) AS close,
    SUM(volume)       AS volume
FROM klines
WHERE interval = '1m'
GROUP BY bucket, symbol;

SELECT add_continuous_aggregate_policy('klines_1h',
    start_offset    => INTERVAL '2 hours',
    end_offset      => INTERVAL '1 minute',
    schedule_interval => INTERVAL '5 minutes'
);

-- 90 天数据保留
SELECT add_retention_policy('klines', INTERVAL '90 days');

-- B-Tree 索引 (加速 symbol + interval + time 查询)
CREATE INDEX idx_klines_symbol_interval_time ON klines(symbol, interval, time DESC);

COMMIT;
