# FnAgent — 资源分配建议

> 版本：v1.0 | 日期：2026-07-28 | 团队：1 人 → 可扩展至 3 人

---

## 1. 当前资源配置（1 人）

| 角色 | 人 | 负责模块 |
|------|-----|----------|
| **全栈开发** | 你 | 全部模块（Python 后端 + React 前端 + DevOps） |

### 单人开发策略

| 策略 | 说明 |
|------|------|
| **先纵后横** | 先打穿一条链路（配置→交易所→策略→风控→执行），再横向扩展功能和前端 |
| **每日优先级** | 早上写核心逻辑（策略/风控/编排），下午写 API/前端/测试 |
| **Mock 先行** | 前端开发期间，后端 API 先用 JSON Mock 跑通 UI 流程 |
| **模拟盘驱动** | 每个阶段结束都在模拟盘跑一遍，问题不过夜 |

---

## 2. 技能要求清单

### 必须掌握的技能（开发前确保具备）

| 技能 | 用途 | 熟练度要求 | 学习资源 |
|------|------|-----------|----------|
| Python asyncio | 异步 WebSocket + ccxt | 中 | 官方文档 |
| LangGraph StateGraph | Agent 编排 | 中 | LangGraph 官方 Tutorial |
| FastAPI + Pydantic | API 开发 | 高 | FastAPI 文档 |
| SQLAlchemy 2.0 (async) | ORM | 中 | 官方 Migration Guide |
| ccxt | 交易所 API | 中 | ccxt Wiki |
| React 18 + TypeScript | 前端 | 中高 | React 官方文档 |
| Docker Compose | 部署 | 低 | 模板已有，改参数即可 |

### 可以边做边学的技能

| 技能 | 用途 | 何时需要 |
|------|------|----------|
| TimescaleDB 超表/压缩 | K 线存储 | 阶段 0 (SQL 模板已有) |
| lightweight-charts | K 线图 | 阶段 8 (前端开发时) |
| Ollama API | LLM 推理 | 阶段 6 |
| LangChain prompt template | 提示词 | 阶段 6 |
| Telegram Bot API | 通知 | 阶段 7 |
| Recharts | 收益曲线图 | 阶段 8 |

---

## 3. 开发环境准备清单

### 硬件

| 资源 | 最低 | 推荐 | 你有吗？ |
|------|------|------|----------|
| CPU | 4 核 | 8 核 | ⬜ |
| 内存 | 16 GB | 32 GB (Ollama 吃内存) | ⬜ |
| 磁盘 | 50 GB SSD | 100 GB SSD | ⬜ |
| 网络 | 10 Mbps | 50 Mbps (行情流稳) | ⬜ |

### 软件

| 软件 | 版本 | 用途 | 已安装？ |
|------|------|------|----------|
| Python | 3.11+ | 后端 | ⬜ |
| Node.js | 20 LTS | 前端 | ⬜ |
| Docker Desktop | latest | 部署 | ⬜ |
| Git | latest | 版本控制 | ⬜ |
| Ollama | latest | LLM | ⬜ |
| VS Code | latest | IDE | ⬜ |
| qwen2.5:7b (Ollama pull) | — | LLM 模型 | ⬜ |

### 账号

| 服务 | 用途 | 已注册？ |
|------|------|----------|
| 币安账号 | 创建 API Key (Demo Mode) | ⬜ |
| GitHub | 代码托管 (MIT 开源) | ⬜ |
| Telegram | Bot Token (通知) | ⬜ |

---

## 4. 扩展到 2-3 人团队

### 角色分配

| 角色 | 人数 | 职责 | 技能要求 |
|------|------|------|----------|
| **后端开发 (Agent 核心)** | 1 | Python 后端：策略/风控/编排/交易所 | Python + LangGraph + ccxt |
| **前端开发** | 1 | React 前端：全部页面和组件 | React + TS + 图表库 |
| **DevOps/QA (可兼职)** | 0.5-1 | Docker、CI/CD、测试、文档 | Docker + pytest |

### 3 人团队排期（压缩到 ~18 天）

```
Week 1:
  后端: 阶段0+1 (基础设施 + 交易所)
  前端: 阶段8.1-8.2 (脚手架 + 共享组件)
  DevOps: Docker + 环境

Week 2:
  后端: 阶段2+3 (策略 + 风控)
  前端: 阶段8.3-8.5 (Dashboard + Strategy + Position)
  DevOps: CI 流水线

Week 3:
  后端: 阶段4+5 (Agent编排 + API)
  前端: 阶段8.6-8.8 (Trade + Analysis + Settings)
  DevOps: 测试框架

Week 4:
  后端: 阶段6+7 (LLM + 通知)
  前端: 阶段8.9 (API 对接 + 联调)
  全员: 阶段9 (测试 + 模拟盘)
```

---

## 5. 成本估算

### 开发环境（一次性）

| 项目 | 费用 |
|------|------|
| 币安 Demo Mode | ¥0（免费） |
| Ollama 本地模型 | ¥0（本地） |
| Docker Desktop | ¥0（个人免费） |
| VS Code | ¥0 |
| GitHub | ¥0（公开仓库免费） |

**一次性成本：¥0**

### 云服务器（如果需要 7×24 运行）

| 选项 | 配置 | 月费 |
|------|------|------|
| 自己的电脑 | 现有 | ¥0（电费忽略） |
| 阿里云 ECS | 4c8g 100GB | ~¥200/月 |
| Hetzner VPS | 4c8g 80GB | ~€15/月（¥120） |

> PoC 阶段建议**用自己电脑**，需要 7×24 时再考虑云服务器。

### 运行成本

| 项目 | 月费 |
|------|------|
| 币安 API | ¥0（免费） |
| Ollama | ¥0（本地推理，CPU） |
| Telegram Bot API | ¥0（免费） |
| PostgreSQL + Redis | ¥0（Docker 本地） |

**月运行成本：¥0**（完全自托管）

---

## 6. 每日开发节奏建议（单人）

```
09:00 - 10:00  写核心逻辑（策略/风控/编排代码）
10:00 - 10:30  Review + 单测
10:30 - 12:00  继续写代码
12:00 - 13:30  午休
13:30 - 15:00  API / 前端开发
15:00 - 16:00  模拟盘跑一跑，查看日志
16:00 - 17:00  文档 / 代码提交 / 收尾
```

### 每个阶段 checklist

- [ ] 代码实现完成
- [ ] 单元测试通过
- [ ] 模拟盘验证 30min+
- [ ] 代码提交 + commit message 清晰
- [ ] 更新 README（如有新功能）
