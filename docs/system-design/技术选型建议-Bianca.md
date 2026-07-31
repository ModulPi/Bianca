# Bianca — 技术选型建议

> 版本：v0.3 | 日期：2026-07-31

---

## 1. PoC 技术栈

```
┌─────────────────────────────────────────────────┐
│                  Bianca PoC                      │
├──────────────┬──────────────┬───────────────────┤
│ Agent 编排    │ 交易所        │ 基础设施           │
│ LangGraph    │ ccxt (Demo)  │ SQLite            │
│ LangChain    │ python-binance│ 内存行情缓存       │
│ Ollama(宿主机)│ (WS 行情)    │ Docker (仅 API)   │
├──────────────┼──────────────┼───────────────────┤
│ Web 框架      │ LLM          │ 配置              │
│ FastAPI      │ qwen2.5:7b   │ pydantic-settings │
│ Uvicorn      │              │                   │
└──────────────┴──────────────┴───────────────────┘
```

## 2. MVP 增量技术栈

| 层 | 新增 |
|----|------|
| 前端 | React 18 + TypeScript + Vite + Tailwind |
| 数据库 | PostgreSQL 16 + TimescaleDB 2.x |
| 缓存 | Redis 7 |
| 图表 | Recharts + lightweight-charts |
| 状态 | Zustand |
| 通知 | Telegram Bot API |
| 部署 | Docker Compose 全套 |

---

## 3. 关键选型理由（PoC）

### LangGraph
- PoC 即用 Supervisor 模式编排 LLM → 风控 → 执行
- SqliteSaver 满足决策审计
- MVP 可扩展 Strategy Agent 节点

### ccxt (Demo 现货)
- 统一 API，MVP 扩展合约时复用
- `enableRateLimit: true` 内置限流

### 宿主机 Ollama
- Docker 只跑 API，最轻部署
- 通过 `host.docker.internal` 连接

### SQLite
- 零配置，PoC 单用户足够
- MVP 迁移 PostgreSQL（Alembic 迁移脚本）

---

## 4. 版本要求

| 组件 | PoC 版本 |
|------|----------|
| Python | ≥ 3.11 |
| LangGraph | ≥ 0.2 |
| FastAPI | ≥ 0.110 |
| ccxt | ≥ 4.x |
| SQLAlchemy | ≥ 2.0 (async) |
| Ollama | latest |

---

## 5. 明确不选（PoC）

| 组件 | 原因 |
|------|------|
| PostgreSQL/TimescaleDB | PoC 数据量小，SQLite 足够 |
| Redis | 内存缓存即可 |
| React 前端 | PoC 用 CLI/curl |
| 策略模板引擎 | PoC 用 LLM 自主决策 |
| Testnet | 统一用 Demo 现货，避免环境混用 |
