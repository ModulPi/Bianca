# Bianca — 里程碑清单

> 版本：v1.1 | 日期：2026-08-05

---

## PoC 里程碑

| 里程碑 | 名称 | 完成标准 | 状态 |
|--------|------|----------|------|
| **M0** | Demo 现货 API 连通 | 查余额、获取 BTCUSDT 行情、DeepSeek API `/health` 可达 | ✅ |
| **M1** | LLM 决策链路 | Analysis Agent 输出结构化 BUY/SELL/HOLD | ✅ |
| **M2** | 风控 + 执行 | 2 条风控规则生效；Demo 现货市价单成功 | ✅ |
| **M3** | 闭环验收 | 日志含 1 BUY + 1 SELL（filled）；Checkpointer 可回放 | ✅ |

---

## MVP 里程碑（PoC 后）

| 里程碑 | 名称 | 完成标准 | 状态 |
|--------|------|----------|------|
| **M4** | 基础设施升级 | PG + TimescaleDB + Redis 迁移完成 | ✅ |
| **M5** | 策略模板 | 网格/DCA/趋势 3 模板可运行 | ✅ |
| **M6** | 半自动 + 完整风控 | Web 确认流 + 8 条风控规则 | ✅ |
| **M6.5** | 汇总管理 | Summary API + `session_summaries`；Agent 启停自动生成会话快照 | ✅ |
| **M7** | Web 控制台 | React 前端全功能可用（含汇总仪表盘） | ✅ |
| **M8** | MVP 交付 | 模拟门禁 + Telegram 通知 + 合约 API stub | ✅ |

### M4 明细

| 子项 | 状态 | 说明 |
|------|------|------|
| 002 **兼容** DDL（`schema_mode=mvp`） | ✅ | `002_mvp_postgres_compat.sql` |
| 双栈 Docker + health 验证 | ✅ | TimescaleDB 正式镜像 + `verify_m4_stack.ps1` |
| Redis 连接 + active session 缓存 | ✅ | `agent/cache/redis_client.py` |
| JSONB 双栈 ORM（`JsonText`） | ✅ | `agent/storage/json_column.py` |
| `klines` 采集写入 | ✅ | `kline_collector` + `KlineRepository` |
| TimescaleDB 压缩 / retention / 连续聚合 | ✅ | `klines_5m` 连续聚合视图 |
| `positions` 表业务读写 | ✅ | tick/execute 同步 + `GET /api/v1/positions` |
| Checkpointer 迁 PostgreSQL | ✅ | `checkpoint/store.py` 双栈 |
| 趋势策略读 5m K 线 | ✅ | `enrich_market_with_klines()` |
| PG 集成测试 | ✅ | `pytest -m pg` 7/7（`BIANCA_PG_E2E=1`） |
| HTTP API E2E | ✅ | `verify_m4_e2e.ps1` |
| 002 **纯** schema（UUID/TIMESTAMPTZ） | ❌ | 设计见 `002_mvp_postgres.sql`（P2 生产化） |
| Live 全链路（Demo Key + 代理） | ❌ | 大陆 451 需 `BINANCE_PROXY`（用户环境） |

### M5 明细

| 子项 | 状态 | 说明 |
|------|------|------|
| 网格 / DCA / 趋势 评估器 | ✅ | `agent/strategy/{grid,dca,trend}.py` |
| 策略 CRUD + 启停 / Tick API | ✅ | `agent/api/strategy_routes.py` |
| 后台 StrategyRunner 循环 | ✅ | `agent/strategy/runner.py` |
| API 重启自动恢复 running 策略 | ✅ | `resume_strategy_runner_if_needed()` |
| 趋势策略读 5m K 线（MVP 栈） | ✅ | `enrich_market_with_klines()` |
| Web 策略页 | ✅ | `web/src/pages/StrategiesPage.tsx` |
| PG 栈策略 Tick | ✅ | `test_m4_pg_integration::test_pg_strategy_tick_hold` |
| 策略 Live E2E（真实下单） | ❌ | 需 `BINANCE_PROXY` + Demo Key |

### M6 明细

| 子项 | 状态 | 说明 |
|------|------|------|
| 8 条风控规则 | ✅ | `agent/risk/rules.py` + `test_m6_risk_rules.py` |
| 风控拒单写 `risk_events` | ✅ | `run_risk_agent()` + Telegram 通知 |
| 半自动 pending 队列 | ✅ | `queue_pending_signal()` + WebSocket |
| 确认 / 拒绝 / 超时过期 | ✅ | `confirmation/service.py` + `_expire_pending_loop` |
| 策略半自动 Tick → pending | ✅ | `execute_signal_pipeline(semi_auto)` |
| 策略确认（按 strategy_id 查 pending） | ✅ | `confirm_pending_for_strategy()` |
| Web 确认队列 | ✅ | `ConfirmQueue.tsx` + Dashboard 集成 |
| Web 风控事件页 | ✅ | `RiskPage.tsx` |
| PG 栈半自动 + 风控集成 | ✅ | `test_pg_pending_signal_confirm` 等 |
| 风控 Live E2E | ❌ | 需 Demo Key + 代理 |

### M6.5 明细

