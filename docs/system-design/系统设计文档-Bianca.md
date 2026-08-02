# Bianca — 系统设计文档

> 版本：v0.4 | 日期：2026-07-31 | 基于 PRD v0.4

---

## 1. 系统概述

Bianca 是基于 LangGraph 的加密货币自动交易 Agent。PoC 阶段聚焦：**LLM 自主决策 + Demo 现货 + 最小风控 + SQLite 轻量部署**。

### 1.1 核心设计原则

| # | 原则 | PoC 实现 |
|---|------|----------|
| 1 | **LLM 自主决策** | Analysis Agent 产出 BUY/SELL/HOLD |
| 2 | **风控不可绕过** | Risk Agent 是 Execute 唯一前置 |
| 3 | **可配置自动执行** | `LLM_AUTO_EXECUTE` 环境变量 |
| 4 | **状态可回溯** | LangGraph SqliteSaver |
| 5 | **最简部署** | Docker 跑 API；LLM 默认 DeepSeek API |
| 6 | **LLM 可切换** | `LLM_PROVIDER` 配置切换 deepseek / ollama |

---

## 2. PoC 架构

### 2.1 分层

```
CLI/curl → FastAPI (127.0.0.1:8000)
         → Agent Runner (asyncio 定时循环, 默认 5min)
         → LangGraph StateGraph
              Supervisor → Analysis(LLM) → Risk(2 rules) → Execute(ccxt Demo)
         → SQLite (trade_logs, decision_logs, risk_events)
         → 内存行情缓存
```

### 2.2 项目目录（PoC）

```
Bianca/
├── docker-compose.yml          # 仅 api 服务
├── Dockerfile.api
├── .env.example
├── data/                       # SQLite + checkpoints（gitignore）
├── agent/
│   ├── main.py                 # FastAPI 入口
│   ├── config.py               # pydantic-settings
│   ├── runner.py               # Agent 定时循环
│   ├── graph/
│   │   ├── supervisor.py
│   │   ├── analysis_agent.py   # LLM 自主决策
│   │   ├── risk_agent.py
│   │   └── execute_agent.py
│   ├── exchange/
│   │   ├── spot_demo.py        # ccxt Demo 现货
│   │   └── market_stream.py
│   ├── llm/
│   │   ├── provider.py         # LLM 提供商抽象（deepseek / ollama）
│   │   ├── analyzer.py
│   │   └── prompts.py
│   ├── risk/
│   │   ├── engine.py
│   │   └── rules.py            # 2 条规则
│   ├── api/
│   │   ├── routes.py
│   │   └── schemas.py
│   └── storage/
│       ├── models.py
│       └── repository.py
├── tests/
└── docs/
```

### 2.3 TradeState（PoC 简化）

```python
class TradeState(TypedDict):
    market_data: dict              # 最新行情
    llm_signal: dict | None        # {action, symbol, amount, confidence, reason}
    risk_decision: dict | None     # {approved, reason}
    order_result: dict | None
    llm_auto_execute: bool
```

---

## 3. 模块设计（PoC）

| 模块 | 职责 |
|------|------|
| `runner.py` | 定时触发 LangGraph；控制启停 |
| `graph/analysis_agent.py` | 调用 LLM 提供商，解析 BUY/SELL/HOLD |
| `graph/risk_agent.py` | 单笔上限 + 日亏损检查 |
| `graph/execute_agent.py` | ccxt Demo 现货市价单 |
| `exchange/spot_demo.py` | Demo API 封装 |
| `llm/provider.py` | 根据 `LLM_PROVIDER` 路由至 DeepSeek 或 Ollama |
| `llm/analyzer.py` | OpenAI 兼容 API 客户端（DeepSeek / Ollama 共用） |
| `risk/rules.py` | MaxTradeAmountRule, DailyLossRule |

---

## 4. API 契约（PoC）

```
Base: http://127.0.0.1:8000/api/v1

GET    /health                    # 健康检查 + 当前 LLM 提供商可达性
POST   /agent/start               # 启动 Agent 循环
POST   /agent/stop                # 停止
GET    /agent/status              # {running, last_tick, daily_pnl}
GET    /trades                    # 交易记录列表
GET    /trades/{id}               # 单笔详情
GET    /decisions                 # LLM 决策日志
GET    /usage                     # Token 消耗汇总（today + total）
GET    /risk/events               # 风控事件
```

