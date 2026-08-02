# Bianca — 里程碑清单

> 版本：v0.3 | 日期：2026-07-31

---

## PoC 里程碑

| 里程碑 | 名称 | 完成标准 | 预计日期* |
|--------|------|----------|-----------|
| **M0** | Demo 现货 API 连通 | 查余额、获取 BTCUSDT 行情、DeepSeek API `/health` 可达 | D+4 |
| **M1** | LLM 决策链路 | Analysis Agent 输出结构化 BUY/SELL/HOLD | D+7 |
| **M2** | 风控 + 执行 | 2 条风控规则生效；Demo 现货市价单成功 | D+10 |
| **M3** | 闭环验收 | 日志含 1 BUY + 1 SELL（filled）；Checkpointer 可回放 | D+12 |

\* 假设 D = 项目启动日，12 工作日节奏。

---

## MVP 里程碑（PoC 后）

| 里程碑 | 名称 | 完成标准 |
|--------|------|----------|
| **M4** | 基础设施升级 | PG + TimescaleDB + Redis 迁移完成 |
| **M5** | 策略模板 | 网格/DCA/趋势 3 模板可运行 | ✅ PoC SQLite 已实现 |
| **M6** | 半自动 + 完整风控 | Web 确认流 + 8 条风控规则 | ✅ PoC SQLite 已实现 |
| **M6.5** | 汇总管理 | Summary API + `session_summaries`；Agent 启停自动生成会话快照 | ✅ PoC SQLite 已实现 |
| **M7** | Web 控制台 | React 前端全功能可用（含汇总仪表盘） | ✅ PoC 已实现 |
| **M8** | MVP 交付 | 模拟门禁 + Telegram 通知 + 合约 API |

---

## 验收检查表（PoC M3）

- [x] `docker compose up api` 成功，绑定 `127.0.0.1:8000`
- [ ] Ollama 在宿主机运行，API 容器可访问（`LLM_PROVIDER=ollama` 时，待冒烟）
- [x] `POST /api/v1/agent/start` 启动 Agent
- [x] `trade_logs` 表有 ≥1 BUY + ≥1 SELL，`status=filled`
- [x] 每条记录含 `decision_reason`（LLM 理由）和 `risk_decision`
- [x] `LLM_AUTO_EXECUTE=false` 时只记录信号不下单（单元测试覆盖）
- [x] 日亏损超限时 Agent 拒绝新单（单元测试覆盖）
- [x] Checkpointer 可回放（`GET /api/v1/checkpoints/threads/{id}/history`）
- [x] 买卖闭环后 Agent 自动 stop（`loop_closed=true`）