| 子项 | 状态 | 说明 |
|------|------|------|
| SessionAggregator / PnLCalculator | ✅ | `agent/summary/aggregator.py`、`pnl.py` |
| MVP 栈持仓快照（positions 表） | ✅ | `resolve_session_positions()` |
| Summary API（current/latest/sessions/daily/close） | ✅ | `agent/api/summary_routes.py` |
| Agent stop 自动固化快照 | ✅ | `runner.stop()` → `close_session()` |
| 闭环检测自动停 Agent | ✅ | `_maybe_stop_on_loop_closed()` |
| Web 仪表盘 / 会话历史 | ✅ | `DashboardPage`、`SessionsPage` |
| Telegram 会话结束通知 | ✅ | `notify_session_closed()` |
| 模拟门禁指标累计 | ✅ | `record_session_for_validation()` |
| PG 栈汇总 API | ✅ | `test_pg_session_summary_api` 等 |

### M7 明细

| 子项 | 状态 | 说明 |
|------|------|------|
| 仪表盘（Agent / 余额 / 汇总 / PnL） | ✅ | `DashboardPage.tsx` |
| 持仓页 `GET /positions` | ✅ | `PositionsPage.tsx` |
| 交易 / 会话 / 策略 / 回放 / 决策 / 风控 / Token | ✅ | 各 `pages/*` |
| 半自动确认队列 + WebSocket | ✅ | `ConfirmQueue.tsx` |
| 基础设施健康面板 | ✅ | `SystemHealthPanel.tsx`（DB/Redis/Checkpointer） |
| 模拟门禁页 | ✅ | `ValidationPage.tsx` |
| Docker 静态部署 | ✅ | `docker-compose` web 服务 |

### M8 明细

| 子项 | 状态 | 说明 |
|------|------|------|
| `paper_validations` 模拟门禁 | ✅ | `agent/validation/paper_gate.py` |
| 累计时长 / 闭环 / filled 校验 | ✅ | `evaluate_validation()` |
| demo → live 切换门禁 | ✅ | `trading/mode.py` + Agent 启动校验 |
| Telegram 通知（会话/风控/日摘要） | ✅ | `agent/notify/telegram.py` |
| 通知 API（test / daily-digest / status） | ✅ | `validation_routes.py` |
| 合约 API stub | ✅ | `GET /futures/status`（`futures_enabled=false`） |
| Web 门禁页 | ✅ | `ValidationPage.tsx` |
| PG 栈门禁 + 通知集成 | ✅ | `test_pg_m8_*` |
| 合约真实对接 | ❌ | P1 生产化 |
| Live 全链路 | ❌ | 需 Demo Key + 代理 |

---

## 待完成编码任务（按优先级）

### P1 — MVP 生产化

1. 002 纯 schema 迁移（UUID/TIMESTAMPTZ）
2. 合约 API 真实对接
3. Live 模式全链路验证（`TRADING_MODE=live` + 代理）
4. Redis 扩展（行情/K 线缓存）

### P2 — 质量与可选

1. ~~Web 端展示 `GET /positions` 持仓快照~~ ✅ M7
2. PG/M4 栈 CI 自动化
3. Ollama 冒烟

---

## 验收检查表（M4 双栈）

- [x] `docker compose -f docker-compose.yml -f docker-compose.m4.yml up` 启动 PG + Redis + API + Web
- [x] `GET /api/v1/health` → `database_backend=postgresql`、`schema_mode=mvp`、`checkpointer_backend=postgresql`
- [x] PG 11 张业务表 + 默认策略种子
- [x] TimescaleDB 正式镜像（`timescale/timescaledb:latest-pg16`）
- [x] `verify_m4_stack.ps1` / `verify_m4_e2e.ps1` 通过
- [x] `pytest -m pg` 7/7 通过（`DATABASE_URL=...@127.0.0.1:5433/bianca`）
- [x] 策略 / 半自动 / 汇总 / checkpoint PG 集成测试
- [ ] Live agent tick（需 `BINANCE_PROXY` + Demo Key）

---

## M4 快速命令

```powershell
docker compose -f docker-compose.yml -f docker-compose.m4.yml up -d --build
powershell -ExecutionPolicy Bypass -File .\scripts\verify_m4_stack.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\verify_m4_e2e.ps1

$env:BIANCA_PG_E2E = "1"
$env:DATABASE_URL = "postgresql+asyncpg://bianca:bianca@127.0.0.1:5433/bianca"
py -m pytest tests/test_m4_pg_integration.py -m pg -v
py -m pytest tests/ -m "not pg" -q
```

> **注意**：宿主机 PG 端口为 `5433`（避免与本地其他 PostgreSQL 冲突）。

---

## 相关文件

| 用途 | 路径 |
|------|------|
| PG DDL | `agent/storage/sql/002_mvp_postgres_compat.sql` |
| JSONB 双栈 | `agent/storage/json_column.py` |
| Checkpointer | `agent/checkpoint/store.py` |
| 持仓同步 | `agent/positions/sync.py` |
| M4 验证 | `scripts/verify_m4_stack.ps1`、`scripts/verify_m4_e2e.ps1` |
| PG 测试 | `tests/test_m4_pg_integration.py` |
