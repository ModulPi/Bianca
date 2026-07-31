# Bianca

English | **[简体中文](./README.md)**

A crypto auto-trading Agent platform for retail users, built on Binance, covering spot, USDT-margined futures, and coin-margined futures.

## Product Overview

Bianca (FnAgent) helps users manage digital assets through a hybrid model of **strategy templates + AI analysis + signal copy-trading**, with full-auto or semi-auto execution to lower the barrier for everyday traders.

## Key Features

- **Zero-code automation** — configure strategy params and run, no programming required
- **AI-assisted decisions** — Ollama local LLM by default, cloud models optional
- **Multi-layer risk control** — strategy → risk → execution; paper trading before live
- **Full product coverage** — spot, USDT-M, and coin-M futures in one system
- **Transparent & auditable** — full traceability of every trading decision
- **Open source & self-hosted** — one-click Docker Compose deploy, full data ownership

## Tech Stack

| Layer | Technology |
|-------|------------|
| Agent Orchestration | LangGraph (Supervisor mode) |
| Backend | Python |
| Frontend | TypeScript / React |
| Database | PostgreSQL |
| LLM | Ollama (local-first) |
| Exchange | Binance REST + WebSocket |

## Documentation

```
docs/
├── PRD-FnAgent.md                          # Product Requirements
├── 用户故事-FnAgent.md                      # User Stories
├── system-design/                          # System Design
│   ├── 系统设计文档-FnAgent.md              #   System Design Document
│   ├── 容量规划报告-FnAgent.md              #   Capacity Planning Report
│   └── 技术选型建议-FnAgent.md              #   Technology Selection Guide
├── outline-design/                         # Outline Design
│   ├── 架构设计/架构设计文档-FnAgent.md      #   Architecture Design (C4)
│   └── 数据库设计/
│       ├── 数据库设计文档-FnAgent.md        #   Database Design Document
│       ├── 数据字典.md                      #   Data Dictionary
│       └── sql/001_init.sql                #   Initial DDL Script
└── module-scheduling/                      # Development Schedule
    ├── 开发排期计划-FnAgent.md              #   Development Plan
    ├── 里程碑清单-FnAgent.md                #   Milestone Checklist
    ├── 甘特图描述-FnAgent.md                #   Gantt Chart Description
    └── 资源分配建议-FnAgent.md              #   Resource Allocation Guide
```

## Development Stage

Currently in **Proof of Concept (PoC)** — requirements and design docs are complete; implementation is pending.

## License

MIT
