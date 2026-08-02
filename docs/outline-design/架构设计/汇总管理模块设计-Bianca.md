# Bianca — 汇总管理模块设计（MVP）

> 版本：v0.1 | 日期：2026-08-02 | 阶段：MVP（P1） | 依赖：PoC 可观测性 API

---

## 1. 背景与目标

### 1.1 PoC 已有能力（分散）

| 能力 | PoC API | 缺口 |
|------|---------|------|
| Token 消耗 | `GET /usage` | 无会话维度、无成本估算 |
| 交易明细 | `GET /trades` | 需客户端自行算盈亏 |
| 决策明细 | `GET /decisions` | 与交易/盈亏未关联 |
| Agent 状态 | `GET /agent/status` | `daily_pnl` 为简化估算，不含持仓市值 |
| 账户余额 | `GET /exchange/balance` | 未与历史成交合并展示 |

PoC 验收（M3）已证明链路可跑通，但**缺少统一汇总视图**；运维/复盘需多次 curl 或查库。

### 1.2 MVP 目标

提供 **汇总管理模块（Summary & Observability）**，实现：

1. **一键会话汇总**：Token 消耗 + 成交统计 + 盈亏 + 闭环状态
2. **准确盈亏口径**：已实现 / 未实现 / 现金净流入 / 持仓市值
3. **持久化会话快照**：Agent 启停周期可回溯、可对比
4. **Web 控制台集成**：M7 仪表盘直接消费 Summary API
5. **可选导出**：JSON / CSV（MVP 先做 JSON）

---

## 2. 模块边界

```
┌─────────────────────────────────────────────────────────┐
│  Web 控制台 (M7) / CLI / Telegram 摘要 (M8)              │
└───────────────────────────┬─────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────┐
│  Summary Service（本模块）                               │
│  · SessionAggregator   — 会话级汇总                      │
│  · PnLCalculator       — 盈亏计算                        │
│  · UsageRollup         — Token rollup（扩 PoC /usage）   │
│  · SnapshotWriter      — 写入 session_summaries          │
└───────────────────────────┬─────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────┐
│  数据源（PoC 已有）                                       │
│  decision_logs · trade_logs · risk_events · agent_config │
│  exchange balance/ticker · agent runner 状态              │
└─────────────────────────────────────────────────────────┘
```

**不在本模块：** 策略模板执行、风控规则、下单执行（仍由 Agent 子图负责）。

---

## 3. API 设计（MVP 新增）

