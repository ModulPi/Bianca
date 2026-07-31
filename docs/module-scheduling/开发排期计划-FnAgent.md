# FnAgent — 开发排期计划

> 版本：v1.0 | 日期：2026-07-28 | 团队：1 人（独立开发）

---

## 项目信息

| 项目 | 内容 |
|------|------|
| 项目名称 | FnAgent |
| 开始日期 | 2026-07-28 |
| 团队人数 | 1 人（独立开发） |
| 工作日 | 周一到周五，每天 4-6 有效工时 |
| 预计总工期 | **32 个工作日（~7 周）** |
| 预计结束 | 2026-09-09 |
| 缓冲天数 | 10 天（~30%） |
| **含缓冲结束** | **2026-09-23** |

---

## 模块依赖图

```
                         ┌─────────────┐
                         │  config.py  │ (无依赖)
                         └──────┬──────┘
                                │
          ┌─────────────────────┼─────────────────────┐
          ▼                     ▼                     ▼
   ┌──────────────┐    ┌──────────────┐     ┌──────────────┐
   │storage/models│    │exchange/     │     │strategy/base │
   │  + repository│    │  adapter     │     │  (ABC)       │
   └──────┬───────┘    └──────┬───────┘     └──────┬───────┘
          │                   │                    │
          ▼                   ▼                    ▼
   ┌──────────────┐    ┌──────────────┐     ┌──────────────┐
   │ api/schemas  │    │exchange/spot │     │strategy/     │
   │              │    │ /futures     │     │grid,dca,trend│
   └──────┬───────┘    │ /market_     │     └──────┬───────┘
          │            │  stream      │            │
          │            └──────┬───────┘            │
          │                   │                    │
          ▼                   ▼                    ▼
   ┌──────────────┐    ┌──────────────┐     ┌──────────────┐
   │ risk/engine  │    │ llm/analyzer │     │ graph/       │
   │ /rules       │    │ /prompts     │     │ strategy_agt │
   │ /circuit_brk │    └──────┬───────┘     │ execute_agt  │
   └──────┬───────┘           │             └──────┬───────┘
          │                   │                    │
          └───────────────────┼────────────────────┘
                              ▼
                      ┌──────────────┐
                      │ graph/       │
                      │ supervisor   │
                      │ risk_agent   │
                      │ analysis_agt │
                      └──────┬───────┘
                             │
                             ▼
                      ┌──────────────┐
                      │ api/routes   │
                      │ api/websocket│
                      └──────┬───────┘
                             │
                             ▼
                      ┌──────────────┐
                      │  main.py     │
                      │  (入口)      │
                      └──────────────┘
```

### 可并行开发的模块组

| 组 | 模块 | 条件 |
|----|------|------|
| **A 组** | config、strategy/base、exchange/adapter、llm/prompts | 无相互依赖 |
| **B 组** | strategy/grid、strategy/dca、strategy/trend | 都只依赖 base |
| **C 组** | exchange/spot、exchange/futures、exchange/market_stream | 都只依赖 adapter |
| **D 组** | risk/rules、risk/engine、risk/circuit_breaker | 都只依赖 risk 内部 |
| **E 组** | graph/strategy_agent、graph/execute_agent | 分别依赖 strategy 和 exchange |
| **F 组** | frontend 各页面 | 只依赖 API 契约（可 Mock） |

---

## 开发阶段与任务拆分

### 阶段 0：项目基础设施（3 天）

| # | 任务 | 依赖 | 工时 | 产出 |
|---|------|------|------|------|
| 0.1 | 项目初始化 (`pyproject.toml`, 目录结构, `.env.example`) | — | 0.5d | 项目骨架 |
| 0.2 | `config.py` — pydantic-settings 配置加载 | — | 0.5d | 配置中心 |
| 0.3 | Docker Compose 编排 (PG+Redis+Ollama) | — | 0.5d | 一键启动 |
| 0.4 | `storage/models.py` — SQLAlchemy ORM 6 张表 | 0.2 | 1d | 数据模型 |
| 0.5 | `storage/repository.py` — 仓储实现 | 0.4 | 0.5d | 数据访问层 |
| 0.6 | Alembic 迁移初始化 + 自动建表 | 0.4 | 0.5d | DDL 自动化 |

**阶段产出：** `docker-compose up` 后 PG/Redis/Ollama 就绪，Alembic 可自动建表。

---

### 阶段 1：交易所接口层（5 天）

