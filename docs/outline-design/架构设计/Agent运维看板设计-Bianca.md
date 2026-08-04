# Bianca — Agent 运维看板设计

> 版本：v1.0 | 日期：2026-08-04 | 状态：设计稿  
> 定位：**Agent 运行态监控看板**（非交易终端、非 K 线看盘）

---

## 1. 设计原则

| 原则 | 说明 |
|------|------|
| **Agent 优先** | 看板回答「Agent 在干什么、干得怎样」，而非「我该怎么下单」 |
| **只读 + 干预** | 默认展示；允许启停 Agent、降级确认、恢复 auto，不提供手动下单 |
| **实时够用** | 行情/Worker 5s 级刷新；汇总/Token 15–60s；不追求毫秒级行情终端 |
| **多 Worker** | 按 `AGENT_SYMBOLS` 并行展示，每个 symbol 一行 Worker 状态 |
| **降级可见** | 自动降级、待确认队列、风控拒绝必须在看板顶部显著展示 |

**不做：** K 线大图、策略编辑器、密钥 Web CRUD、手动限价/市价下单表单。

---

## 2. 页面结构（单页看板 + 审计子页）

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Agent 运维看板                                    demo/live · crypto    │
├─────────────────────────────────────────────────────────────────────────┤
│ [A] 运行态总览          │ [B] 实盘 / 门禁信息      │ [C] Token 用量      │
├─────────────────────────┴────────────────────────┴────────────────────┤
│ [D] 并行 Worker 表（Agent 干活情况）                                       │
├─────────────────────────────────────────────────────────────────────────┤
│ [E] 仓位快照              │ [F] 收益（PnL）                              │
├───────────────────────────┴─────────────────────────────────────────────┤
│ [G] 进行中交易 + 降级确认队列                                              │
├─────────────────────────────────────────────────────────────────────────┤
│ [H] 实时行情（轻量 ticker，非 K 线终端）                                    │
└─────────────────────────────────────────────────────────────────────────┘
     快捷入口：成交明细 · 会话汇总 · 决策回放