Base：`/api/v1/summary`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/session/current` | 当前运行中会话的实时汇总（Agent running 时） |
| GET | `/session/latest` | 最近一次已结束会话快照 |
| GET | `/sessions` | 历史会话列表（分页，`?limit=&offset=`） |
| GET | `/sessions/{id}` | 指定会话详情 |
| GET | `/daily` | 按 UTC 日汇总（`?date=2026-08-02`） |
| POST | `/sessions/{id}/close` | 手动结束并固化快照（Agent stop 时自动调用） |

### 3.1 PoC 保留并归口

| 现有端点 | MVP 处理 |
|----------|----------|
| `GET /usage` | 保留；Summary Service 内部复用，响应增加 `estimated_cost_usd`（可选） |
| `GET /trades` | 保留；Summary 引用其聚合结果 |
| `GET /decisions` | 保留 | |

### 3.2 响应结构（`SessionSummaryResponse`）

```json
{
  "session_id": "uuid",
  "started_at": "2026-08-02T07:21:00+00:00",
  "ended_at": "2026-08-02T07:29:11+00:00",
  "agent": {
    "tick_count": 8,
    "tick_interval_sec": 60,
    "trading_style": "aggressive",
    "last_status": "filled"
  },
  "usage": {
    "llm_calls": 28,
    "prompt_tokens": 9792,
    "completion_tokens": 5634,
    "total_tokens": 15426,
    "estimated_cost_usd": 0.012
  },
  "trades": {
    "buy_filled": 3,
    "sell_filled": 1,
    "failed": 5,
    "signal_only": 0,
    "loop_closed": true
  },
  "pnl": {
    "cash_flow_usdt": -55.24,
    "realized_usdt": 0.0004,
    "unrealized_usdt": 0.01,
    "total_usdt": 0.01,
    "daily_pnl_legacy": -55.30
  },
  "positions": {
    "base_asset": "BTC",
    "base_free": 0.00087,
    "usdt_free": 4795.25,
    "mark_price": 63491.5
  },
  "highlights": [
    "闭环：1 BUY filled + 1 SELL filled",
    "主要失败原因：max_trade_amount / SELL 参数错误（已修复）"
  ]
}
```

---

## 4. 盈亏计算口径

| 指标 | 定义 | MVP 实现 |
|------|------|----------|
| **cash_flow_usdt** | Σ(SELL 成交额) − Σ(BUY 成交额) | 来自 `trade_logs` filled |
| **realized_usdt** | 已平仓部分盈亏（加权成本法） | `PnLCalculator` |
| **unrealized_usdt** | 剩余持仓 × 现价 − 成本 | 结合 `exchange/balance` + ticker |
| **total_usdt** | realized + unrealized | 展示用 |
| **daily_pnl_legacy** | PoC `agent_config.daily_pnl` | 兼容字段，标注 deprecated |

**闭环判定：** 同一会话内 `buy_filled ≥ 1` 且 `sell_filled ≥ 1`。

---

## 5. 数据模型

### 5.1 新表 `session_summaries`（PostgreSQL / MVP）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID PK | 会话 ID |
| started_at | TIMESTAMPTZ | Agent start 时间 |
| ended_at | TIMESTAMPTZ | Agent stop / 自动 close |
| tick_count | INT | 循环次数 |
| trading_style | TEXT | conservative / aggressive |
| usage_json | JSONB | Token 汇总 |
| trades_json | JSONB | 成交计数 + 关键订单 ID 列表 |
| pnl_json | JSONB | 盈亏快照 |
| positions_json | JSONB | 结束时的持仓快照 |
| loop_closed | BOOLEAN | 是否完成买卖闭环 |
| created_at | TIMESTAMPTZ | 写入时间 |

DDL 见 `docs/outline-design/数据库设计/sql/002_mvp_postgres.sql` § session_summaries。

### 5.2 与 `analysis_reports` 的关系

| 表 | 用途 |
|----|------|
| `session_summaries` | **运行会话**维度：Token + 成交 + 盈亏（本模块） |
| `analysis_reports` | **LLM 分析报告**维度：长文解读、建议列表（Strategy/Analysis 扩展） |

会话汇总可嵌入 `analysis_reports` 的链接 ID，但不合并存储。

---

## 6. 服务流程

### 6.1 Agent 启停挂钩

```
POST /agent/start
  → 创建 session_id，记录 started_at（内存 + Redis 可选）

POST /agent/stop  / Runner 异常退出
  → SessionAggregator 计算汇总
  → 写入 session_summaries
  → 返回 SessionSummaryResponse（可选）
```

### 6.2 定时快照（可选，MVP P1.1）

Agent 运行中每 **N 分钟**（默认 15）写 `session_summaries` 中间态，`ended_at` 为空，便于 Web 实时刷新。

---

## 7. Web 控制台集成（M7）

| 页面 | 消费 API |
|------|----------|
| 仪表盘首页 | `/summary/session/current` 或 `/daily` |
| 交易复盘 | `/sessions/{id}` + `/trades?session_id=` |
| LLM 成本 | `/usage` + session.usage |
| 闭环验收 | `trades.loop_closed` 徽章 |

---

## 8. 里程碑与排期

| 里程碑 | 交付物 |
|--------|--------|
| **M6.5**（新增） | Summary API + `session_summaries` 表 + 单元测试 |
| **M7** | Web 汇总仪表盘 |
| **M8** | Telegram 每日摘要推送（调用 `/summary/daily`） |

**工时估算：** 2d（后端 Summary Service + API + 迁移）+ 1d（Web 仪表盘，含在 M7）

---

## 9. 验收标准

- [x] Agent stop 后可通过 `GET /summary/session/latest` 拿到完整汇总
- [x] `pnl.realized_usdt` 与手动按成交记录计算误差 < 0.01 USDT
- [x] `usage.total_tokens` 与 `decision_logs` 聚合一致
- [x] `loop_closed=true` 当且仅当存在 filled BUY 与 filled SELL
- [x] Web 控制台首页展示 Token、盈亏、闭环状态（M7）

---

## 10. PoC → MVP 迁移说明

| PoC | MVP |
|-----|-----|
| 分散的 `/usage`、`/trades` | 归口到 Summary Service，保留原 API 兼容 |
| SQLite `agent_config.daily_pnl` | 保留作 legacy；正式口径走 `PnLCalculator` |
| 无会话概念 | `session_id` 绑定 Agent 一次启停周期 |
| 手动查库复盘 | API + Web 一键查看 |
