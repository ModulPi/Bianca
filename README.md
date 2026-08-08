# Bianca

**[English](./README.en.md)** | 简体中文

**自主交易 Agent 引擎** — 非交易平台。聚焦 LLM 自主决策、24×7 稳定运行、多 symbol 并行执行；异常时自动降级 semi_auto 供人工介入。

## 目录结构

```
Bianca/
├── agent/                 # Python 后端（API · Agent · 风控 · 策略）
├── web/                   # React 运维看板
├── tests/                 # pytest
├── scripts/               # 运维与验收脚本
│   ├── poc/               # PoC 一键启动 / 闭环等待
│   ├── up.ps1             # 启动基础栈（SQLite）
│   ├── up-m4.ps1          # 启动 M4 全栈（PG + Redis）
│   └── verify_m4_*.ps1    # 验收脚本
├── deploy/
│   ├── compose/           # Docker Compose 叠加配置
│   │   ├── m4.yml         # PostgreSQL + Redis
│   │   ├── mvp.yml        # Prometheus + Grafana（可选）
│   │   └── m4.verify.yml  # PG 镜像降级
│   ├── prometheus.yml
│   └── grafana/
├── docs/                  # PRD · 架构 · 里程碑
├── data/                  # 运行时数据（gitignore）
├── docker-compose.yml     # 基础栈：api + web
├── Dockerfile
└── pyproject.toml
```

## 定位

| 做 | 不做 |
|----|------|
| Agent 24×7 自主买卖 | 交易终端 / K 线看盘 / 手动下单 |
| 多 Worker 并行（`AGENT_SYMBOLS`） | 策略商城 / 模板 UI |
| **Agent 运维看板**（监控·K 线买卖点·仓位·收益·Token） | C 端交易平台式控制台 |
| 失败自动降级 + 人工确认 | 以人工操作为主的产品 |
| 决策审计（回放 / 会话） | 以人工下单为主的产品 |

**市场：** 加密（已实现）· A 股 / 美股（适配层钩子，未实现）

## 快速开始

```bash
cp .env.example .env
# BINANCE_* · LLM_* · AGENT_SYMBOLS=BTCUSDT,ETHUSDT

docker compose up -d --build
curl http://127.0.0.1:8000/api/v1/health
curl -X POST http://127.0.0.1:8000/api/v1/agent/start
curl http://127.0.0.1:8000/api/v1/agent/status
```

PowerShell 快捷启动：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\up.ps1 -Build
```

PoC 本地一键启动（不用 Docker）：

```powershell
python scripts/poc/start_poc.py
python scripts/poc/run_poc_closure.py
```

## 运维 Web（Agent 看板）

```bash
docker compose up -d api && cd web && npm install && npm run dev
# http://127.0.0.1:3001
```

看板设计：→ [docs/outline-design/架构设计/Agent运维看板设计-Bianca.md](./docs/outline-design/架构设计/Agent运维看板设计-Bianca.md)

## 核心配置

| 变量 | 说明 |
|------|------|
| `AGENT_SYMBOLS` | 并行 Worker 交易对，逗号分隔 |
| `TRADE_MARKET` | `crypto`（默认）/ `a_share` / `us_stock` |
| `EXECUTION_MODE` | `auto` / `semi_auto` / `signal_only` |
| `AUTO_DEGRADE_ENABLED` | 连续失败自动切 semi_auto |
| `AGENT_STOP_ON_LOOP_CLOSED` | 默认 `false`（24×7 不因 PoC 闭环停止） |

## M4 栈（PostgreSQL + Redis）

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\up-m4.ps1 -Build
powershell -ExecutionPolicy Bypass -File .\scripts\verify_m4_stack.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\verify_m4_e2e.ps1
```

等价命令：

```bash
docker compose -f docker-compose.yml -f deploy/compose/m4.yml up -d --build
```

`.env` 中 M4 相关项由 compose 覆盖：

```bash
DATABASE_URL=postgresql+asyncpg://bianca:bianca@postgres:5432/bianca
REDIS_URL=redis://redis:6379/0
```

Health 应返回 `database_backend=postgresql`、`schema_mode=mvp`、`checkpointer_backend=postgresql`。

详见 `docs/module-scheduling/里程碑清单-Bianca.md` M4 章节。

## 许可证

MIT
