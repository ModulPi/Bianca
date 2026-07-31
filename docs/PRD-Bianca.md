# Bianca — 产品需求文档（PRD）

> 版本：v0.3 | 日期：2026-07-31 | 阶段：概念验证（PoC） | 状态：需求已确认 ✅

---

## 1. 产品概述

### 1.1 产品定位

Bianca 是一个**面向 C 端用户的加密货币自动交易 Agent 平台**，基于币安（Binance）交易所，帮助用户通过 AI Agent 辅助的自动化交易策略实现数字资产管理。

**PoC 聚焦：** 在币安 **Demo 现货** 环境上，由 **LLM 自主决策** 完成至少一次 **买入 → 卖出** 闭环。

**MVP 及后期：** 扩展策略模板、半自动模式、Web 控制台、合约品种与完整风控。

### 1.2 目标用户

| 用户画像 | 特征 | 核心诉求 |
|----------|------|----------|
| 初级散户 | 交易经验 < 1 年，资金量小 | "帮我自动赚钱，不用盯盘" |
| 进阶玩家 | 有一定交易经验，有自己的策略思路 | "把我的策略交给 Agent 自动执行" |
| 跟单用户 | 不想自己分析，愿意跟随信号 | "跟着高手/信号走" |

> **PoC 阶段：** 仅开发者本人本地使用（`localhost`），无多用户、无注册登录。

### 1.3 核心价值主张

- **Agent 自主决策（PoC 核心）：** LLM 分析行情，自主产出买/卖/持有信号
- **可配置执行模式：** 用户可选择 AI 建议「自动执行」或「仅记录不执行」（PoC 默认自动执行）
- **风险可控：** 内置最小风控，PoC 验证后再扩展
- **透明可审计：** 每笔交易的决策过程可回溯
- **本地 LLM 优先：** 默认宿主机 Ollama，保护隐私、零 API 费用

---

## 2. 阶段边界（PoC / MVP / 后期）

### 2.1 PoC — 必须交付

| 项 | 范围 |
|----|------|
| **交易所** | 币安 Demo 现货（`demo-api.binance.com`） |
| **决策模型** | **LLM 自主：** Analysis Agent 分析行情 → 产出 BUY/SELL/HOLD → 风控 → 执行 |
| **Agent 编排** | LangGraph Supervisor 模式 |
| **闭环目标** | Agent 自主完成至少 **1 次买入 + 1 次卖出** |
| **LLM** | 宿主机 Ollama（`localhost:11434`），支持配置「AI 建议自动执行」 |
| **执行模式** | 仅 **全自动**（半自动放 MVP） |
| **风控** | **最小 2 条：** 单笔金额上限 + 日亏损熔断 |
| **基础设施** | SQLite + 内存/文件缓存；Docker 仅跑 API 服务 |
| **界面** | CLI / curl 调 API，**无 Web 前端** |
| **安全** | API 仅绑定 `127.0.0.1`，PoC 不加鉴权 |
| **持久化** | SQLite 存交易日志、决策记录、Agent 状态 |

### 2.2 MVP — PoC 之后

| 项 | 范围 |
|----|------|
| **界面** | React + TypeScript 完整 Web 控制台 |
| **执行模式** | 增加 **半自动**（AI 建议 → 用户确认 → 执行） |
| **策略** | 策略模板引擎（网格、DCA、趋势跟踪） |
| **品种** | 现货 + U 本位合约 + 币本位合约 |
| **风控** | 完整 8 条规则 + 熔断状态机 |
| **基础设施** | PostgreSQL + TimescaleDB + Redis + Docker Compose 全套 |
| **LLM** | 分析报告 + 可选自动执行；支持云端 LLM 切换 |
| **通知** | Telegram / 邮件告警 |
| **模拟门禁** | 模拟交易验证达标后才允许切换实盘 |

### 2.3 后期（P2）

策略回测、策略市场、多交易所、移动端、信号跟单、社交交易、国际化。

---

## 3. 功能需求

### 3.1 PoC 功能（P0）

| 功能 | 描述 |
|------|------|
| **币安 Demo 现货对接** | REST 下单/查余额 + WebSocket 行情（ccxt 或 python-binance） |
| **LLM 自主决策 Agent** | LangGraph：Supervisor → Analysis(LLM) → Risk → Execute |
| **AI 自动执行配置** | 环境变量 `LLM_AUTO_EXECUTE=true/false` 控制是否自动下单 |
| **最小风控** | 单笔最大交易额 + 日亏损熔断 |
| **交易日志** | 记录：时间、方向、价格、数量、LLM 决策理由、风控结果 |
| **Agent 状态持久化** | LangGraph SQLite Checkpointer |

### 3.2 MVP 功能（P1）

| 功能 | 描述 |
|------|------|
| Web 控制台 | 策略配置、启停、持仓、盈亏曲线 |
| 策略模板引擎 | 网格、DCA、趋势跟踪 |
| 半自动模式 | 信号推送 → 用户确认 → 风控 → 执行 |
| 完整风控 | 8 条规则 + 熔断器 |
| 合约 API | U 本位 + 币本位 |
| 告警通知 | Telegram Bot |
| 模拟→实盘门禁 | 24h+ 模拟验证达标检查 |

