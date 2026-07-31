# Bianca

> **中文：** 面向 C 端用户的加密货币自动交易 Agent 平台，基于币安（Binance）交易所，覆盖现货、U 本位合约、币本位合约全品种。  
> **English:** A crypto auto-trading Agent platform for retail users, built on Binance, covering spot, USDT-margined futures, and coin-margined futures.

---

## 产品定位 / Product Overview

**中文：** Bianca（FnAgent）帮助用户通过 **策略模板 + AI 分析建议 + 信号跟单** 的混合决策模式实现数字资产管理，支持全自动或半自动执行，降低普通用户的交易门槛。

**English:** Bianca (FnAgent) helps users manage digital assets through a hybrid model of **strategy templates + AI analysis + signal copy-trading**, with full-auto or semi-auto execution to lower the barrier for everyday traders.

---

## 核心特性 / Key Features

| 中文 | English |
|------|---------|
| **零门槛自动化** — 无需编程，配置策略参数即可运行 | **Zero-code automation** — configure strategy params and run, no programming required |
| **AI 辅助决策** — 默认 Ollama 本地 LLM，也可切换云端模型 | **AI-assisted decisions** — Ollama local LLM by default, cloud models optional |
| **多层风控** — 策略 → 风控 → 执行三层分离，模拟交易先行验证 | **Multi-layer risk control** — strategy → risk → execution; paper trading before live |
| **全品种覆盖** — 现货 + U 本位合约 + 币本位合约统一管理 | **Full product coverage** — spot, USDT-M, and coin-M futures in one system |
| **透明可审计** — 每笔交易的决策过程可回溯 | **Transparent & auditable** — full traceability of every trading decision |
| **开源自部署** — Docker Compose 一键部署，数据完全自控 | **Open source & self-hosted** — one-click Docker Compose deploy, full data ownership |

---

## 技术栈 / Tech Stack

| 层级 Layer | 技术 Technology |
|------------|-----------------|
| Agent 编排 Orchestration | LangGraph (Supervisor mode) |
| 后端 Backend | Python |
| 前端 Frontend | TypeScript / React |
| 数据库 Database | PostgreSQL |
| LLM | Ollama (local-first) |
| 交易所 Exchange | Binance REST + WebSocket |

---

## 文档目录 / Documentation

```
docs/
├── PRD-FnAgent.md                          # 产品需求文档 / Product Requirements
├── 用户故事-FnAgent.md                      # 用户故事集 / User Stories
├── system-design/                          # 系统设计 / System Design
│   ├── 系统设计文档-FnAgent.md              #   System Design Document
│   ├── 容量规划报告-FnAgent.md              #   Capacity Planning Report
│   └── 技术选型建议-FnAgent.md              #   Technology Selection Guide
├── outline-design/                         # 概要设计 / Outline Design
│   ├── 架构设计/架构设计文档-FnAgent.md      #   Architecture Design (C4)
│   └── 数据库设计/
│       ├── 数据库设计文档-FnAgent.md        #   Database Design Document
│       ├── 数据字典.md                      #   Data Dictionary
│       └── sql/001_init.sql                #   Initial DDL Script
└── module-scheduling/                      # 开发排期 / Development Schedule
    ├── 开发排期计划-FnAgent.md              #   Development Plan
    ├── 里程碑清单-FnAgent.md                #   Milestone Checklist
    ├── 甘特图描述-FnAgent.md                #   Gantt Chart Description
    └── 资源分配建议-FnAgent.md              #   Resource Allocation Guide
```

---

## 开发阶段 / Development Stage

**中文：** 当前处于 **概念验证（PoC）** 阶段，需求与设计文档已完成，代码开发待启动。

**English:** Currently in **Proof of Concept (PoC)** — requirements and design docs are complete; implementation is pending.

---

## 许可证 / License

MIT