MVP 新增 `/summary/*` 会话汇总，见《汇总管理模块设计-Bianca.md》。

---

## 5. 配置（.env.example）

### 5.1 LLM 提供商切换

通过 `LLM_PROVIDER` 切换，**无需改代码**，重启 API 生效。

**默认：DeepSeek API（PoC 推荐）**

```bash
LLM_PROVIDER=deepseek
LLM_API_KEY=sk-your-deepseek-key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
LLM_AUTO_EXECUTE=true
```

**切换：本地 Ollama（后期）**

```bash
LLM_PROVIDER=ollama
LLM_API_KEY=                    # Ollama 无需 Key，留空即可
LLM_BASE_URL=http://host.docker.internal:11434
LLM_MODEL=qwen2.5:7b
LLM_AUTO_EXECUTE=true
```

> DeepSeek 与 Ollama 均通过 **OpenAI 兼容 API** 调用（`/v1/chat/completions`），`llm/provider.py` 统一封装。

### 5.2 完整 .env.example

```bash
# 币安 Demo 现货
BINANCE_API_KEY=
BINANCE_API_SECRET=
BINANCE_DEMO_BASE_URL=https://demo-api.binance.com

# LLM（见 §5.1 切换说明）
LLM_PROVIDER=deepseek
LLM_API_KEY=
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
LLM_AUTO_EXECUTE=true

# 风控
MAX_TRADE_AMOUNT=50          # USDT
DAILY_LOSS_LIMIT=100         # USDT

# Agent
AGENT_TICK_INTERVAL=300      # 秒，默认 5 分钟
TRADE_SYMBOL=BTCUSDT

# 数据库
DATABASE_URL=sqlite+aiosqlite:///./data/bianca.db

# 服务
API_HOST=127.0.0.1
API_PORT=8000
LOG_LEVEL=INFO
```

---

## 6. 部署（PoC）

```yaml
# docker-compose.yml
services:
  api:
    build: .
    ports:
      - "127.0.0.1:8000:8000"
    env_file: .env
    volumes:
      - ./data:/app/data
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

**前置条件（DeepSeek，默认）：** 在 `.env` 中配置有效的 `LLM_API_KEY`。

**切换 Ollama 时：** 宿主机安装并运行 Ollama，且已 pull 对应模型；`docker-compose.yml` 保留 `extra_hosts` 以便容器访问宿主机。

---

## 7. MVP 扩展概要

| 模块 | MVP 新增 |
|------|----------|
| 前端 | React + TypeScript + Vite |
| Agent | Strategy Agent；半自动 confirm 路径 |
| 策略 | grid / dca / trend 模板 |
| 风控 | 8 条规则 + 熔断器 |
| 交易所 | U 本位 + 币本位合约 |
| 数据 | PostgreSQL + TimescaleDB + Redis |
| 通知 | Telegram Bot |
| 安全 | API Token 鉴权 |
| 汇总 | Summary Service；`session_summaries`；`/summary/*` |
| API | + `/strategies/*`, `/summary/*`, `/ws/market`, `/ws/system`, confirm 端点 |

详细设计见《汇总管理模块设计-Bianca.md》；其余 MVP 模块保留在原 v1.0 模块设计中，PoC 验收后按需启用。

---

## 8. 安全设计

| 层面 | PoC | MVP |
|------|-----|-----|
| 网络 | 仅 `127.0.0.1` 绑定 | + API Token |
| API Key | `.env` 明文（本地，含 DeepSeek Key） | AES-256 加密存储 |
| 日志 | API Key 脱敏 | 同 |
| 风控 | 2 条硬规则 | 8 条 + 熔断 |

---

## 9. 监控（PoC）

| 指标 | 告警条件 |
|------|----------|
| LLM 可达性 | /health 检查当前 LLM_PROVIDER 失败 |
| Demo WS 连接 | 断连 > 60s |
| 日亏损 | ≥ 限额 80% 时日志 WARN |
| Agent 异常 | Runner 未捕获异常 → 日志 ERROR |

MVP 引入 Prometheus + Grafana。
