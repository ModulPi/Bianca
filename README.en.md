# Bianca

English | **[简体中文](./README.md)**

A crypto auto-trading Agent platform for retail users. **PoC focus:** LLM autonomous decisions + Binance Demo spot + LangGraph orchestration.

## Current Stage: PoC

On Binance Demo spot, the LLM (Ollama) autonomously analyzes market data, produces buy/sell signals, passes minimal risk checks, and completes at least **one buy + one sell** cycle.

| Item | PoC Scope |
|------|-----------|
| Exchange | Binance Demo spot |
| Decision | LLM autonomous (BUY/SELL/HOLD) |
| Orchestration | LangGraph Supervisor |
| Risk Control | Max trade amount + daily loss limit |
| Deployment | Docker (API) + host Ollama + SQLite |
| UI | CLI / curl (no web frontend) |

## Tech Stack

| Layer | PoC | MVP (Later) |
|-------|-----|-------------|
| Agent | LangGraph + Ollama | + strategy templates |
| Backend | FastAPI + Python | Same |
| Exchange | ccxt (Demo spot) | + futures |
| Database | SQLite | PostgreSQL + TimescaleDB |
| Frontend | — | React + TypeScript |

## Documentation

```
docs/
├── PRD-Bianca.md                           # Product requirements
├── 用户故事-Bianca.md                       # User stories
├── system-design/                          # System design
│   ├── 系统设计文档-Bianca.md
│   ├── 技术选型建议-Bianca.md
│   └── 容量规划报告-Bianca.md
├── outline-design/                         # Outline design
│   ├── 架构设计/架构设计文档-Bianca.md
│   └── 数据库设计/
│       ├── 数据库设计文档-Bianca.md
│       ├── 数据字典.md
│       └── sql/
│           ├── 001_poc_sqlite.sql
│           └── 002_mvp_postgres.sql
└── module-scheduling/                      # Schedule
    ├── 开发排期计划-Bianca.md
    ├── 里程碑清单-Bianca.md
    ├── 甘特图描述-Bianca.md
    └── 资源分配建议-Bianca.md
```

## Quick Start (PoC, pending implementation)

```bash
ollama pull qwen2.5:7b && ollama serve
cp .env.example .env
docker compose up api
curl -X POST http://127.0.0.1:8000/api/v1/agent/start
```

## License

MIT
