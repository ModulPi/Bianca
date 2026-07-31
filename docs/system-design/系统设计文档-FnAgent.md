# FnAgent — 系统设计文档

> 版本：v1.0 | 日期：2026-07-28 | 基于 PRD v0.2

---

## 目录

1. [系统概述](#1-系统概述)
2. [架构总览](#2-架构总览)
3. [模块详细设计](#3-模块详细设计)
4. [Agent 编排设计](#4-agent-编排设计)
5. [策略引擎设计](#5-策略引擎设计)
6. [风控引擎设计](#6-风控引擎设计)
7. [交易所接口层设计](#7-交易所接口层设计)
8. [数据层设计](#8-数据层设计)
9. [API 契约设计](#9-api-契约设计)
10. [前端架构设计](#10-前端架构设计)
11. [部署架构](#11-部署架构)
12. [安全设计](#12-安全设计)
13. [监控与可观测](#13-监控与可观测)

---

## 1. 系统概述

### 1.1 系统定位

FnAgent 是一个基于 LangGraph 多 Agent 编排的加密货币自动交易系统。核心采用 **Supervisor 模式**，将策略决策、风险控制、订单执行拆分为独立 Agent，通过显式状态图（StateGraph）编排协作。

### 1.2 核心设计原则

| # | 原则 | 实现方式 |
|---|------|----------|
| 1 | **三层分离** | 策略 → 风控 → 执行，每层独立 Agent，风控不可绕过 |
| 2 | **LLM 辅助，非主导** | LLM 仅生成分析建议，不直接下单；交易决策由规则引擎或人工执行 |
| 3 | **模拟先行** | 所有策略必须先通过模拟交易（Demo Mode）24h+ 验证 |
| 4 | **状态可回溯** | LangGraph checkpointer 持久化每次决策状态，完整审计链路 |
| 5 | **配置驱动** | 策略模板 + 风控参数全部外部化，热更新不重启 |

### 1.3 系统边界

```
┌──────────────────────────────────────────────────┐
│                   FnAgent 系统                      │
│                                                    │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│   │ Web 控制台 │  │ API 网关  │  │ 通知服务  │       │
│   └─────┬─────┘  └────┬─────┘  └────┬─────┘       │
│         └──────────────┼─────────────┘             │
│                 ┌──────┴──────┐                    │
│                 │ Agent 编排层 │                    │
│                 └──────┬──────┘                    │
│         ┌──────────────┼──────────────┐            │
│   ┌─────┴─────┐ ┌──────┴──────┐ ┌────┴─────┐      │
│   │ 策略引擎   │ │  风控引擎   │ │ 执行引擎  │      │
│   └───────────┘ └─────────────┘ └──────────┘      │
│                       │                            │
│                 ┌─────┴──────┐                     │
│                 │ 交易所接口层 │                     │
│                 └─────┬──────┘                     │
└───────────────────────┼────────────────────────────┘
                        │ HTTPS / WSS
                   ┌────┴────┐
                   │  币安    │
                   └─────────┘
```

---

## 2. 架构总览

### 2.1 分层架构

```
┌────────────────────────────────────────────────────────────┐
│  展现层 (Presentation)                                       │
│  React 18 + TypeScript + Tailwind CSS + Recharts           │
│  ├─ 仪表盘首页 (Dashboard)                                   │
│  ├─ 策略管理 (Strategy Manager)                              │
│  ├─ 持仓监控 (Position Monitor)                              │
│  ├─ K线面板 (Chart Panel)                                    │
│  ├─ AI 分析 (Analysis View)                                 │
│  └─ 系统设置 (Settings)                                     │
├────────────────────────────────────────────────────────────┤
│  API 网关层 (Gateway)                                        │
│  FastAPI + Uvicorn                                          │
│  ├─ REST:  /api/v1/*                                       │
│  ├─ WS:    /ws/market (行情推送)                             │
│  ├─ WS:    /ws/system (系统事件推送)                          │
│  ├─ 中间件: CORS, 请求日志, 异常处理                           │
│  └─ OpenAPI 自动文档                                         │
├────────────────────────────────────────────────────────────┤
│  服务层 (Services)                                           │
│  ├─ StrategyService  — 策略 CRUD、参数验证、生命周期          │
│  ├─ RiskService      — 风控规则解析、熔断判定                 │
│  ├─ OrderService     — 订单管理、状态跟踪                     │
│  ├─ MarketService    — 行情缓存、K线查询                     │
│  ├─ AnalysisService  — LLM 分析调度、报告生成                 │
│  └─ NotificationSvc  — Telegram/邮件通知                     │
├────────────────────────────────────────────────────────────┤
│  Agent 编排层 (Orchestration) — LangGraph StateGraph        │
│                                                              │
│         ┌──────────────┐                                    │
│         │  Supervisor   │  ← 入口路由 + 状态管理              │
│         │    Agent      │                                    │
│         └──┬───┬───┬───┘                                    │
│      ┌─────┘   │   └─────┐                                   │
│      ▼         ▼         ▼                                   │
│  ┌────────┐┌────────┐┌────────┐┌────────┐                  │
│  │Strategy││Analysis││  Risk  ││Execute │                  │
│  │ Agent  ││ Agent  ││ Agent  ││ Agent  │                  │
│  │(规则)  ││ (LLM)  ││(规则)  ││ (API)  │                  │
│  └────────┘└────────┘└────────┘└────────┘                  │
│       │                              │                      │
│       └──────────────┬───────────────┘                      │
│                      ▼                                      │
│              ┌──────────────┐                               │
│              │  MemorySaver │ ← SQLite checkpoint (PoC)     │
│              └──────────────┘                               │
├────────────────────────────────────────────────────────────┤
│  交易所接口层 (Exchange Adapter)                              │
│  ccxt (统一封装) + python-binance (WebSocket 行情)            │
│  ├─ SpotAdapter     — 现货 REST + WS                        │
│  ├─ FuturesAdapter  — U本位/币本位合约 REST + WS             │
│  └─ RateLimiter     — 全局限流管理                           │
├────────────────────────────────────────────────────────────┤
│  基础设施层 (Infrastructure)                                 │
│  ├─ PostgreSQL 16 + TimescaleDB 2.x  — 交易记录、K线、配置   │
│  ├─ Redis 7                           — 行情缓存、会话、锁   │
│  ├─ Ollama                            — 本地 LLM (qwen2.5)  │
│  └─ Docker Compose                    — 一键部署             │
└────────────────────────────────────────────────────────────┘
```

### 2.2 项目目录结构

```
FnAgent/
├── docker-compose.yml              # 一键部署编排
├── Dockerfile.api                  # API 服务镜像
├── Dockerfile.frontend             # 前端镜像
├── .env.example                    # 环境变量模板
├── LICENSE                         # MIT
├── README.md
│
├── agent/                          # Agent 编排核心 (Python)
│   ├── __init__.py
│   ├── main.py                     # FastAPI 入口 + uvicorn
│   ├── config.py                   # pydantic-settings 配置
│   ├── graph/                      # LangGraph 图定义
│   │   ├── __init__.py
│   │   ├── supervisor.py           # Supervisor Agent (路由)
│   │   ├── strategy_agent.py       # 策略 Agent
│   │   ├── analysis_agent.py       # LLM 分析 Agent
│   │   ├── risk_agent.py           # 风控 Agent
│   │   └── execute_agent.py        # 执行 Agent
│   ├── strategy/                   # 策略模板
│   │   ├── __init__.py
│   │   ├── base.py                 # 策略抽象基类
│   │   ├── grid.py                 # 网格交易策略
│   │   ├── dca.py                  # 定投策略
│   │   └── trend.py                # 趋势跟踪策略
│   ├── risk/                       # 风控规则
│   │   ├── __init__.py
│   │   ├── engine.py               # 风控引擎 (责任链)
│   │   ├── rules.py                # 风控规则实现
│   │   └── circuit_breaker.py      # 熔断器
│   ├── exchange/                   # 交易所适配层
│   │   ├── __init__.py
│   │   ├── adapter.py              # 抽象适配器
│   │   ├── spot.py                 # 现货适配器
│   │   ├── futures.py              # 合约适配器
│   │   ├── market_stream.py        # WebSocket 行情流
│   │   └── rate_limiter.py         # 限流器
│   ├── llm/                        # LLM 模块
│   │   ├── __init__.py
│   │   ├── analyzer.py             # 市场分析器
│   │   └── prompts.py              # 提示词模板
│   ├── api/                        # FastAPI 路由
│   │   ├── __init__.py
│   │   ├── routes.py               # REST 路由
│   │   ├── schemas.py              # Pydantic 模型
│   │   └── websocket.py            # WebSocket 端点
│   ├── storage/                    # 数据访问层
│   │   ├── __init__.py
│   │   ├── models.py               # SQLAlchemy ORM
│   │   ├── repository.py           # 仓储模式
│   │   └── migrations/             # Alembic 迁移
│   └── notification/               # 通知模块
│       ├── __init__.py
│       ├── telegram.py
│       └── email.py
│
├── frontend/                       # React 前端 (TypeScript)
│   ├── src/
│   │   ├── components/             # UI 组件
│   │   ├── pages/                  # 页面
│   │   ├── hooks/                  # 自定义 Hooks
│   │   ├── stores/                 # Zustand 状态管理
│   │   ├── api/                    # API 调用层
│   │   └── utils/                  # 工具函数
│   ├── package.json
│   └── vite.config.ts
│
├── tests/                          # 测试
│   ├── test_strategy.py
│   ├── test_risk.py
│   ├── test_exchange.py
│   └── test_graph.py
│
└── docs/                           # 文档
    ├── PRD-FnAgent.md
    ├── 用户故事-FnAgent.md
    └── system-design/              # 系统设计文档
```

---

## 3. 模块详细设计

### 3.1 模块依赖图

```
main.py (入口)
  └── config.py (配置)
  └── api/routes.py
        ├── StrategyService
        │     ├── strategy/* (策略模板)
        │     └── storage/repository.py
        ├── MarketService
        │     ├── exchange/market_stream.py
        │     └── storage/repository.py
        ├── OrderService
        │     └── exchange/*
        ├── RiskService
        │     └── risk/engine.py
        ├── AnalysisService
        │     └── llm/analyzer.py
        └── graph/supervisor.py (Agent 编排入口)
              ├── graph/strategy_agent.py
              ├── graph/analysis_agent.py
              ├── graph/risk_agent.py
              └── graph/execute_agent.py
```

### 3.2 模块职责

| 模块 | 职责 | 关键技术 |
|------|------|----------|
| `graph/supervisor.py` | LangGraph 入口，路由决策，状态管理 | StateGraph, MemorySaver |
| `graph/strategy_agent.py` | 加载策略模板，生成交易信号 | 规则引擎，参数化策略 |
| `graph/analysis_agent.py` | 调用 LLM 分析市场，生成建议报告 | Ollama, LangChain |
| `graph/risk_agent.py` | 执行风控规则，熔断判定 | 责任链模式 |
| `graph/execute_agent.py` | 将信号转为订单，调用交易所 API | ccxt |
| `exchange/` | 交易所 API 抽象，现货/合约适配 | ccxt + python-binance |
| `strategy/` | 策略模板实现（网格、DCA、趋势） | ABC 基类，插件化 |
| `risk/` | 风控规则 + 熔断器 | 责任链，状态机 |
| `llm/` | LLM 分析调度，提示词管理 | Ollama API, Prompt Template |
| `storage/` | 数据持久化，仓储模式 | SQLAlchemy, Alembic |
| `api/` | REST + WebSocket 端点 | FastAPI, asyncio |
| `frontend/` | Web 控制台 | React 18, Vite, Zustand |

---

## 4. Agent 编排设计

### 4.1 为什么选 Supervisor 模式

| 考量 | Supervisor 模式 | Swarm 模式 |
|------|----------------|------------|
| 安全性（风控不可绕过） | ✅ 中心化路由可控 | ❌ Agent 间自由通信 |
| 审计性（每步可追溯） | ✅ 所有消息经过中心 | ❌ 需额外日志 |
| 交易场景匹配度 | ✅ 星型拓扑天然 = 策略→风控→执行 | ❌ 适合开放式对话 |
| Token 开销 | 高 20-35% | 低 |
| 延迟 | 高 30-50% | 低 |

**结论：** 交易场景安全性优先，Supervisor 模式是最佳选择。Token 额外开销在交易场景中可接受（非高频实时对话）。

### 4.2 StateGraph 设计

```python
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from typing import TypedDict, Literal, Annotated
from operator import add

# ──── 状态定义 ────
class TradeState(TypedDict):
    # 用户输入
    user_message: str

    # 策略上下文
    active_strategy_id: str | None
    strategy_config: dict          # 策略参数
    execution_mode: Literal["auto", "semi_auto"]

    # Agent 协作消息 (add reducer 实现累加)
    messages: Annotated[list, add]

    # 各 Agent 输出
    strategy_signal: dict | None   # 策略生成的交易信号
    analysis_report: dict | None   # LLM 分析报告
    risk_decision: dict | None     # 风控审核结果 {approved: bool, reason: str}
    order_result: dict | None      # 下单结果

    # 路由
    next_agent: str | None
```

### 4.3 图拓扑

```
                 ┌─────────┐
                 │  START   │
                 └────┬─────┘
                      ▼
              ┌───────────────┐
              │  Supervisor   │ ← 入口，解析意图，路由
              └───┬───┬───┬───┘
                  │   │   │
        ┌─────────┘   │   └─────────┐
        ▼             ▼             ▼
┌──────────────┐ ┌──────────┐ ┌──────────┐
│Strategy Agent│ │Analysis  │ │ Execute  │
│ (全自动模式)  │ │ Agent    │ │ Agent    │
│              │ │ (LLM分析)│ │ (手动确认)│
└──────┬───────┘ └────┬─────┘ └────┬─────┘
       │              │            │
       └──────────────┼────────────┘
                      ▼
              ┌───────────────┐
              │  Risk Agent   │ ← 必经节点，不可跳过
              └───────┬───────┘
                      │
              ┌───────┴───────┐
              │ risk_decision  │
              │   .approved?   │
              └───┬───────┬───┘
          approved │       │ rejected
              ┌────┘       └──────────┐
              ▼                       ▼
      ┌──────────────┐       ┌──────────────┐
      │Execute Agent  │       │  通知 + 日志   │
      │   (下单)      │       │  (不执行)     │
      └──────┬───────┘       └──────┬───────┘
             │                      │
             └──────────┬───────────┘
                        ▼
                    ┌──────┐
                    │ END  │
                    └──────┘
```

### 4.4 图构建代码结构

```python
def build_trading_graph() -> StateGraph:
    workflow = StateGraph(TradeState)

    # 添加节点
    workflow.add_node("supervisor", supervisor_node)
    workflow.add_node("strategy", strategy_agent_node)
    workflow.add_node("analysis", analysis_agent_node)
    workflow.add_node("risk", risk_agent_node)       # 不可绕过
    workflow.add_node("execute", execute_agent_node)

    # 入口 → Supervisor
    workflow.set_entry_point("supervisor")

    # Supervisor → 条件路由
    workflow.add_conditional_edges(
        "supervisor",
        route_intent,  # 根据 user_message 判断：strategy | analysis | execute
        {"strategy": "strategy", "analysis": "analysis", "execute": "execute"}
    )

    # 策略/分析输出 → 风控（必经）
    workflow.add_edge("strategy", "risk")
    workflow.add_edge("analysis", "risk")

    # 风控 → 条件路由
    workflow.add_conditional_edges(
        "risk",
        lambda s: "execute" if s["risk_decision"]["approved"] else END,
        {"execute": "execute", END: END}
    )

    # 执行 → 回 Supervisor（多轮循环）
    workflow.add_edge("execute", "supervisor")

    # 持久化
    checkpointer = MemorySaver()  # PoC 用内存；MVP 切 SQLite
    return workflow.compile(checkpointer=checkpointer)
```

---

## 5. 策略引擎设计

### 5.1 策略抽象基类

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

@dataclass
class TradeSignal:
    """策略输出的交易信号"""
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: float
    price: float | None          # None = 市价单
    order_type: Literal["MARKET", "LIMIT"]
    strategy_name: str
    reason: str                  # 决策理由（审计用）
    confidence: float            # 0.0 ~ 1.0

class BaseStrategy(ABC):
    """策略模板抽象基类"""

    strategy_id: str
    name: str
    version: str
    supported_markets: list[Literal["spot", "futures_u", "futures_coin"]]

    @abstractmethod
    def validate_params(self, params: dict) -> bool:
        """校验用户输入的策略参数"""
        ...

    @abstractmethod
    def analyze(self, market_data: dict, position: dict) -> list[TradeSignal]:
        """
        核心决策逻辑
        Args:
            market_data: {symbol: {price, klines, orderbook, ...}}
            position: 当前持仓信息
        Returns:
            交易信号列表（可能为空 = 不操作）
        """
        ...

    def get_default_params(self) -> dict:
        """返回默认参数（保守型默认值）"""
        return {}
```

### 5.2 策略模板

#### 网格交易 (GridStrategy)

```
参数:
  - upper_price: 网格上限
  - lower_price: 网格下限
  - grid_count: 网格层数 (默认 10)
  - invest_amount: 每格投入金额 (USDT)

逻辑:
  price > upper → 停止开仓
  price < lower → 停止开仓
  price 在区间内 → 按网格价差自动低买高卖
```

#### 定投 DCA (DCAStrategy)

```
参数:
  - symbol: 交易对
  - invest_amount: 每次投入金额 (USDT)
  - interval_hours: 定投间隔 (小时)

逻辑:
  每隔 interval_hours → 市价买入 invest_amount 的 symbol
```

#### 趋势跟踪 (TrendStrategy)

```
参数:
  - symbol: 交易对
  - fast_ma: 快线周期 (默认 7)
  - slow_ma: 慢线周期 (默认 25)
  - position_ratio: 每次仓位比例 (默认 20%)

逻辑:
  fast_ma 上穿 slow_ma → 买入 (金叉)
  fast_ma 下穿 slow_ma → 卖出 (死叉)
```

### 5.3 策略执行模式

```
【全自动模式 (auto)】
  Strategy Agent 生成信号 → 直接进入 Risk Agent → 风控通过 → 自动下单

【半自动模式 (semi_auto)】
  Strategy Agent 生成信号 → 推送到前端，等待用户确认
    → 用户点击"确认执行" → 进入 Risk Agent → 下单
    → 用户点击"拒绝" → 丢弃信号，记录日志
    → 超时未响应 (默认 30min) → 自动丢弃
```

---

## 6. 风控引擎设计

### 6.1 责任链模式

风控引擎采用**责任链（Chain of Responsibility）**模式，每条规则独立实现，按优先级依次检查。任一规则拒绝，后续跳过。

```python
class RiskRule(ABC):
    """风控规则抽象基类"""
    priority: int           # 越小越先执行
    name: str

    @abstractmethod
    def check(self, signal: TradeSignal, context: RiskContext) -> RiskResult:
        ...

class RiskEngine:
    def __init__(self, rules: list[RiskRule]):
        self.rules = sorted(rules, key=lambda r: r.priority)

    def evaluate(self, signal: TradeSignal, context: RiskContext) -> RiskResult:
        for rule in self.rules:
            result = rule.check(signal, context)
            if not result.approved:
                return result  # 短路：第一个拒绝即返回
        return RiskResult(approved=True, reason="all passed")
```

### 6.2 风控规则列表

| 优先级 | 规则名称 | 逻辑 | 类型 |
|--------|----------|------|------|
| 1 | **止损检查** | 当前持仓浮亏 ≥ 止损线 → 拒绝新开仓 | 硬限制 |
| 2 | **日亏损熔断** | 当日累计亏损 ≥ 日限额 → 拒绝所有交易 | 熔断 |
| 3 | **回撤熔断** | 总资金回撤 ≥ 最大回撤线 → 拒绝所有交易 | 熔断 |
| 4 | **单币种仓位上限** | 新开仓后该币种占比 > 上限 → 拒绝 | 硬限制 |
| 5 | **总持仓数上限** | 当前持仓币种数 ≥ 上限 → 拒绝新开仓 | 硬限制 |
| 6 | **杠杆上限**（仅合约） | 设置杠杆 > 最大杠杆 → 拒绝 | 硬限制 |
| 7 | **最小交易额** | 订单金额 < 币安最小交易额 → 调整或拒绝 | 提示 |
| 8 | **信号频率限制** | 同一策略 N 秒内重复信号 → 去重 | 去重 |

### 6.3 熔断器状态机

```
          ┌──────────┐
          │  CLOSED   │ ← 正常状态，所有交易通过
          └─────┬─────┘
                │ 触发条件 (日亏损 / 回撤超限)
                ▼
          ┌──────────┐
          │   OPEN    │ ← 熔断状态，拒绝所有新开仓
          └─────┬─────┘
                │ 冷却时间到 (默认 24h) 或 手动重置
                ▼
          ┌──────────┐
          │ HALF_OPEN │ ← 允许小额试探交易
          └─────┬─────┘
                │ 试探成功 → CLOSED
                │ 试探失败 → OPEN
                ▼
```

---

## 7. 交易所接口层设计

### 7.1 适配器模式

```python
class ExchangeAdapter(ABC):
    """交易所抽象适配器 — 后期扩展多交易所只需新增实现"""

    @abstractmethod
    async def get_balance(self) -> dict: ...

    @abstractmethod
    async def get_positions(self) -> list[dict]: ...

    @abstractmethod
    async def create_order(self, signal: TradeSignal) -> OrderResult: ...

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> bool: ...

    @abstractmethod
    async def get_order_status(self, order_id: str, symbol: str) -> dict: ...

    @abstractmethod
    async def set_leverage(self, symbol: str, leverage: int) -> bool: ...

    @abstractmethod
    async def subscribe_market(self, symbols: list[str]) -> AsyncIterator: ...
```

### 7.2 ccxt 封装策略

```python
# 现货实例
spot = ccxt.binance({
    'apiKey': settings.binance_api_key,
    'secret': settings.binance_api_secret,
    'options': {'defaultType': 'spot'},
    'enableRateLimit': True,
})

# U本位合约实例
futures = ccxt.binance({
    'apiKey': settings.binance_futures_api_key,
    'secret': settings.binance_futures_api_secret,
    'options': {'defaultType': 'future'},
    'enableRateLimit': True,
})
```

### 7.3 WebSocket 行情流设计

```
MarketStreamManager (asyncio 事件循环)
  ├─ 多路复用连接 (1个WS连接订阅多交易对)
  │   wss://stream.binance.com:9443/ws
  │   订阅: btcusdt@kline_1m/ethusdt@kline_1m/...
  │
  ├─ 自动重连 (指数退避: 1s → 2s → 4s → 8s → max 60s)
  ├─ 心跳保活 (ping/pong 3min 间隔)
  ├─ 写入 Redis (最新行情缓存，ZSET 按时间排序)
  └─ 通过 API WebSocket 推送到前端
```

### 7.4 限流管理

币安 API 权重限制：

| API 类别 | 限制 | FnAgent 策略 |
|----------|------|-------------|
| 现货 REST | 1200 权重/分钟 | Token Bucket 限流器 |
| 合约 REST | 2400 权重/分钟 | 独立 Bucket |
| WebSocket 连接 | 最多 1024 流/连接 | 复用连接 + 计数 |
| 下单 | 50 次/10秒 | 订单队列 + 去重 |

---

## 8. 数据层设计

### 8.1 核心数据模型 (ER)

```
┌──────────────┐       ┌──────────────────┐
│  strategies   │       │   trade_logs      │
├──────────────┤       ├──────────────────┤
│ id (PK)      │──┐    │ id (PK)           │
│ name         │  │    │ strategy_id (FK)  │──┐
│ type         │  │    │ symbol            │  │
│ market       │  │    │ side              │  │
│ params (JSON)│  │    │ quantity          │  │
│ status       │  │    │ price             │  │
│ exec_mode    │  │    │ order_type        │  │
│ created_at   │  │    │ execution_mode    │  │
└──────────────┘  │    │ risk_decision     │  │
                  │    │ decision_reason   │  │
┌──────────────┐  │    │ order_id (ext)    │  │
│  positions    │  │    │ status            │  │
├──────────────┤  │    │ created_at        │  │
│ id (PK)      │  │    └──────────────────┘  │
│ symbol       │  │                          │
│ quantity     │  │    ┌──────────────────┐  │
│ entry_price  │  │    │  risk_events      │  │
│ current_price│  │    ├──────────────────┤  │
│ pnl          │  │    │ id (PK)           │  │
│ strategy_id  │──┘    │ event_type        │  │
│ updated_at   │       │ detail (JSON)     │  │
└──────────────┘       │ created_at        │  │
                       └──────────────────┘  │
                                             │
┌──────────────────────┐                    │
│  analysis_reports     │                    │
├──────────────────────┤                    │
│ id (PK)              │                    │
│ content (TEXT)        │                    │
│ suggestions (JSON)    │                    │
│ confidence           │                    │
│ model_used           │                    │
│ created_at           │                    │
└──────────────────────┘                    │
                                            │
┌──────────────────────┐                    │
│  klines (TimescaleDB Hypertable)          │
├──────────────────────┤                    │
│ time (PK, 分区键)     │                    │
│ symbol (segmentby)   │                    │
│ interval             │                    │
│ open/high/low/close  │                    │
│ volume               │                    │
└──────────────────────┘
```

### 8.2 TimescaleDB 超表设计

```sql
-- 创建 K 线超表
CREATE TABLE klines (
    time        TIMESTAMPTZ   NOT NULL,
    symbol      TEXT          NOT NULL,
    interval    TEXT          NOT NULL,  -- '1m', '5m', '15m', '1h', '4h', '1d'
    open        DOUBLE PRECISION,
    high        DOUBLE PRECISION,
    low         DOUBLE PRECISION,
    close       DOUBLE PRECISION,
    volume      DOUBLE PRECISION,
    trades      INTEGER,
    PRIMARY KEY (time, symbol, interval)
);

SELECT create_hypertable('klines', 'time');

-- 按交易对分段 + 按时间排序（优化 OHLCV 聚合查询）
ALTER TABLE klines SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol, interval',
    timescaledb.compress_orderby = 'time DESC'
);

-- 7 天后的数据自动压缩（压缩率预计 10-20x）
SELECT add_compression_policy('klines', INTERVAL '7 days');

-- 连续聚合：预计算 1 小时 K 线
CREATE MATERIALIZED VIEW klines_1h
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS bucket,
    symbol,
    FIRST(open, time) AS open,
    MAX(high) AS high,
    MIN(low) AS low,
    LAST(close, time) AS close,
    SUM(volume) AS volume
FROM klines
WHERE interval = '1m'
GROUP BY bucket, symbol;

-- 自动刷新策略
SELECT add_continuous_aggregate_policy('klines_1h',
    start_offset => INTERVAL '2 hours',
    end_offset => INTERVAL '1 minute',
    schedule_interval => INTERVAL '5 minutes'
);
```

### 8.3 Redis 缓存设计

| Key 模式 | 类型 | 用途 | TTL |
|----------|------|------|-----|
| `market:ticker:{symbol}` | String (JSON) | 最新 ticker | 实时更新 |
| `market:depth:{symbol}` | String (JSON) | 最新深度 | 实时更新 |
| `market:klines:{symbol}:{interval}` | ZSET | 最近 500 根 K 线 | 持久 |
| `strategy:state:{strategy_id}` | String (JSON) | 策略运行状态 | 持久 |
| `order:pending:{order_id}` | String (JSON) | 挂单状态 | 5 min |
| `risk:circuit:{dimension}` | String | 熔断状态 OPEN/CLOSED | 24h |
| `ws:session:{client_id}` | String | WebSocket 会话 | 连接期间 |

---

## 9. API 契约设计

### 9.1 REST API

```
Base URL: http://localhost:8000/api/v1

# ──── 系统 ────
GET    /health                          # 健康检查 + Ollama 可达性

# ──── 策略管理 ────
GET    /strategies                      # 列出所有策略 + 运行状态
POST   /strategies                      # 创建新策略
GET    /strategies/{id}                 # 策略详情
PUT    /strategies/{id}                 # 更新策略参数
DELETE /strategies/{id}                 # 删除策略
PUT    /strategies/{id}/start           # 启动策略
PUT    /strategies/{id}/pause           # 暂停策略
PUT    /strategies/{id}/stop            # 停止策略（平仓后停止）

# ──── 持仓与账户 ────
GET    /account/balance                 # 账户余额（现货+合约）
GET    /positions                       # 当前持仓列表
GET    /positions/{symbol}              # 单币种持仓详情

# ──── 交易记录 ────
GET    /trades                          # 交易记录（支持筛选/分页）
GET    /trades/{id}                     # 单笔交易详情
GET    /trades/export?format=csv        # 导出 CSV

# ──── 行情 ────
GET    /market/ticker?symbols=BTC,ETH   # 最新行情
GET    /market/klines?symbol=BTC&interval=1h&limit=100  # K 线数据

# ──── 风控 ────
GET    /risk/status                     # 风控状态（熔断器、日亏损）
PUT    /risk/reset                      # 手动重置熔断器
GET    /risk/events                     # 风控事件列表

# ──── AI 分析 ────
POST   /analysis/generate               # 手动触发分析报告
GET    /analysis/reports                # 历史报告列表
GET    /analysis/reports/{id}           # 报告详情

# ──── 通知 ────
PUT    /settings/notifications          # 配置通知渠道
POST   /settings/notifications/test     # 测试通知
```

### 9.2 关键 API Schema

```python
# POST /strategies
class CreateStrategyRequest(BaseModel):
    name: str
    type: Literal["grid", "dca", "trend"]
    market: Literal["spot", "futures_u", "futures_coin"]
    execution_mode: Literal["auto", "semi_auto"]
    params: dict                     # 策略参数（依 type 不同）

class CreateStrategyResponse(BaseModel):
    id: str
    name: str
    status: Literal["created", "running", "paused", "stopped"]
    execution_mode: str
    created_at: datetime

# PUT /strategies/{id}/start
class StartStrategyResponse(BaseModel):
    id: str
    status: str
    started_at: datetime
    message: str

# GET /trades
class TradeRecord(BaseModel):
    id: str
    strategy_id: str
    strategy_name: str
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: float
    price: float
    order_type: Literal["MARKET", "LIMIT"]
    execution_mode: str
    risk_decision: str
    decision_reason: str
    status: Literal["submitted", "partial", "filled", "canceled", "failed"]
    created_at: datetime
```

### 9.3 WebSocket 协议

```
# 行情推送 (客户端订阅)
ws://localhost:8000/ws/market
→ 客户端发送: {"action": "subscribe", "symbols": ["BTCUSDT", "ETHUSDT"]}
← 服务端推送: {"type": "ticker", "symbol": "BTCUSDT", "price": 65432.10, "change_24h": 2.3}

# 系统事件推送
ws://localhost:8000/ws/system
← 服务端推送: {"type": "trade_executed", "data": {...}}
← 服务端推送: {"type": "risk_triggered", "data": {"rule": "daily_loss", "detail": "..."}}
← 服务端推送: {"type": "strategy_status", "data": {"strategy_id": "...", "status": "paused"}}
← 服务端推送: {"type": "confirmation_required", "data": {"signal": {...}}}
```

---

## 10. 前端架构设计

### 10.1 技术选型

| 层次 | 选型 | 理由 |
|------|------|------|
| 框架 | React 18 + TypeScript | C 端主流，生态丰富 |
| 构建 | Vite | 快速开发，HMR |
| 样式 | Tailwind CSS | 高效，响应式方便 |
| 图表 | Recharts + lightweight-charts | Recharts 做报表，TradingView 轻量版做 K 线 |
| 状态管理 | Zustand | 轻量，API 简洁，适合中等复杂度 |
| HTTP | axios | 拦截器，错误处理 |
| WebSocket | 原生 WebSocket + 自动重连 Hook | 轻量 |

### 10.2 页面结构

```
/                       仪表盘 (总资产、收益曲线、策略状态一览)
/strategies             策略列表 + 创建/编辑
/strategies/:id         策略详情 (参数、运行状态、历史信号)
/positions              持仓监控 (当前持仓、盈亏、止损状态)
/trades                 交易记录 (筛选、列表、导出)
/analysis               AI 分析报告 (列表 + 详情)
/settings               设置 (API Key、风控参数、通知)
```

### 10.3 组件树 (核心页面)

```
App
├── Layout
│   ├── Sidebar (导航)
│   └── Header  (状态指示器: 连接状态、熔断状态)
│
├── DashboardPage
│   ├── AssetOverview      (总资产、今日盈亏、收益率)
│   ├── PnLCurve           (收益曲线图)
│   ├── StrategyStatusList (策略运行状态卡片)
│   └── RecentTrades       (最近交易列表)
│
├── StrategyDetailPage
│   ├── StrategyConfigForm  (参数配置 + 实时校验)
│   ├── ExecutionModeToggle (全自动/半自动切换)
│   ├── StrategyControls    (启动/暂停/停止按钮)
│   ├── SignalLog           (信号历史)
│   └── ConfirmationDialog  (半自动模式确认弹窗)
│
├── PositionPage
│   ├── PositionTable       (持仓列表)
│   └── KLineChart          (TradingView K线图)
│
├── TradeHistoryPage
│   ├── TradeFilter         (筛选器: 时间/策略/交易对)
│   ├── TradeTable          (交易列表)
│   └── ExportButton        (导出 CSV)
│
└── AnalysisPage
    ├── ReportList          (报告列表)
    └── ReportDetail        (报告详情 + 置信度)
```

---

## 11. 部署架构

### 11.1 Docker Compose 拓扑

```yaml
# docker-compose.yml
services:
  api:
    build: .
    ports: ["8000:8000"]
    env_file: .env
    depends_on: [db, redis]
    restart: unless-stopped

  frontend:
    build: ./frontend
    ports: ["3000:80"]
    depends_on: [api]
    restart: unless-stopped

  db:
    image: timescale/timescaledb:latest-pg16
    environment:
      POSTGRES_DB: fnagent
      POSTGRES_USER: fnagent
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports: ["5432:5432"]
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    volumes:
      - redisdata:/data
    restart: unless-stopped

  ollama:
    image: ollama/ollama:latest
    ports: ["11434:11434"]
    volumes:
      - ollamadata:/root/.ollama
    restart: unless-stopped

volumes:
  pgdata:
  redisdata:
  ollamadata:
```

### 11.2 环境变量 (.env)

```bash
# 币安 API
BINANCE_API_KEY=your_api_key
BINANCE_API_SECRET=your_api_secret

# 运行模式: paper (模拟) / live (实盘)
TRADE_MODE=paper
PAPER_BASE_URL=https://demo-api.binance.com

# 数据库
DATABASE_URL=postgresql+asyncpg://fnagent:password@db:5432/fnagent
REDIS_URL=redis://redis:6379/0

# LLM
LLM_PROVIDER=ollama            # ollama | openai
LLM_MODEL=qwen2.5:7b
LLM_BASE_URL=http://ollama:11434

# 通知
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# 系统
LOG_LEVEL=INFO
API_PORT=8000
```

---

## 12. 安全设计

| 层面 | 措施 | 优先级 |
|------|------|--------|
| **API Key 存储** | AES-256-GCM 加密，密钥从环境变量注入 | P0 |
| **通信加密** | 全量 HTTPS/WSS，TLS 1.3 | P0 |
| **API Key 权限** | 检测并告警提现权限；建议用户仅开放交易+读取 | P0 |
| **风控不可绕过** | 风控 Agent 在图拓扑中是必经节点，无旁路 | P0 |
| **输入校验** | 所有 API 参数 Pydantic 严格校验 | P0 |
| **SQL 注入** | SQLAlchemy ORM 参数化查询 | P0 |
| **日志脱敏** | API Key、密码等敏感信息自动脱敏 | P1 |
| **IP 白名单** | 建议用户在币安后台绑定 IP | P1 |
| **CORS** | 仅允许本地 localhost 来源（Docker 部署时） | P1 |

---

## 13. 监控与可观测

### 13.1 日志规范

```python
# 结构化日志格式
logger.info("trade_executed", extra={
    "strategy_id": "s_001",
    "symbol": "BTCUSDT",
    "side": "BUY",
    "quantity": 0.01,
    "price": 65432.10,
    "order_id": "binance_order_123",
    "decision_reason": "网格策略第3层买入信号",
    "risk_approved": True,
    "execution_mode": "auto"
})
```

### 13.2 关键指标

| 指标 | 采集方式 | 告警阈值 |
|------|----------|----------|
| WebSocket 连接状态 | 心跳监控 | 断连 > 30s |
| API 限流剩余 | 响应头解析 | 剩余 < 10% |
| 订单延迟 | 下单到成交时间差 | > 5s |
| 日亏损 | 交易日志聚合 | ≥ 日限额的 80% |
| 策略异常退出 | 进程监控 | 任何异常退出 |
| 数据库连接池 | SQLAlchemy pool 事件 | 耗尽 |
| Ollama 可达性 | /health 定时检查 | 不可达 > 5min |

---

## 附录 A：技术选型汇总

| 层 | 技术 | 版本 |
|----|------|------|
| Agent 编排 | LangGraph | ≥ 0.2 |
| LLM 框架 | LangChain + langchain-ollama | latest |
| Web 框架 | FastAPI + Uvicorn | ≥ 0.110 |
| 交易所 API | ccxt + python-binance | ≥ 4.x |
| 数据库 | PostgreSQL + TimescaleDB | 16 + 2.x |
| 缓存 | Redis | 7 |
| ORM | SQLAlchemy 2.0 (async) | ≥ 2.0 |
| 迁移 | Alembic | latest |
| 配置 | pydantic-settings | ≥ 2.x |
| 前端 | React + TypeScript + Vite | 18 + 5.x |
| 图表 | Recharts + lightweight-charts | latest |
| 样式 | Tailwind CSS | 3.x |
| 部署 | Docker Compose | v2 |
| 测试 | pytest + pytest-asyncio | ≥ 8.x |

## 附录 B：PoC vs MVP 功能边界

| 模块 | PoC (M0–M2) | MVP (M3–M4) |
|------|-------------|-------------|
| 交易所 | 现货 API + 模拟交易 | + 合约 API |
| 策略 | 1 个策略 (网格) | 3 个策略 (网格+DCA+趋势) |
| 风控 | 止损 + 日亏损 | 完整 8 条规则 |
| LLM | 无 | Ollama 分析报告 |
| 前端 | 无 (CLI / curl) | Web 完整控制台 |
| 通知 | 日志 only | Telegram Bot |
| 持久化 | MemorySaver | SQLite checkpointer |
