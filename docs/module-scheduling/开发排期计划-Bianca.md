# Bianca — 开发排期计划

> 版本：v0.3 | 日期：2026-07-31 | 团队：1 人（独立开发）

---

## 项目信息

| 项目 | 内容 |
|------|------|
| 项目名称 | Bianca |
| PoC 目标 | Demo 现货 LLM 自主完成 1 买 1 卖闭环 |
| 团队人数 | 1 人 |
| **PoC 预计工期** | **12 个工作日（~2.5 周）** |
| **MVP 预计工期** | **25 个工作日（PoC 后另计）** |

---

## PoC 排期（12 天）

### 阶段 P0：基础设施（2 天）

| # | 任务 | 工时 | 产出 |
|---|------|------|------|
| P0.1 | 项目骨架、`pyproject.toml`、目录结构 | 0.5d | 代码仓库结构 |
| P0.2 | `config.py` — pydantic-settings（含 LLM_AUTO_EXECUTE 等） | 0.5d | 配置中心 |
| P0.3 | SQLite 模型 + Alembic 初始化 | 0.5d | 数据层 |
| P0.4 | Dockerfile（仅 API）+ docker-compose.yml | 0.5d | Docker 部署 |

**产出：** `docker compose up api` 启动；SQLite 建表完成。

---

### 阶段 P1：交易所 Demo 现货（2 天）

| # | 任务 | 工时 | 产出 |
|---|------|------|------|
| P1.1 | `exchange/spot_demo.py` — ccxt 封装 Demo 现货 | 1d | 余额/下单/查单 |
| P1.2 | `exchange/market_stream.py` — WebSocket 行情 | 1d | 实时 ticker/K 线 |

**产出：** **M0** — Demo 现货 API 连通。

---

### 阶段 P2：LLM Analysis Agent（3 天）

| # | 任务 | 工时 | 产出 |
|---|------|------|------|
| P2.1 | `llm/prompts.py` — 结构化输出 Prompt | 0.5d | Prompt 模板 |
| P2.2 | `llm/analyzer.py` — 连接宿主机 Ollama | 1d | MarketAnalyzer |
| P2.3 | `graph/analysis_agent.py` — 产出 BUY/SELL/HOLD | 1d | Analysis Agent |
| P2.4 | `LLM_AUTO_EXECUTE` 开关逻辑 | 0.5d | 可配置执行 |

**产出：** **M1** — LLM 能产出结构化交易信号。

---

### 阶段 P3：风控 + 执行 + LangGraph（3 天）

| # | 任务 | 工时 | 产出 |
|---|------|------|------|
| P3.1 | `risk/rules.py` — 单笔上限 + 日亏损 2 条规则 | 1d | 最小风控 |
| P3.2 | `graph/risk_agent.py` + `graph/execute_agent.py` | 1d | 风控/执行节点 |
| P3.3 | `graph/supervisor.py` — 图编译 + SqliteSaver | 1d | 完整 StateGraph |

**产出：** **M2** — 风控生效，Demo 现货可下单。

---

### 阶段 P4：Agent 循环 + API + 验收（2 天）

| # | 任务 | 工时 | 产出 |
|---|------|------|------|
| P4.1 | `agent/runner.py` — 定时触发 Agent 循环 | 0.5d | 7×24 运行 |
| P4.2 | `api/routes.py` — start/stop/status/trades | 0.5d | CLI 接口 |
| P4.3 | 集成测试 + Demo 环境 1 买 1 卖验收 | 0.5d | 闭环验证 |
| P4.4 | 文档与 `.env.example` 完善 | 0.5d | 交付 |

**产出：** **M3** — PoC 验收通过。

---

## PoC 关键路径

```
P0(2d) → P1(2d) → P2(3d) → P3(3d) → P4(2d) = 12d
         M0        M1        M2        M3
```

---

## MVP 排期概要（PoC 后，25 天）

| 阶段 | 名称 | 工时 | 里程碑 |
|------|------|------|--------|
| M0 | PostgreSQL + TimescaleDB + Redis 迁移 | 3d | 基础设施升级 |
| M1 | 策略模板引擎（grid/dca/trend） | 5d | 规则策略 |
| M2 | 完整风控 8 条 + 半自动确认 API | 4d | 风控 + 半自动 |
| M3 | 合约 API + 模拟→实盘门禁 | 4d | 全品种 |
| M4 | React Web 控制台 | 7d | Web 上线 |
| M5 | 测试 + Telegram 通知 | 2d | MVP 交付 |

> MVP 详细任务见各模块设计文档，PoC 验收通过后再细化排期。

---

## 风险与缓冲

| 风险 | 缓解 |
|------|------|
| LLM 输出不稳定 | 结构化 Prompt + JSON 模式 + 解析失败默认 HOLD |
| Ollama 宿主机连不通 | `.env` 文档说明 `host.docker.internal` |
| Demo API 限流 | 降低 Agent 循环频率（如 5min/次） |
| PoC 缓冲 | 建议预留 3 天（总计 15 天） |