### 3.3 后期功能（P2）

见 §2.3。

---

## 4. 核心业务流程

### 4.1 PoC 主流程（LLM 自主 + 全自动）

```
启动 Agent → 采集 Demo 现货行情（WebSocket/REST）
→ Analysis Agent（Ollama）分析 → 输出 BUY / SELL / HOLD + 理由 + 置信度
→ 若 HOLD：等待下一周期
→ 若 BUY/SELL 且 LLM_AUTO_EXECUTE=true：
    → Risk Agent（单笔上限 + 日亏损检查）
    → 通过 → Execute Agent 在 Demo 现货下单
    → 记录日志 + Checkpointer 持久化
→ 直至完成至少 1 买 + 1 卖 → PoC 验收
```

### 4.2 MVP 半自动流程（PoC 不做）

```
LLM/策略 产生信号 → 推送 Web 前端 → 用户确认/拒绝
→ 确认 → Risk Agent → Execute Agent → 日志
```

### 4.3 风控流程（PoC 最小版）

```
LLM 产出交易信号
  → ① 单笔金额 ≤ MAX_TRADE_AMOUNT？
  → ② 当日累计亏损 < DAILY_LOSS_LIMIT？
  → 任一拒绝 → 记录原因，不下单
  → 全部通过 → 执行
```

---

## 5. 技术架构概要

```
┌─────────────────────────────────────────┐
│  PoC：CLI / curl → FastAPI (127.0.0.1)  │
├─────────────────────────────────────────┤
│  LangGraph Supervisor                   │
│    → Analysis Agent (Ollama @ 宿主机)    │
│    → Risk Agent (2 条规则)               │
│    → Execute Agent (Demo 现货 ccxt)      │
├─────────────────────────────────────────┤
│  SQLite + LangGraph SqliteSaver         │
│  内存/文件行情缓存                        │
└─────────────────────────────────────────┘
         │ REST/WSS                │ HTTP
         ▼                         ▼
   币安 Demo 现货              Ollama (localhost)
```

**MVP 扩展：** + React 前端 + PostgreSQL/TimescaleDB + Redis + 策略模板 + 完整风控。

---

## 6. 非功能需求

| 类别 | PoC | MVP |
|------|-----|-----|
| 安全性 | localhost 绑定；API Key 从 `.env` 读取 | AES-256 加密存储；API Token 鉴权 |
| 可靠性 | Agent 7×24；WS 自动重连 | + Telegram 告警 |
| 性能 | 行情延迟 < 2s；LLM 推理 2–15s 可接受 | 前端刷新 < 1s |
| 部署 | Docker 跑 API；Ollama 宿主机 | Docker Compose 全套 |

---

## 7. PoC 里程碑

| 里程碑 | 目标 | 产出 |
|--------|------|------|
| **M0** | 环境搭建 | Demo 现货 API 连通；Ollama 可达；Docker API 启动 |
| **M1** | LLM 决策链路 | Analysis Agent 能输出 BUY/SELL/HOLD |
| **M2** | 风控 + 执行 | 最小风控生效；Demo 现货能下单 |
| **M3** | 闭环验收 | Agent 自主完成 1 买 + 1 卖；日志可追溯 |

```
M0 → M1 → M2 → M3
```

---

## 8. 已确认决策

| # | 决策项 | 结论 | 日期 |
|---|--------|------|------|
| D1 | 项目命名 | **Bianca**（统一替换 FnAgent） | 2026-07-31 |
| D2 | PoC 目标 | Demo 现货 LLM 自主完成 1 买 1 卖闭环 | 2026-07-31 |
| D3 | 决策模型 | LLM 自主决策（非规则策略模板） | 2026-07-31 |
| D4 | Agent 编排 | LangGraph Supervisor（PoC 即用） | 2026-07-31 |
| D5 | LLM 部署 | 宿主机 Ollama，Docker 只跑 API | 2026-07-31 |
| D6 | LLM 执行 | 用户可配置 AI 建议自动执行 | 2026-07-31 |
| D7 | PoC 基础设施 | SQLite 轻量，无 PG/Redis/TimescaleDB | 2026-07-31 |
| D8 | PoC 界面 | 无前端；MVP 做 React 控制台 | 2026-07-31 |
| D9 | PoC 半自动 | 不做；MVP 增加 | 2026-07-31 |
| D10 | PoC 安全 | 仅 localhost，不加鉴权 | 2026-07-31 |
| D11 | PoC 风控 | 单笔上限 + 日亏损熔断 | 2026-07-31 |
| D12 | 币安环境 | Demo 现货（非 Testnet，PoC 不做合约） | 2026-07-31 |

---

## 9. 术语表

| 术语 | 说明 |
|------|------|
| PoC | Proof of Concept，概念验证 |
| Demo Mode | 币安官方模拟交易（`demo-api.binance.com`） |
| LLM 自主决策 | Analysis Agent 直接产出买卖信号，非规则策略模板 |
| 半自动 | AI/策略出信号 → 用户确认 → 执行（MVP） |
