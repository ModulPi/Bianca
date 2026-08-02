# Bianca

**[English](./README.en.md)** | 简体中文

面向 C 端用户的加密货币自动交易 Agent 平台。PoC 阶段聚焦：**LLM 自主决策 + 币安 Demo 现货 + LangGraph 编排**。

## 当前阶段：PoC

在币安 Demo 现货环境上，由 LLM 自主分析行情、产出买卖信号，经最小风控后在模拟盘完成 **1 次买入 + 1 次卖出** 闭环。

| 项 | PoC 范围 |
|----|----------|
| 交易所 | 币安 Demo 现货 |
| 决策 | LLM 自主（BUY/SELL/HOLD） |
| LLM | **DeepSeek API（默认）**，配置切换 Ollama |
| 编排 | LangGraph Supervisor |
| 风控 | 单笔上限 + 日亏损熔断 |
| 部署 | Docker(API) + SQLite |
| 界面 | CLI / curl（无 Web 前端） |

## 技术栈

| 层级 | PoC | MVP（后续） |
|------|-----|------------|
| Agent | LangGraph + DeepSeek/Ollama | + 策略模板 |
| 后端 | FastAPI + Python | 同 |
| 交易所 | ccxt (Demo 现货) | + 合约 |
| 数据库 | SQLite | PostgreSQL + TimescaleDB |
| 前端 | — | React + TypeScript |

## 文档目录

```
docs/
├── PRD-Bianca.md
├── 用户故事-Bianca.md
├── system-design/
│   ├── 系统设计文档-Bianca.md    # 含 LLM 切换配置说明
│   ├── 技术选型建议-Bianca.md
│   └── 容量规划报告-Bianca.md
├── outline-design/
│   ├── 架构设计/架构设计文档-Bianca.md
│   └── 数据库设计/
│       ├── 数据库设计文档-Bianca.md
│       ├── 数据字典.md
│       └── sql/
│           ├── 001_poc_sqlite.sql
│           └── 002_mvp_postgres.sql
└── module-scheduling/
    ├── 开发排期计划-Bianca.md
    ├── 里程碑清单-Bianca.md
    ├── 甘特图描述-Bianca.md
    └── 资源分配建议-Bianca.md
```

## 快速开始（PoC）

```bash
# 1. 配置环境变量
cp .env.example .env
# 编辑 .env：BINANCE_API_KEY/SECRET、LLM_API_KEY、BINANCE_PROXY（如需）

# 2. 启动 API（Docker）
docker compose up -d --build

# 3. 一键启动 Agent 并等待健康检查
python start_poc.py

# 或分步：
curl http://127.0.0.1:8000/api/v1/health
curl -X POST http://127.0.0.1:8000/api/v1/agent/start

# 4. 等待买卖闭环（可选）
python run_poc_closure.py

# 5. 查看汇总
curl http://127.0.0.1:8000/api/v1/usage
curl http://127.0.0.1:8000/api/v1/trades
curl http://127.0.0.1:8000/api/v1/summary/session/latest
```

**切换本地 Ollama：** 修改 `.env` 中 `LLM_PROVIDER=ollama` 及相关 URL/模型，重启 API 即可。

## 许可证

MIT
