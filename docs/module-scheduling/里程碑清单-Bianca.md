# Bianca — 里程碑清单

> 版本：v0.4 | 日期：2026-08-04 | 对齐 Agent 自主交易 + 运维看板定位

---

## PoC 里程碑

| 里程碑 | 名称 | 完成标准 | 状态 |
|--------|------|----------|------|
| **M0** | Demo 现货 API 连通 | 查余额、获取 BTCUSDT 行情、LLM `/health` 可达 | ✅ |
| **M1** | LLM 决策链路 | Analysis Agent 输出结构化 BUY/SELL/HOLD | ✅ |
| **M2** | 风控 + 执行 | 最小风控生效；Demo 现货市价单成功 | ✅ |
| **M3** | 闭环验收 | 日志含 1 BUY + 1 SELL（filled）；Checkpointer 可回放 | ✅ |

---

## MVP 里程碑（PoC 后）

| 里程碑 | 名称 | 完成标准 | 状态 |
|--------|------|----------|------|
| **M4** | 基础设施升级 | PG + TimescaleDB + Redis 双栈；Checkpointer PG | ✅ |
| **M5** | 策略模板（实验） | 网格/DCA/趋势后端可运行；**无产品 UI** | ✅ 实验保留 |
| **M6** | 半自动 + 完整风控 | 8 条风控 + 降级确认 API/WS | ✅ |
| **M6.5** | 汇总管理 | Summary API + 会话快照 + CSV 导出 | ✅ |
| **M7** | Agent 重构 | 24×7 多 Worker · MarketAdapter 钩子 · 自动降级 | ✅ |
| **M7.5** | **Agent 运维看板** | snapshot 聚合 · ETag · 仓位/PnL/Token · US-M01 | ✅ |
| **M8** | MVP 交付 | 模拟门禁 + 通知 + 合约 Demo + Live 钩子 | ✅ |

> **产品界面：** 已由「C 端交易控制台」调整为 **[Agent 运维看板](../outline-design/架构设计/Agent运维看板设计-Bianca.md)**（监控 + 干预，非交易终端）。

---

## 待后续（P2，MVP crypto 跑通后）

| 项 | 说明 |
|----|------|
| A 股 / 美股适配 | **延后**；`MarketAdapter` 占位已就绪 |
| MarketStream 接入 Agent | ✅ 可选 `MARKET_STREAM_ENABLED` + WS 缓存 |
| analysis_reports 落库 | ✅ `GET /api/v1/analysis/reports` |
| 多 symbol Agent 端到端 | ✅ prompt / 风控 / 仓位按 Worker symbol |

---

## 验收检查表（PoC M3）

- [x] `docker compose up api` 成功，绑定 `127.0.0.1:8000`
- [x] Ollama / DeepSeek 可通过 `/health` 探测
- [x] `POST /api/v1/agent/start` 启动 Agent
- [x] `trade_logs` 表有 ≥1 BUY + ≥1 SELL，`status=filled`
- [x] 每条记录含 `decision_reason` 和 `risk_decision`
- [x] `LLM_AUTO_EXECUTE=false` 时只记录信号不下单
- [x] 日亏损超限时 Agent 拒绝新单
- [x] Checkpointer 可回放
- [x] PoC 闭环指标 `loop_closed` 可观测（**当前默认 24×7 不因闭环 stop**，见 `AGENT_STOP_ON_LOOP_CLOSED=false`）

---

## 验收检查表（运维看板 US-M01）

详见 [用户故事 US-M01](../用户故事-Bianca.md#us-m01agent-运维看板) — 已全部 ✅
