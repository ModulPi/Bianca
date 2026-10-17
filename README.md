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

**下一步方向（目标驱动，见 [PRD §2.4](docs/PRD-Bianca.md) 与 [经理 Agent 与对话层设计](docs/outline-design/模块设计/经理Agent与对话层设计-Bianca.md)）：** 用户以对话方式给出一个交易目标，由**经理 Agent** 理解目标、指挥手下的子 Agent（行情 / 分析 / 风控 / 执行）执行并追踪进度，直到达成 / 中止 / 到期。**对话层（阶段 C）已落地**：多轮修正、独立确认动作、计划落库，经 HTTP 端点与薄 CLI 使用（见下方「对话层」）。**阶段 D（闭环执行）已接通**：目标 tick 串起「终止判据 → 破线保护 → 预算 → 经理决策 → 风控 → 执行」；达成先全平再转终态，后台循环只跑 active 目标。尚差真机冒烟（D-5，需要代理 + 模型 + 连续数据面）。

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
│   ├── 模块设计/
│   │   ├── 行情数据模块设计-Bianca.md
│   │   └── 经理Agent与对话层设计-Bianca.md
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

## 快速开始（PoC，待实现）

```bash
# 1. 配置环境变量（默认 DeepSeek）
cp .env.example .env
# 编辑 .env：LLM_PROVIDER=deepseek, LLM_API_KEY=sk-xxx

# 2. 启动 API
docker compose up api

# 3. 启动 Agent
curl -X POST http://127.0.0.1:8000/api/v1/agent/start
```

**切换本地 Ollama：** 修改 `.env` 中 `LLM_PROVIDER=ollama` 及相关 URL/模型，重启 API 即可。

## 对话层（阶段 C，ADR-032）

用自然语言提一个交易目标，经理回问缺的维度、复述、等你**单独确认**，然后做计划：

```bash
BASE=http://127.0.0.1:8000/api/v1

# 1. 说一个目标（不完整也行，它会点名问缺哪个维度）
curl -s -X POST $BASE/dialogue/start \
  -H 'Content-Type: application/json' \
  -d '{"text":"我想用 1000 USDT 试水，两周内做到 1100，最多亏 5% 就停"}'

# 2. 补充 / 修正（只改提到的那个维度）
curl -s -X POST $BASE/dialogue/<goal_id>/say \
  -H 'Content-Type: application/json' \
  -d '{"text":"做 BTCUSDT"}'

# 3. 采集一次交易所权益快照（确认的基准权益取它，ADR-033）
curl -s -X POST $BASE/portfolio/snapshot

# 4. 确认 —— 正文里说「对」不算确认，这一步才是（ADR-037）
curl -s -X POST $BASE/dialogue/<goal_id>/confirm

# 5. 让经理做计划并落库
curl -s -X POST $BASE/dialogue/<goal_id>/plan
```

薄 CLI（只是拼上面这些请求，不含任何判断）：

```bash
python -m agent.dialogue.cli start "用 1000 USDT 做 BTCUSDT，两周内赚 10%，最多亏 5% 就停"
python -m agent.dialogue.cli say <goal_id> "回撤放到 8%"
python -m agent.dialogue.cli show
python -m agent.dialogue.cli confirm <goal_id>
python -m agent.dialogue.cli plan <goal_id>
```

> **⚠️ PoC 阶段没有鉴权。** 这些端点能改目标、能触发计划，只能绑在
> `127.0.0.1` 上（`docker-compose.yml` 已如此绑定）。不要把它暴露到公网，
> 也不要把端口映射改成 `0.0.0.0`。

## 许可证

MIT
