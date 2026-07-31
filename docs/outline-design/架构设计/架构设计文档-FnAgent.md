# FnAgent — 架构设计文档 (C4 模型)

> 版本：v1.0 | 日期：2026-07-28 | 基于系统设计 v1.0

---

## 目录

1. [C4 Level 1: 系统上下文](#1-c4-level-1-系统上下文)
2. [C4 Level 2: 容器图](#2-c4-level-2-容器图)
3. [C4 Level 3: 组件图](#3-c4-level-3-组件图)
4. [ADR 架构决策记录](#4-adr-架构决策记录)
5. [关键时序流程](#5-关键时序流程)
6. [模块接口契约](#6-模块接口契约)

---

## 1. C4 Level 1: 系统上下文

```
                      ┌─────────────────────┐
                      │   币安交易所 (Binance) │
                      │   - 现货 REST/WS     │
                      │   - 合约 REST/WS     │
                      │   - Demo Mode (模拟) │
                      └──────────┬──────────┘
                                 │ HTTPS / WSS
                                 │
┌─────────────────┐     ┌───────┴───────┐     ┌─────────────────┐
│   交易用户 (人)   │────▶│   FnAgent     │◀────│  Ollama (本地)   │
│   Web 浏览器     │◀────│   自动交易系统  │     │  本地 LLM 推理   │
└─────────────────┘     └───────┬───────┘     └─────────────────┘
                                │
                                │ HTTPS
                         ┌──────┴──────┐
                         │  Telegram   │
                         │  (告警通知)  │
                         └─────────────┘
```

### 外部系统说明

| 外部系统 | 关系 | 协议 | 数据流向 |
|----------|------|------|----------|
| **币安交易所** | 交易执行 + 行情采集 | REST + WebSocket (TLS 1.3) | 双向：查询/下单 →，行情/订单状态 ← |
| **Ollama** | LLM 推理服务 | HTTP (本地) | 单向：FnAgent 发送 prompt →，接收分析文本 ← |
| **Telegram Bot API** | 告警通知推送 | HTTPS | 单向：FnAgent → 用户手机 |
| **用户浏览器** | 人机交互 | HTTPS + WSS | 双向：用户操作 →，UI 展示 ← |

### 数据流类型

| 流 | 描述 | 敏感度 |
|----|------|--------|
| 行情数据 | 实时价格、K 线、深度 | 公开 |
| 交易指令 | 下单、撤单 | **高** (含 API Key 签名) |
| 账户信息 | 余额、持仓 | **高** |
| LLM 分析 | 市场分析文本、交易建议 | 中 (本地，不外传) |
| 告警通知 | 风控触发、异常事件 | 中 |

---

## 2. C4 Level 2: 容器图

```
┌────────────────────────────────────────────────────────────────┐
│                         FnAgent 系统                            │
│                                                                  │
│  ┌──────────────────┐     ┌──────────────────────────────┐     │
│  │  Web 前端 (SPA)   │     │   API 网关 (FastAPI)          │     │
│  │  React + TS       │────▶│   - REST: /api/v1/*          │     │
│  │  Nginx 静态托管    │◀────│   - WS: /ws/market           │     │
│  │  端口: 3000       │     │   - WS: /ws/system           │     │
│  └──────────────────┘     │   端口: 8000                  │     │
│                           └──────────┬───────────────────┘     │
│                                      │                          │
│                           ┌──────────┴───────────────────┐     │
│                           │  Agent 编排核心 (Python)       │     │
│                           │  LangGraph StateGraph         │     │
│                           │  Supervisor → Strategy        │     │
│                           │           → Analysis          │     │
│                           │           → Risk → Execute    │     │
│                           └──┬──────────┬──────────┬─────┘     │
│                              │          │          │            │
│              ┌───────────────┘          │          └───────┐    │
│              ▼                          ▼                  ▼    │
│  ┌──────────────────┐  ┌──────────────────┐  ┌────────────┐   │
│  │  PostgreSQL 16    │  │  Redis 7          │  │ Ollama     │   │
│  │  + TimescaleDB 2  │  │  - 行情缓存       │  │ Container  │   │
│  │  - 交易记录        │  │  - 策略状态       │  │ qwen2.5:7b │   │
│  │  - K线 (超表)      │  │  - 熔断标记       │  │ 端口:11434 │   │
│  │  - 配置           │  │  - WS 会话        │  └────────────┘   │
│  │  端口: 5432       │  │  端口: 6379       │                   │
│  └──────────────────┘  └──────────────────┘                    │
│                                                                  │
│  ┌──────────────────────────────────────────────────────┐       │
│  │  行情采集器 (Market Stream Manager)                    │       │
│  │  - python-binance WebSocket                           │       │
│  │  - 多路复用多交易对 K 线/深度流                         │       │
│  │  - 写入 Redis 缓存                                    │       │
│  └──────────────────────────────────────────────────────┘       │
│                                                                  │
│  ┌──────────────────┐                                           │
│  │  通知服务          │                                           │
│  │  Telegram Bot     │                                           │
│  └──────────────────┘                                           │
└────────────────────────────────────────────────────────────────┘
```

### 容器职责

| 容器 | 技术栈 | 职责 | 通信方式 |
|------|--------|------|----------|
| **Web 前端** | React 18 + Vite + Nginx | 用户交互界面 | → REST/WS to API 网关 |
| **API 网关** | FastAPI + Uvicorn | 请求路由、参数校验、WebSocket 管理 | 内部 Python 调用 |
| **Agent 编排核心** | LangGraph + LangChain | 策略执行、风控审核、订单管理 | 内部 Python 调用 |
| **行情采集器** | python-binance asyncio | 实时行情采集与分发 | WSS → 币安，写入 Redis |
| **PostgreSQL + TimescaleDB** | Docker 容器 | 交易记录、K 线、配置持久化 | TCP 5432 |
| **Redis** | Docker 容器 | 行情缓存、会话、锁 | TCP 6379 |
| **Ollama** | Docker 容器 | 本地 LLM 推理 | HTTP 11434 |
| **通知服务** | Python (内嵌) | Telegram 消息推送 | HTTPS → Telegram API |

---

## 3. C4 Level 3: 组件图

### 3.1 Agent 编排核心内部组件

```
┌─────────────────────────────────────────────────────────────┐
│                   Agent 编排核心 (Python)                     │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  API Layer (api/)                                     │    │
│  │  ├─ routes.py      — REST 端点 (21 个接口)            │    │
│  │  ├─ schemas.py     — Pydantic 请求/响应模型           │    │
│  │  └─ websocket.py   — WS 连接管理 + 消息广播            │    │
│  └──────────────────────────┬──────────────────────────┘    │
│                             │                                 │
│  ┌──────────────────────────┴──────────────────────────┐    │
│  │  Service Layer (服务层)                                │    │
│  │  ├─ StrategyService   — 策略 CRUD + 生命周期          │    │
│  │  ├─ OrderService      — 订单管理 + 状态机             │    │
│  │  ├─ MarketService     — 行情查询 + K线聚合            │    │
│  │  ├─ RiskService       — 风控规则查询 + 熔断重置       │    │
│  │  ├─ AnalysisService   — LLM 分析调度 + 报告管理       │    │
│  │  └─ NotificationSvc   — Telegram 通知分发             │    │
│  └──────────────────────────┬──────────────────────────┘    │
│                             │                                 │
│  ┌──────────────────────────┴──────────────────────────┐    │
│  │  Agent Graph Layer (graph/)                           │    │
│  │                                                         │    │
│  │  ┌──────────────┐                                      │    │
│  │  │  Supervisor   │◀── 入口，解析意图，状态管理          │    │
│  │  └──┬───┬───┬───┘                                      │    │
│  │     │   │   │                                            │    │
│  │  ┌──┘   │   └──┐                                         │    │
│  │  ▼      ▼      ▼                                         │    │
│  │ ┌────┐┌────┐┌────┐┌────┐                               │    │
│  │ │Stra││Ana ││Risk││Exec│                               │    │
│  │ │tegy││lysi││    ││ute │                               │    │
│  │ │Agt ││sAgt││Agt ││Agt │                               │    │
│  │ └────┘└────┘└────┘└────┘                               │    │
│  │              │                                           │    │
│  │       ┌──────┴──────┐                                   │    │
│  │       │MemorySaver  │  ← 状态持久化 (内存/SQLite)       │    │
│  │       └─────────────┘                                   │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Domain Layer                                            │   │
│  │  ├─ strategy/   — BaseStrategy, GridStrategy, DCA...    │   │
│  │  ├─ risk/       — RiskEngine, RiskRule, CircuitBreaker  │   │
│  │  ├─ exchange/   — ExchangeAdapter, Spot, Futures        │   │
│  │  ├─ llm/        — MarketAnalyzer, PromptTemplates       │   │
│  │  └─ notification/ — TelegramSender, EmailSender         │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Infrastructure Layer                                    │   │
│  │  ├─ storage/    — SQLAlchemy ORM, Repository, Alembic   │   │
│  │  ├─ config.py   — pydantic-settings, .env 加载          │   │
│  │  └─ market_stream.py — WebSocket 行情采集 + Redis 写入  │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 组件依赖图（按接口边界）

```
api/routes.py ────── 依赖 ──────▶ StrategyService
    │                              ├── strategy/BaseStrategy
    │                              └── storage/Repository
    │
    ├──────────────────────────▶ OrderService
    │                              └── exchange/ExchangeAdapter
    │                                    ├── SpotAdapter → ccxt (现货)
    │                                    └── FuturesAdapter → ccxt (合约)
    │
    ├──────────────────────────▶ MarketService
    │                              ├── exchange/MarketStreamManager
    │                              │     └── python-binance WebSocket
    │                              └── storage/Repository (K线查询)
    │
    ├──────────────────────────▶ RiskService
    │                              └── risk/RiskEngine
    │                                    └── risk/rules/* (8 条规则)
    │
    ├──────────────────────────▶ AnalysisService
    │                              └── llm/MarketAnalyzer
    │                                    └── Ollama HTTP API
    │
    └──────────────────────────▶ graph/supervisor.py
                                   └── LangGraph StateGraph
                                         ├── StrategyAgent
                                         ├── AnalysisAgent
                                         ├── RiskAgent (必经)
                                         └── ExecuteAgent
```

### 3.3 前端组件结构

```
App
├── Layout
│   ├── Sidebar (路由导航)
│   │   ├── NavItem("/")
│   │   ├── NavItem("/strategies")
│   │   ├── NavItem("/positions")
│   │   ├── NavItem("/trades")
│   │   ├── NavItem("/analysis")
│   │   └── NavItem("/settings")
│   └── Header
│       ├── ConnectionStatus (WS 连接指示器)
│       ├── CircuitBreakerStatus (熔断状态)
│       └── AccountBalance (实时余额)
│
├── Pages
│   ├── DashboardPage
│   │   ├── AssetOverviewCard (总资产、今日盈亏)
│   │   ├── PnLChart (收益曲线 Recharts)
│   │   ├── StrategyRunningList (运行中策略)
│   │   └── RecentTradeTable (最近 10 笔交易)
│   │
│   ├── StrategyListPage
│   │   ├── StrategyCard[] (策略卡片)
│   │   ├── CreateStrategyDialog
│   │   └── DeleteConfirmDialog
│   │
│   ├── StrategyDetailPage
│   │   ├── StrategyConfigForm (参数表单 + 实时校验)
│   │   ├── ExecutionModeToggle (auto/semi_auto)
│   │   ├── StrategyControlBar (启动/暂停/停止)
│   │   ├── SignalLogTable
│   │   └── ConfirmationDialog (半自动确认弹窗)
│   │
│   ├── PositionPage
│   │   ├── PositionTable (持仓列表)
│   │   └── KLineChart (lightweight-charts)
│   │
│   ├── TradeHistoryPage
│   │   ├── TradeFilterBar (时间/策略/交易对)
│   │   ├── TradeTable (分页)
│   │   └── ExportCSVButton
│   │
│   ├── AnalysisPage
│   │   ├── ReportList
│   │   └── ReportDetail (Markdown 渲染)
│   │
│   └── SettingsPage
│       ├── ApiKeyForm (加密配置)
│       ├── RiskParamsForm (风控参数)
│       └── NotificationConfigForm (通知渠道)
│
└── Shared Components
    ├── KLineChart (TradingView lightweight-charts 封装)
    ├── ConfirmButton (带二次确认的按钮)
    ├── StatusBadge (状态标签: running/paused/stopped)
    └── EmptyState (空状态占位)
```

---

## 4. ADR 架构决策记录

### ADR-001: 选择 LangGraph Supervisor 而非 AutoGen GroupChat

- **状态:** ✅ 已采纳
- **日期:** 2026-07-28
- **背景:** 需要 Agent 编排框架来协调策略、风控、执行
- **决策:** 采用 LangGraph StateGraph + Supervisor 模式
- **理由:**
  - 显式有向图天然匹配「策略→风控→执行」管道
  - 风控作为必经节点在图拓扑中不可绕过（安全硬保证）
  - MemorySaver / SqliteSaver 开箱即用，满足审计需求
  - 比 AutoGen GroupChat 更适合交易系统（中心化路由可控、可审计）
- **代价:** Token 开销比 Swarm 模式高 20-35%，但在交易场景可接受
- **替代方案:** AutoGen GroupChat（灵活性高但不可控）、CrewAI（隐式协作难审计）

### ADR-002: ccxt 主力 + python-binance WebSocket 补充

- **状态:** ✅ 已采纳
- **日期:** 2026-07-28
- **背景:** 需要对接币安现货 + 合约 API，后期可能扩展多交易所
- **决策:** 交易执行用 ccxt，行情采集用 python-binance WebSocket
- **理由:**
  - ccxt 统一 API 意味着「后期加 OKX/Gate.io」只需换参数
  - python-binance 的 multiplex_socket 多路复用多交易对行情，比 ccxt WebSocket 更完善
  - 两者互补而非替代
- **代价:** 引入两个依赖库，需统一异常处理
- **替代方案:** 单用 ccxt（WebSocket 能力弱）、单用 python-binance（锁定币安）

### ADR-003: TimescaleDB 超表而非普通 PostgreSQL 表存储 K 线

- **状态:** ✅ 已采纳
- **日期:** 2026-07-28
- **背景:** K 线时序数据量大，需要高效存储和快速 OHLCV 聚合
- **决策:** K 线表使用 TimescaleDB Hypertable + 列式压缩
- **理由:**
  - 压缩率 10-20x，年增 2.2GB 降到 ~200MB（1000 DAU 级别）
  - 连续聚合预计算 1h/1d K 线，查询从 1s 降到 15ms
  - 100% 兼容 PostgreSQL，无需额外学习成本
- **代价:** Docker 镜像从 postgres 换成 timescaledb，微量运维复杂度
- **替代方案:** 普通 PG 表 + 手动分区（运维成本高）、InfluxDB（不兼容 SQL）

### ADR-004: 风控 Agent 在图拓扑中硬编码，不可绕过

- **状态:** ✅ 已采纳
- **日期:** 2026-07-28
- **背景:** 策略信号必须经风控审核才能执行，这是交易系统安全底线
- **决策:** 在 LangGraph 图中，Risk Agent 是所有交易路径的必经节点，无旁路
- **理由:**
  - 代码层硬保证：图中不存在 strategy → execute 的直接边
  - 即使 Strategy Agent 输出信号，也必须经过 Risk Agent 的 `add_edge`
  - 熔断状态持久化在 Redis，Risk Agent 启动时强制读取
- **代价:** 半自动确认场景多一跳延迟（用户确认 → Risk → Execute）
- **替代方案:** 软约定（代码 review 保证）— 不可靠

### ADR-005: PoC 用 MemorySaver，MVP 切 SQLite Checkpointer

- **状态:** ✅ 已采纳
- **日期:** 2026-07-28
- **背景:** LangGraph 需要状态持久化来支持多轮 Agent 协作和审计
- **决策:** PoC 阶段用 MemorySaver（内存），MVP 阶段切 SqliteSaver（持久化）
- **理由:**
  - PoC 只验证流程，内存在线足够
  - MVP 需要跨进程重启保留状态 + 审计回溯
  - SQLite 零配置，与 Docker Compose 单机部署一致
  - 后期可平滑升级到 PostgresSaver
- **代价:** PoC 重启丢失状态（不影响验证）
- **替代方案:** 直接上 PostgresSaver（PoC 阶段过度设计）

---

## 5. 关键时序流程

### 5.1 全自动策略交易流程

```
用户         API网关      Supervisor    StrategyAgt   RiskAgent    ExecuteAgt   币安       Redis       DB
 │              │              │              │            │            │          │          │          │
 │  POST start  │              │              │            │            │          │          │          │
 │─────────────▶│              │              │            │            │          │          │          │
 │              │  invoke()    │              │            │            │          │          │          │
 │              │─────────────▶│              │            │            │          │          │          │
 │              │              │  route()     │            │            │          │          │          │
 │              │              │─────────────▶│            │            │          │          │          │
 │              │              │              │ 读取 K线    │            │          │          │          │
 │              │              │              │────────────────────────────────────▶│          │          │
 │              │              │              │◀─────────────────────────────────────│          │          │
 │              │              │              │            │            │          │          │          │
 │              │              │              │ generate_signal()       │          │          │          │
 │              │              │              │────────────▶│            │          │          │          │
 │              │              │              │             │ 责任链检查  │          │          │          │
 │              │              │              │             │───────────▶│          │          │          │
 │              │              │              │             │ 读取熔断状态 │          │          │          │
 │              │              │              │             │──────────────────────▶│          │          │
 │              │              │              │             │◀──────────────────────│          │          │
 │              │              │              │             │            │          │          │          │
 │              │              │              │             │ approved   │          │          │          │
 │              │              │              │◀────────────│            │          │          │          │
 │              │              │              │             │            │          │          │          │
 │              │              │              │─────────────────────────▶│          │          │          │
 │              │              │              │             │            │ create_order()       │          │
 │              │              │              │             │            │─────────▶│          │          │
 │              │              │              │             │            │◀─────────│          │          │
 │              │              │              │             │            │          │          │          │
 │              │              │              │             │            │ INSERT trade_log      │          │
 │              │              │              │             │            │──────────────────────────────▶│
 │              │              │              │             │            │          │          │          │
 │  200 OK      │              │              │             │            │          │          │          │
 │◀─────────────│              │              │             │            │          │          │          │
 │              │              │              │             │            │          │          │          │
 │  WS push     │              │              │             │            │          │          │          │
 │◀─────────────│              │              │             │            │          │          │          │
```

### 5.2 半自动策略确认流程

```
用户         API网关      前端WS        Supervisor    StrategyAgt    用户操作
 │              │            │              │              │            │
 │              │            │              │  generate    │            │
 │              │            │              │─────────────▶│            │
 │              │            │              │   signal     │            │
 │              │            │              │◀─────────────│            │
 │              │            │              │              │            │
 │              │  WS push   │              │              │            │
 │              │───────────▶│              │              │            │
 │              │            │              │              │            │
 │              │  弹窗: 确认交易?         │              │            │
 │◀─────────────│◀───────────│              │              │            │
 │              │            │              │              │            │
 │  点击 "确认"  │            │              │              │            │
 │─────────────▶│            │              │              │            │
 │              │───────────▶│              │              │            │
 │              │            │              │              │            │
 │              │  POST /strategies/{id}/confirm            │            │
 │              │──────────────────────────▶│              │            │
 │              │            │              │────▶ RiskAgent            │
 │              │            │              │         (继续风控+执行)    │
 │              │            │              │              │            │
 │  超时 30min 不响应 → 自动丢弃信号         │              │            │
```

### 5.3 LLM 分析报告生成流程

```
Cron/定时         AnalysisService    MarketAnalyzer    Ollama     Redis       DB
 │                    │                  │              │          │          │
 │  触发 (每N小时)     │                  │              │          │          │
 │───────────────────▶│                  │              │          │          │
 │                    │ analyze_market() │              │          │          │
 │                    │─────────────────▶│              │          │          │
 │                    │                  │ 获取多框架K线  │          │          │
 │                    │                  │─────────────────────────▶│
 │                    │                  │◀─────────────────────────│
 │                    │                  │              │          │          │
 │                    │                  │  /api/generate           │          │
 │                    │                  │─────────────▶│          │          │
 │                    │                  │◀─────────────│          │          │
 │                    │                  │ (2-10s 推理)  │          │          │
 │                    │                  │              │          │          │
 │                    │  报告 + 建议      │              │          │          │
 │                    │◀─────────────────│              │          │          │
 │                    │                  │              │          │          │
 │                    │ INSERT analysis_report                    │          │
 │                    │─────────────────────────────────────────────────────▶│
 │                    │                  │              │          │          │
 │                    │  WS push 新报告   │              │          │          │
 │                    │──────────────────▶ (通知前端)   │          │          │
```

### 5.4 风控熔断触发流程

```
RiskAgent         CircuitBreaker    Redis          Notification   前端WS
 │                    │               │               │             │
 │  evaluate()        │               │               │             │
 │───────────────────▶│               │               │             │
 │                    │ 检查日亏损     │               │             │
 │                    │─────────────────────────▶    │             │
 │                    │ 累计亏损 >= 限额│               │             │
 │                    │◀─────────────────────────    │             │
 │                    │               │               │             │
 │                    │ SET circuit:OPEN             │             │
 │                    │──────────────▶│               │             │
 │                    │               │               │             │
 │                    │               │  发送告警      │             │
 │                    │               │──────────────▶│             │
 │                    │               │               │ Telegram    │
 │                    │               │               │             │
 │                    │               │               │  前端 WS push│
 │                    │               │               │─────────────▶│
 │                    │               │               │             │
 │  REJECTED          │               │               │             │
 │◀───────────────────│               │               │             │
```

---

## 6. 模块接口契约

### 6.1 StrategyService

```python
class StrategyService:
    """策略管理服务"""

    async def list_strategies() -> list[StrategyDTO]:
        """列出所有策略及运行状态"""

    async def get_strategy(strategy_id: str) -> StrategyDTO:
        """获取单个策略详情"""

    async def create_strategy(req: CreateStrategyRequest) -> StrategyDTO:
        """创建策略 (含参数校验)"""

    async def update_strategy(strategy_id: str, params: dict) -> StrategyDTO:
        """更新策略参数 (热更新)"""

    async def delete_strategy(strategy_id: str) -> None:
        """删除策略 (需先停止)"""

    async def start_strategy(strategy_id: str) -> StrategyDTO:
        """启动策略 → 加载到 Agent 编排引擎"""

    async def pause_strategy(strategy_id: str) -> StrategyDTO:
        """暂停策略 (保留持仓)"""

    async def stop_strategy(strategy_id: str) -> StrategyDTO:
        """停止策略 (平仓后停止)"""

    async def confirm_signal(strategy_id: str, signal_id: str) -> TradeResult:
        """半自动模式：用户确认信号"""
```

### 6.2 RiskEngine

```python
class RiskEngine:
    """风控引擎 — 责任链模式"""

    def __init__(self, rules: list[RiskRule]):
        """按 priority 排序规则"""

    def evaluate(signal: TradeSignal, context: RiskContext) -> RiskResult:
        """
        责任链依次执行，首个拒绝即返回

        RiskContext:
            - current_positions: dict[symbol, Position]
            - daily_pnl: float
            - total_equity: float
            - max_drawdown: float
            - circuit_state: dict[dimension, Literal["OPEN","CLOSED","HALF_OPEN"]]

        RiskResult:
            - approved: bool
            - rejected_by: str | None  (规则名称)
            - reason: str
            - adjusted_signal: TradeSignal | None  (风控可调整信号)
        """
```

### 6.3 ExchangeAdapter

```python
class ExchangeAdapter(ABC):
    """交易所抽象适配器"""

    @abstractmethod
    async def get_balance(self) -> dict[str, float]:
        """查询账户余额 {BTC: 0.5, USDT: 10000}"""

    @abstractmethod
    async def get_positions(self) -> list[Position]:
        """查询当前持仓"""

    @abstractmethod
    async def create_order(self, signal: TradeSignal) -> OrderResult:
        """
        下单
        OrderResult: {order_id, symbol, side, quantity, price,
                      status, filled_qty, avg_price, fee}
        """

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """撤单"""

    @abstractmethod
    async def get_order_status(self, order_id: str, symbol: str) -> OrderResult:
        """查询订单状态"""

    @abstractmethod
    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """设置杠杆倍数 (仅合约)"""

    @abstractmethod
    async def get_open_orders(self, symbol: str | None = None) -> list[OrderResult]:
        """查询挂单"""
```

### 6.4 MarketStreamManager

```python
class MarketStreamManager:
    """WebSocket 行情流管理器"""

    async def start(self, symbols: list[str], streams: list[str]):
        """
        启动行情流
        streams: ["kline_1m", "kline_5m", "ticker", "depth20"]
        """

    async def subscribe(self, symbols: list[str]):
        """动态增加订阅 (无需断开连接)"""

    async def unsubscribe(self, symbols: list[str]):
        """动态取消订阅"""

    async def stop(self):
        """优雅关闭所有连接"""

    def on_ticker(self, callback: Callable[[Ticker], None]):
        """注册 ticker 回调 → 写入 Redis"""

    def on_kline(self, callback: Callable[[Kline], None]):
        """注册 K 线回调 → 写入 Redis + TimescaleDB"""
```

### 6.5 存储接口 (Repository)

```python
class TradeRepository:
    async def insert(self, trade: TradeLog) -> TradeLog: ...
    async def find_by_strategy(self, strategy_id: str, limit: int, offset: int) -> list[TradeLog]: ...
    async def find_by_timerange(self, start: datetime, end: datetime) -> list[TradeLog]: ...
    async def export_csv(self, filters: TradeFilter) -> str: ...

class StrategyRepository:
    async def list_all(self) -> list[Strategy]: ...
    async def find_by_id(self, id: str) -> Strategy | None: ...
    async def save(self, strategy: Strategy) -> Strategy: ...
    async def delete(self, id: str) -> None: ...

class KlineRepository:
    async def get_klines(self, symbol: str, interval: str, limit: int) -> list[Kline]: ...
    async def get_ohlcv(self, symbol: str, interval: str, start: datetime, end: datetime) -> list[OHLCV]: ...
    async def insert_batch(self, klines: list[Kline]) -> None: ...

class RiskEventRepository:
    async def insert(self, event: RiskEvent) -> RiskEvent: ...
    async def find_recent(self, limit: int) -> list[RiskEvent]: ...

class AnalysisReportRepository:
    async def insert(self, report: AnalysisReport) -> AnalysisReport: ...
    async def list_all(self, limit: int) -> list[AnalysisReport]: ...
    async def find_by_id(self, id: str) -> AnalysisReport | None: ...
```