| # | 任务 | 依赖 | 工时 | 产出 |
|---|------|------|------|------|
| 1.1 | `exchange/adapter.py` — 抽象适配器 + OrderResult | 0.2 | 0.5d | 接口定义 |
| 1.2 | `exchange/spot.py` — SpotAdapter (ccxt) | 1.1 | 1.5d | 现货交易 |
| 1.3 | `exchange/futures.py` — FuturesAdapter (ccxt) | 1.1 | 1d | 合约交易 |
| 1.4 | `exchange/rate_limiter.py` — Token Bucket 限流器 | — | 0.5d | API 限流 |
| 1.5 | `exchange/market_stream.py` — WebSocket 行情流 | 0.2 | 1.5d | 实时行情 |

**阶段产出：** 能通过 ccxt 查询余额、获取行情、在模拟盘下单。

---

### 阶段 2：策略引擎（4 天）

| # | 任务 | 依赖 | 工时 | 产出 |
|---|------|------|------|------|
| 2.1 | `strategy/base.py` — BaseStrategy ABC + TradeSignal | — | 0.5d | 策略基类 |
| 2.2 | `strategy/grid.py` — 网格交易策略 | 2.1 | 1.5d | 策略模板 1 |
| 2.3 | `strategy/dca.py` — 定投策略 | 2.1 | 1d | 策略模板 2 |
| 2.4 | `strategy/trend.py` — 趋势跟踪策略 | 2.1 | 1d | 策略模板 3 |

**阶段产出：** 3 个策略模板可独立运行 `analyze()` 生成 TradeSignal。

---

### 阶段 3：风控引擎（3 天）

| # | 任务 | 依赖 | 工时 | 产出 |
|---|------|------|------|------|
| 3.1 | `risk/rules.py` — 8 条风控规则实现 | — | 1.5d | 规则集 |
| 3.2 | `risk/engine.py` — 责任链引擎 | 3.1 | 0.5d | 风控引擎 |
| 3.3 | `risk/circuit_breaker.py` — 熔断器状态机 | 3.2 | 1d | 熔断器 |

**阶段产出：** RiskEngine.evaluate() 可独立验证任意 TradeSignal。

---

### 阶段 4：Agent 编排核心（4 天）

| # | 任务 | 依赖 | 工时 | 产出 |
|---|------|------|------|------|
| 4.1 | `graph/strategy_agent.py` — 策略 Agent 节点 | 阶段2, 1.2 | 1d | StrategyAgent |
| 4.2 | `graph/execute_agent.py` — 执行 Agent 节点 | 阶段1 | 1d | ExecuteAgent |
| 4.3 | `graph/risk_agent.py` — 风控 Agent 节点 | 阶段3 | 0.5d | RiskAgent |
| 4.4 | `graph/supervisor.py` — Supervisor + 图编译 | 4.1-4.3 | 1.5d | 完整 StateGraph |

**阶段产出：** `build_trading_graph()` 可编译并 invoke，完整走通 策略→风控→执行。

---

### 阶段 5：API 网关（3 天）

| # | 任务 | 依赖 | 工时 | 产出 |
|---|------|------|------|------|
| 5.1 | `api/schemas.py` — Pydantic 请求/响应模型 | 0.4 | 0.5d | API Schema |
| 5.2 | `api/routes.py` — 21 个 REST 端点 | 5.1, 阶段4 | 1.5d | REST API |
| 5.3 | `api/websocket.py` — WebSocket 端点 | 1.5 | 1d | WS API |
| 5.4 | `main.py` — FastAPI 入口 + uvicorn + 中间件 | 5.2-5.3 | 0.5d | 应用入口 |

**阶段产出：** `curl localhost:8000/health` 返回 OK，`/api/v1/strategies` CRUD 可用。

---

### 阶段 6：LLM 分析模块（2 天）

| # | 任务 | 依赖 | 工时 | 产出 |
|---|------|------|------|------|
| 6.1 | `llm/prompts.py` — 提示词模板 | — | 0.5d | Prompt 模板 |
| 6.2 | `llm/analyzer.py` — MarketAnalyzer | 6.1, 1.5 | 1d | LLM 分析器 |
| 6.3 | `graph/analysis_agent.py` — 分析 Agent 节点 | 6.2, 4.4 | 0.5d | AnalysisAgent |

**阶段产出：** POST `/analysis/generate` 可生成一份市场分析报告。

---

### 阶段 7：通知模块（1 天）

