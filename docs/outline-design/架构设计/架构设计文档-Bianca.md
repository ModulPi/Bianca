# Bianca — 架构设计文档 (C4 模型)

> 版本：v0.4 | 日期：2026-07-31 | 基于 PRD v0.4

---

## 1. C4 Level 1: 系统上下文（PoC）

```
┌─────────────────┐     ┌───────────────────┐     ┌─────────────────┐
│  开发者 (CLI)    │────▶│      Bianca       │◀────│ DeepSeek API（默认）│
│  curl/localhost  │◀────│  LLM 自主交易 Agent │     │ 或 Ollama（可切换） │
└─────────────────┘     └─────────┬─────────┘     └─────────────────┘
                                  │ HTTPS / WSS
                                  ▼
                        ┌───────────────────┐
                        │ 币安 Demo 现货     │
                        │ demo-api.binance  │
                        └───────────────────┘
```

---

## 2. C4 Level 2: 容器图（PoC）

```
┌────────────────────────────────────────────────────┐
│                    Bianca PoC                       │
│                                                     │
│  ┌─────────────────────────────────────────────┐   │
│  │  API 容器 (Docker)                           │   │
│  │  FastAPI @ 127.0.0.1:8000                   │   │
│  │  ├─ Agent Runner (定时循环)                  │   │
│  │  ├─ LangGraph StateGraph                    │   │
│  │  │    Supervisor → Analysis → Risk → Execute │   │
│  │  ├─ REST: /api/v1/agent/*, /trades          │   │
│  │  └─ SQLite (bianca.db + checkpoints.db)     │   │
│  └─────────────────────────────────────────────┘   │
│                                                     │
│  宿主机 (切换 ollama 时需要):                        │
│  └─ Ollama (qwen2.5:7b) @ localhost:11434           │
└────────────────────────────────────────────────────┘
```

**MVP 扩展容器：** Web 前端、PostgreSQL、TimescaleDB、Redis、Telegram 通知。

---

## 3. LangGraph 拓扑（PoC — LLM 自主）

```
              ┌─────────┐
              │  START   │ ← Agent Runner 定时触发
              └────┬─────┘
                   ▼
           ┌───────────────┐
           │  Supervisor   │ ← 加载行情，路由到 Analysis
           └───────┬───────┘
                   ▼
           ┌───────────────┐
           │ Analysis Agent│ ← DeepSeek（默认）/ Ollama 产出 BUY/SELL/HOLD
           └───────┬───────┘
                   │
         ┌─────────┼─────────┐
         │ HOLD    │ BUY/SELL + LLM_AUTO_EXECUTE=true
         ▼         ▼
       END    ┌───────────────┐
              │  Risk Agent   │ ← 单笔上限 + 日亏损（必经）
              └───────┬───────┘
                      │
              ┌───────┴───────┐
         rejected          approved
              │                │
              ▼                ▼
            END        ┌───────────────┐
                       │ Execute Agent │ ← Demo 现货下单
                       └───────┬───────┘
                               ▼
                             END
```

> **PoC 无 Strategy Agent 节点**（无规则策略模板）。MVP 增加 Strategy Agent 和半自动确认路径。

---

## 4. ADR 架构决策记录

### ADR-001: LangGraph Supervisor（PoC 即用）
- **状态:** ✅ 已采纳
- **决策:** PoC 采用 LangGraph StateGraph + Supervisor
- **理由:** 统一 Agent 编排；SqliteSaver 满足审计；MVP 可扩展 Strategy 节点

### ADR-002: LLM 自主决策（PoC 核心）
- **状态:** ✅ 已采纳 | **日期:** 2026-07-31
- **决策:** PoC 由 Analysis Agent 自主产出 BUY/SELL/HOLD（默认 DeepSeek，可切换 Ollama）
- **理由:** PoC 目标是验证 AI Agent 自主交易闭环，非规则策略
- **约束:** `LLM_AUTO_EXECUTE=false` 时只记录信号

### ADR-003: LLM 提供商可配置（DeepSeek 默认，Ollama 可切换）
- **状态:** ✅ 已采纳 | **日期:** 2026-07-31
- **决策:** PoC 默认 DeepSeek API；通过 `LLM_PROVIDER=deepseek|ollama` 切换，OpenAI 兼容接口统一封装
- **理由:**
  - PoC 快速验证，无需本地 GPU/大模型部署
  - 后期切 Ollama 仅改 `.env`，保护隐私、降本
  - DeepSeek 与 Ollama 均支持 OpenAI 兼容 `/v1/chat/completions`
- **代价:** PoC 阶段行情/决策数据发送至 DeepSeek 云端
- **替代方案:** 仅 Ollama（部署门槛高）；多 Provider 硬编码（切换成本高）

### ADR-004: SQLite（PoC）→ PostgreSQL（MVP）
- **状态:** ✅ 已采纳 | **日期:** 2026-07-31
- **决策:** PoC 用 SQLite；MVP 迁移 PostgreSQL + TimescaleDB
- **理由:** PoC 单用户、数据量小；MVP 需要策略模板、K 线时序

### ADR-005: 币安 Demo 现货（PoC 唯一环境）
- **状态:** ✅ 已采纳 | **日期:** 2026-07-31
- **决策:** PoC 只用 `demo-api.binance.com` 现货，不用 Testnet，不做合约
- **理由:** 避免 Demo/Testnet/合约混用；聚焦闭环验证

### ADR-006: 风控必经节点
- **状态:** ✅ 已采纳
- **决策:** Risk Agent 是 Execute 的唯一前置，无旁路
- **PoC 规则:** 单笔上限 + 日亏损（2 条）
- **MVP 扩展:** 8 条完整规则

---

## 5. PoC 时序：LLM 自主交易

```
Runner      Supervisor    Analysis     LLM API     Risk       Execute     Demo API    SQLite
  │              │            │           │          │           │           │          │
  │ tick         │            │           │          │           │           │          │
  │─────────────▶│            │           │          │           │           │          │
  │              │───────────▶│           │          │           │           │          │
  │              │            │ chat      │          │           │           │          │
  │              │            │──────────▶│          │           │           │          │
  │              │            │◀──────────│          │           │           │          │
  │              │            │ BUY 0.001 BTC        │           │           │          │
  │              │            │─────────────────────▶│           │           │          │
  │              │            │           │  approved │           │           │          │
  │              │            │           │          │──────────▶│           │          │
  │              │            │           │          │           │ market buy│          │
  │              │            │           │          │           │──────────▶│          │
  │              │            │           │          │           │◀──────────│          │
  │              │            │           │          │           │ INSERT log│          │
  │              │            │           │          │           │─────────────────────▶│
```

---

## 6. MVP 扩展（概要）

| 组件 | MVP 新增 |
|------|----------|
| 前端 | React SPA @ :3000 |
| Agent | + Strategy Agent；+ 半自动 confirm 路径 |
| 数据 | PostgreSQL + TimescaleDB + Redis |
| 交易所 | + U 本位/币本位合约 |
| 通知 | Telegram Bot |

详细设计见《系统设计文档-Bianca.md》MVP 章节。