```

---

## 3. 模块详细设计

### [A] 运行态总览

**目的：** 一眼判断 Agent 是否在 24×7 正常工作。

| 展示项 | 数据源（现有 API） | 刷新 |
|--------|-------------------|------|
| 运行状态（运行中/已停止） | `GET /api/v1/agent/status` → `running` | 5s |
| 执行模式（auto / semi_auto / signal_only） | `execution_mode` + `degraded` 徽章 | 5s |
| 市场类型 | `trade_market`（crypto / a_share / us_stock） | 5s |
| 并行 symbol 列表 | `symbols` | 5s |
| 累计 tick / 上次 tick 时间 | `tick_count`, `last_tick` | 5s |
| 最近错误 | `last_error` | 5s |
| 当前 session | `session_id`, `session_started_at` | 5s |

**操作按钮：**

| 操作 | API |
|------|-----|
| 启动 / 停止 | `POST /agent/start` · `POST /agent/stop` |
| 恢复 auto（清除降级） | `POST /agent/recover` |

**视觉：** 运行中绿色脉冲点；`degraded=true` 时顶部红色横幅「已自动降级，需人工介入」。

---

### [B] 实盘 / 门禁信息

**目的：** 区分 Demo 与 Live，展示实盘切换前置条件（非交易平台 onboarding 流程，仅状态只读）。

| 展示项 | 数据源 | 刷新 |
|--------|--------|------|
| 当前模式 demo / live | `GET /api/v1/trading/mode` → `mode` | 30s |
| 是否可切 live | `can_enable_live` | 30s |
| 模拟验证状态 | `validation_status` | 30s |
| 验证未通过原因 | `GET /api/v1/validation/status` → `reasons[]` | 60s |
| 交易所连通 | `GET /api/v1/health` → `binance_demo`, `binance_live` | 30s |
| LLM 连通 | `health.llm`, `llm_provider` | 30s |

**说明文案（静态）：** Live 需 `.env` 中 `LIVE_TRADING_CONFIRMED=true` 且模拟验证通过。

**不做：** 门禁重置、复杂验证向导（保留 API，看板只读）。

---

### [C] Token 用量

**目的：** 监控 LLM 成本与调用频率，评估 Agent「脑力」消耗。

| 展示项 | 数据源 | 刷新 |
|--------|--------|------|
| 今日 calls / tokens | `GET /api/v1/usage` → `today.*` | 60s |
| 累计 calls / tokens | `usage.total.*` | 60s |
| 当前会话 Token | `GET /summary/session/current` → `usage.*`（运行中） | 15s |
| 最近会话 Token | `GET /summary/session/latest`（停止后） | 15s |
| 预估成本 USD | `usage.estimated_cost_usd` | 60s |

**可选增强（后续）：** 按 symbol Worker 分摊 Token（需后端在 decision 记录中关联 `symbol`）。

---

### [D] 并行 Worker 表 — Agent 干活情况

**目的：** 多 symbol 并行时，看清每个 Worker 是否在正常 tick。

| 列 | 字段 | 数据源 |
|----|------|--------|
| Symbol | `workers[].symbol` | `agent/status` |
| Tick 次数 | `workers[].tick_count` | 5s |
| 最近状态 | `workers[].last_status`（filled / awaiting_confirmation / risk_rejected …） | 5s |
| 最近 tick 时间 | `workers[].last_tick` | 5s |
| 最近错误 | `workers[].last_error` | 5s |
| 决策回放 | 链接 | `/checkpoints?thread={session_id}:{symbol}` |

**行级颜色：**

- 正常：`last_status` 为 filled / signal_only / HOLD 相关
- 警告：awaiting_confirmation、semi_auto 队列有积压
- 错误：`last_error` 非空或连续失败触发降级

---

### [E] 仓位快照

**目的：** 展示 Agent 当前持有的资产，非「交易平台持仓页」。

| 展示项 | 数据源 | 刷新 |
|--------|--------|------|
| USDT 可用 / 冻结 | `GET /exchange/balance` → `free.USDT`, `used.USDT` | 20s |
| 各 base 资产数量 | `free.BTC`, `free.ETH`…（按 `AGENT_SYMBOLS` 推导 base） | 20s |
| 标记价 | 各 symbol `GET /exchange/ticker?symbol=` | 20s |
| 名义价值 USDT | 前端计算 `qty × last` | — |
| 会话级持仓汇总 | `summary/session/current` → `positions.*` | 15s |

**多 symbol 扩展（后续 API）：**

```
GET /api/v1/dashboard/positions?symbols=BTCUSDT,ETHUSDT
→ [{ symbol, base, free, used, mark, notional_usdt }]
```

当前可轮询 `balance` + 多次 `ticker` 拼装。

---

### [F] 收益（PnL）

**目的：** 区分已实现 / 未实现 / 现金净流入，服务 Agent 效果评估。

| 展示项 | 数据源 | 刷新 |
|--------|--------|------|
| 已实现盈亏 USDT | `summary.session.pnl.realized_usdt` | 15s |
| 未实现盈亏 USDT | `pnl.unrealized_usdt` | 15s |
| 现金净流入 USDT | `pnl.cash_flow_usdt` | 15s |
| 合计 total_usdt | `pnl.total_usdt` | 15s |
| 当日 legacy PnL | `pnl.daily_pnl_legacy` / `agent/status.daily_pnl` | 15s |
| 闭环徽章 | `trades.loop_closed`（PoC 指标，24×7 模式下仅作参考） | 15s |

**轻量曲线（可选，非 K 线）：** 基于 `GET /trades?limit=100` 按时间累计 realized 折线，仅会话级趋势，不是行情 K 线。

---

### [G] 进行中交易 + 降级确认

**目的：** 展示「尚未结束」的交易动作；降级时人工介入入口。

#### G1 — 进行中 / 近期交易

| 状态 | 含义 | 数据源 |
|------|------|--------|
| `submitted` / 非 filled | 已提交未成交 | `GET /trades?status=submitted` |
| `pending` 待确认 | 半自动等待人工 | `GET /pending-signals` + WS `confirmation_required` |
| 最近 filled | 最近成交 | `GET /trades?status=filled&limit=10` |
| 风控拒绝 | 最近拦截 | `GET /risk/events?limit=10` |

**表格列：** 时间 · symbol · side · 数量 · 价格 · status · 决策理由 · 风控结果

#### G2 — 降级确认队列（semi_auto / 自动降级时展示）

| 项 | 数据源 |
|----|--------|
| 待确认信号列表 | `GET /pending-signals` |
| 实时推送 | WS `/api/v1/ws/system` → `confirmation_required` |
| 确认 / 拒绝 | `POST /pending-signals/{id}/confirm` · `reject` |

---

### [H] 实时行情（轻量）

**目的：** 给 Agent 决策提供上下文价格，**不是** K 线交易终端。

| 展示项 | 数据源 | 刷新 |
|--------|--------|------|
| 各 symbol last / bid / ask | `GET /exchange/ticker?symbol=` | 5–10s |
| 24h 涨跌（后续） | 需 ticker 扩展或 ccxt 字段 | — |

**不做：** 全屏 K 线、画线工具、订单簿深度。

---

## 4. 数据流与刷新策略

```
┌──────────────┐     5s poll      ┌─────────────────┐
│  Web 看板     │◀───────────────│ /agent/status   │
│  (React)      │     20s poll   │ /exchange/*     │
│               │◀───────────────│ /trades         │
│               │     15s poll   │ /summary/*      │
│               │◀───────────────│ /usage          │
│               │     WS         │ /ws/system      │
└──────────────┘                 └─────────────────┘
```

| 数据类型 | 推荐间隔 | 说明 |
|----------|----------|------|
| Agent / Worker | 5s | 核心运行态 |
| Ticker | 10s | 实时价格 |
| Balance / 仓位 | 20s | 交易所 REST |
| 会话 PnL / Token | 15s | 汇总快照 |
| Health / 门禁 | 30–60s | 低频即可 |

---

## 5. API 映射总表

| 看板模块 | 已有 API | 待补充 API |
|----------|----------|------------|
| 运行态总览 | `/agent/status`, `/agent/start`, `/stop`, `/recover` | — |
| 实盘信息 | `/trading/mode`, `/validation/status`, `/health` | — |
| Token | `/usage`, `/summary/session/current\|latest` | 按 Worker 分摊 Token |
| Worker 表 | `/agent/status.workers[]` | — |
| 仓位 | `/exchange/balance`, `/exchange/ticker`, `summary.positions` | `/dashboard/positions` 聚合 |
| 收益 | `/summary/session/*`, `/agent/status.daily_pnl` | — |
| 进行中交易 | `/trades`, `/pending-signals`, `/risk/events` | `/trades?status=in_progress` 语义统一 |
| 实时行情 | `/exchange/ticker` | 批量 ticker 一次请求 |
| 审计 | `/checkpoints/*`, `/summary/sessions` | — |

**建议新增（P1 实现）：**

```http
GET /api/v1/dashboard/snapshot
```

一次返回看板所需聚合 JSON（减少前端 8+ 路轮询）：

```json
{
  "agent": { "...AgentStatusResponse" },
  "trading_mode": { "...TradingModeResponse" },
  "health": { "...HealthResponse" },
  "usage": { "...UsageSummaryResponse" },
  "session": { "...SessionSummaryResponse" },
  "positions": [{ "symbol": "BTCUSDT", "base": "BTC", "free": 0.01, "mark": 65000, "notional_usdt": 650 }],
  "open_trades": [{ "...TradeLogItem" }],
  "pending_signals": [{ "...PendingSignalItem" }],
  "tickers": [{ "symbol": "BTCUSDT", "last": 65000 }]
}
```

---

## 6. 与产品定位的关系

| 用户期望 | 看板如何满足 | 刻意不做 |
|----------|--------------|----------|
| 看实时数据 | 轻量 ticker + Worker 状态 | K 线终端 |
| 看进行中交易 | trades + pending + WS 确认 | 手动下单 |
| 看仓位 | balance + positions 快照 | 多账户资产管理系统 |
| 看实盘信息 | demo/live + 验证状态只读 | 复杂开户/onboarding |
| 看收益 | PnL 四分项 + 可选折线 | 投资组合分析器 |
| 看 Agent 干活 | Worker 表 + tick + 执行模式 | 策略商城 |
| 看 Token | usage + session 汇总 | — |

---

## 7. 验收标准（US-M01 对齐）

- [ ] 单页看板展示 A–H 八个模块（可折叠次要模块）
- [ ] Agent 运行 / 停止 / 恢复 auto 可操作
- [ ] 降级时确认队列自动展开，WS + 轮询双通道
- [ ] 盈亏展示 realized / unrealized / cash_flow / total 四分项
- [ ] Token 展示今日 + 当前会话消耗
- [ ] 多 Worker 时每个 symbol 独立一行状态
- [ ] demo / live 模式与验证状态只读展示
- [ ] 刷新符合第 4 节策略，页面无明显卡顿

---

## 8. 实施分期

| 阶段 | 内容 | 优先级 |
|------|------|--------|
| **P0** | 复用现有 API 拼装看板（当前 `web/` 运维页扩展） | 立即 |
| **P1** | `GET /dashboard/snapshot` 聚合 API | 高 |
| **P1** | 批量 ticker、按 Worker Token 分摊 | 中 |
| **P2** | A 股/美股仓位字段扩展（适配层落地后） | 低 |

---

## 9. 相关文档

- 用户故事：[US-M01 运维看板](../用户故事-Bianca.md#us-m01agent-运维看板)
- 汇总口径：[汇总管理模块设计](./汇总管理模块设计-Bianca.md)
- 架构：[架构设计文档](./架构设计文档-Bianca.md)