| # | 任务 | 依赖 | 工时 | 产出 |
|---|------|------|------|------|
| 7.1 | `notification/telegram.py` — Telegram Bot 推送 | 0.2 | 0.5d | Telegram 通知 |
| 7.2 | 通知集成到风控和策略生命周期 | 7.1, 5.2 | 0.5d | 事件驱动通知 |

**阶段产出：** 止损触发时收到 Telegram 消息。

---

### 阶段 8：前端开发（7 天）

| # | 任务 | 依赖 | 工时 | 产出 |
|---|------|------|------|------|
| 8.1 | 项目脚手架 (Vite + TS + Tailwind + Router) | — | 0.5d | 前端骨架 |
| 8.2 | 共享组件 (Layout、Sidebar、StatusBadge...) | 8.1 | 1d | 基础组件库 |
| 8.3 | DashboardPage — 仪表盘首页 | 8.2 | 1d | 首页 |
| 8.4 | StrategyListPage + StrategyDetailPage | 8.2 | 1.5d | 策略管理 |
| 8.5 | PositionPage + KLineChart | 8.2 | 1d | 持仓监控 |
| 8.6 | TradeHistoryPage + Export | 8.2 | 0.5d | 交易记录 |
| 8.7 | AnalysisPage — AI 报告展示 | 8.2 | 0.5d | 分析报告 |
| 8.8 | SettingsPage — API Key + 风控 + 通知配置 | 8.2 | 0.5d | 设置页 |
| 8.9 | API 对接 + WebSocket 实时推送 | 8.3-8.8, 阶段5 | 0.5d | 数据接通 |

**阶段产出：** 浏览器打开 `localhost:3000` 可完整操作所有功能。

---

### 阶段 9：测试 + 联调（3 天）

| # | 任务 | 依赖 | 工时 | 产出 |
|---|------|------|------|------|
| 9.1 | 单元测试 (pytest) — strategy、risk、exchange | 阶段2-4 | 1d | 核心逻辑测试 |
| 9.2 | 集成测试 — graph 完整流程 | 阶段4-5 | 0.5d | 端到端测试 |
| 9.3 | 模拟盘 24h+ 运行验证 | 阶段1-5 | 1d | 稳定性验证 |
| 9.4 | Bug 修复 + 文档完善 | 9.1-9.3 | 0.5d | 收尾 |

**阶段产出：** `pytest` 全绿，模拟盘连续运行无异常。

---

## 汇总

| 阶段 | 名称 | 工时 | 累计 | 里程碑 |
|------|------|------|------|--------|
| 0 | 项目基础设施 | 3d | 3d | — |
| 1 | 交易所接口层 | 5d | 8d | **M0: API 连通** |
| 2 | 策略引擎 | 4d | 12d | — |
| 3 | 风控引擎 | 3d | 15d | **M2: 风控集成** |
| 4 | Agent 编排核心 | 4d | 19d | **M1: 策略跑通** |
| 5 | API 网关 | 3d | 22d | — |
| 6 | LLM 分析模块 | 2d | 24d | **M3: LLM 分析** |
| 7 | 通知模块 | 1d | 25d | — |
| 8 | 前端开发 | 7d | 32d | **M4: Web 控制台** |
| 9 | 测试 + 联调 | 3d | 35d | 交付 |
| — | **风险缓冲 (30%)** | 10d | **45d** | — |

> ⚠️ **注意：** 阶段编号不代表执行顺序。实际执行顺序见下方甘特图。

---

## 执行顺序（关键路径）

```
【第 1-3 天】   阶段 0: 基础设施
【第 4-8 天】   阶段 1: 交易所接口层 ────────▶ M0 里程碑
【第 9-12 天】  阶段 2: 策略引擎    ┐
【第 9-11 天】  阶段 3: 风控引擎    ├─ 可并行 (不同模块)
【第 13-16 天】 阶段 4: Agent 编排 ────────▶ M1 + M2 里程碑
【第 17-19 天】 阶段 5: API 网关
【第 20-21 天】 阶段 6: LLM 分析 ─────────▶ M3 里程碑
【第 22 天】    阶段 7: 通知模块
【第 23-29 天】 阶段 8: 前端开发 ─────────▶ M4 里程碑
【第 30-32 天】 阶段 9: 测试联调 ─────────▶ 交付
【第 33-42 天】 风险缓冲
```

**关键路径：** 阶段 0→1→4→5→8→9（25 天，不含缓冲），不可压缩。
