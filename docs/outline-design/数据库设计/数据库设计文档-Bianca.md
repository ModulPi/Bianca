# Bianca — 数据库设计文档

> 版本：v0.3 | 日期：2026-07-31

---

## 1. 设计规范

| 规范项 | PoC (SQLite) | MVP (PostgreSQL) |
|--------|-------------|------------------|
| 主键 | TEXT (UUID 字符串) | UUID |
| 时间戳 | TEXT (ISO8601) 或 INTEGER | TIMESTAMPTZ |
| 金额 | REAL（PoC 可接受） | NUMERIC(20,8) |
| 命名 | snake_case | snake_case |

---

## 2. PoC 数据模型（SQLite）

### 2.1 ER 关系

```
agent_config (1) ──▶ 运行配置
trade_logs (N)   ──▶ 交易/信号记录
risk_events (N)  ──▶ 风控事件
decision_logs (N)──▶ LLM 决策详情
```

> PoC 无 `strategies` 表（无策略模板），无 `positions` 表（从 Demo API 实时查），无 `klines` 持久化（内存/文件缓存）。

### 2.2 trade_logs — 交易记录

| 列名 | 类型 | 说明 |
|------|------|------|
| `id` | TEXT PK | UUID |
| `symbol` | TEXT | 如 BTCUSDT |
| `side` | TEXT | BUY / SELL / HOLD（HOLD 仅记录） |
| `quantity` | REAL | 数量 |
| `price` | REAL | 价格（HOLD 可为 NULL） |
| `order_type` | TEXT | MARKET / LIMIT |
| `llm_confidence` | REAL | 0.0–1.0 |
| `decision_reason` | TEXT | LLM 决策理由 |
| `risk_decision` | TEXT | approved / rejected |
| `risk_reason` | TEXT | 风控拒绝原因 |
| `external_order_id` | TEXT | 币安订单 ID |
| `status` | TEXT | signal_only / submitted / filled / failed |
| `created_at` | TEXT | ISO8601 |

### 2.3 risk_events — 风控事件

| 列名 | 类型 | 说明 |
|------|------|------|
| `id` | TEXT PK | UUID |
| `event_type` | TEXT | max_trade_amount / daily_loss |
| `detail` | TEXT | JSON |
| `related_trade_id` | TEXT FK | 关联 trade_logs |
| `created_at` | TEXT | ISO8601 |

### 2.4 decision_logs — LLM 决策详情

| 列名 | 类型 | 说明 |
|------|------|------|
| `id` | TEXT PK | UUID |
| `model_used` | TEXT | 如 qwen2.5:7b |
| `prompt_summary` | TEXT | 输入摘要 |
| `raw_output` | TEXT | LLM 原始输出 |
| `parsed_signal` | TEXT | JSON: {action, symbol, amount, confidence, reason} |
| `created_at` | TEXT | ISO8601 |

### 2.5 agent_config — 运行配置（单行）

| 列名 | 类型 | 说明 |
|------|------|------|
| `key` | TEXT PK | 配置键 |
| `value` | TEXT | 配置值 |

---

## 3. MVP 数据模型（PostgreSQL + TimescaleDB）

PoC 验收后迁移至 PostgreSQL，新增/恢复以下表：

| 表 | 说明 |
|----|------|
| `strategies` | 策略模板配置 |
| `positions` | 持仓快照 |
| `pending_signals` | 半自动待确认信号 |
| `paper_validations` | 模拟验证记录（模拟→实盘门禁） |
| `analysis_reports` | LLM 分析报告 |
| `klines` | TimescaleDB 超表（K 线时序） |
| `api_keys` | AES-256 加密存储（替代 .env 明文） |

完整 DDL 见 `sql/002_mvp_postgres.sql`（MVP 阶段使用）。

---

## 4. 索引策略

### PoC (SQLite)

```sql
CREATE INDEX idx_trade_logs_created ON trade_logs(created_at DESC);
CREATE INDEX idx_trade_logs_side_status ON trade_logs(side, status);
CREATE INDEX idx_risk_events_time ON risk_events(created_at DESC);
```

### MVP (PostgreSQL)

见 `sql/002_mvp_postgres.sql`。

---

## 5. LangGraph Checkpointer

PoC 使用 **SqliteSaver**，Checkpointer 数据库文件独立于业务 SQLite：

```
data/
├── bianca.db          # 业务数据
└── checkpoints.db     # LangGraph 状态
```

MVP 可迁移至 PostgresSaver。
