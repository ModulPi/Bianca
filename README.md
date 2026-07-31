# Bianca

**[English](./README.en.md)** | 简体中文

面向 C 端用户的加密货币自动交易 Agent 平台。PoC 阶段聚焦：**LLM 自主决策 + 币安 Demo 现货 + LangGraph 编排**。

## 当前阶段：PoC

在币安 Demo 现货环境上，由 LLM（Ollama）自主分析行情、产出买卖信号，经最小风控后在模拟盘完成 **1 次买入 + 1 次卖出** 闭环。

| 项 | PoC 范围 |
|----|----------|
| 交易所 | 币安 Demo 现货 |
| 决策 | LLM 自主（BUY/SELL/HOLD） |
| 编排 | LangGraph Supervisor |
| 风控 | 单笔上限 + 日亏损熔断 |
| 部署 | Docker(API) + 宿主机 Ollama + SQLite |
| 界面 | CLI / curl（无 Web 前端） |

## 技术栈

| 层级 | PoC | MVP（后续） |
|------|-----|------------|
| Agent | LangGraph + Ollama | + 策略模板 |
| 后端 | FastAPI + Python | 同 |
| 交易所 | ccxt (Demo 现货) | + 合约 |
| 数据库 | SQLite | PostgreSQL + TimescaleDB |
| 前端 | — | React + TypeScript |

## 文档目录

```
docs/
├── PRD-Bianca.md                           # 产品需求（含 PoC/MVP 边界）
├── 用户故事-Bianca.md                       # 用户故事
├── system-design/                          # 系统设计
│   ├── 系统设计文档-Bianca.md
│   ├── 技术选型建议-Bianca.md
│   └── 容量规划报告-Bianca.md
├── outline-design/                         # 概要设计
│   ├── 架构设计/架构设计文档-Bianca.md
│   └── 数据库设计/
│       ├── 数据库设计文档-Bianca.md
│       ├── 数据字典.md
│       └── sql/
│           ├── 001_poc_sqlite.sql          # PoC DDL
│           └── 002_mvp_postgres.sql        # MVP DDL
└── module-scheduling/                      # 开发排期
    ├── 开发排期计划-Bianca.md
    ├── 里程碑清单-Bianca.md
    ├── 甘特图描述-Bianca.md
    └── 资源分配建议-Bianca.md
```

## 快速开始（PoC，待实现）

```bash
# 1. 宿主机启动 Ollama
ollama pull qwen2.5:7b
ollama serve

# 2. 配置环境变量
cp .env.example .env

# 3. 启动 API
docker compose up api

# 4. 启动 Agent
curl -X POST http://127.0.0.1:8000/api/v1/agent/start
```

## 许可证

MIT
