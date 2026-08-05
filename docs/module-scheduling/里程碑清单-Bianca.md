# Bianca — 里程碑清单

> 版本：v0.5 | 日期：2026-08-05

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
| **M4** | 基础设施升级 | PG + TimescaleDB + Redis 迁移完成 | 🟡 部分完成 |
| **M5** | 策略模板 | 网格/DCA/趋势 3 模板可运行 | ✅ PoC SQLite |
| **M6** | 半自动 + 完整风控 | Web 确认流 + 8 条风控规则 | ✅ PoC SQLite |
| **M6.5** | 汇总管理 | Summary API + `session_summaries`；Agent 启停自动生成会话快照 | ✅ PoC SQLite |
| **M7** | Web 控制台 | React 前端全功能可用（含汇总仪表盘） | ✅ PoC |
| **M8** | MVP 交付 | 模拟门禁 + Telegram 通知 + 合约 API stub | ✅ PoC SQLite（合约 stub） |

### M4 明细

| 子项 | 状态 | 说明 |
|------|------|------|
| 002 **兼容** DDL（`schema_mode=mvp`） | ✅ | `agent/storage/sql/002_mvp_postgres_compat.sql` |
| 双栈 Docker + health 验证 | ✅ | `scripts/verify_m4_stack.ps1`；无 Timescale 镜像时降级 `postgres:16-alpine` |
| Redis 连接 + active session 缓存 | ✅ | `agent/cache/redis_client.py` |
| JSONB 字段读取兼容 | ✅ | `parse_json_field()` 全链路 |
| `klines` 采集写入 | ✅ | `agent/market/kline_collector.py` + `KlineRepository` |
| `positions` 表业务读写 | ✅ | `PositionRepository` + tick/execute 后同步；`GET /api/v1/positions` |
| Checkpointer 迁 PostgreSQL | ✅ | `agent/checkpoint/store.py` 双栈（PG / SQLite） |
| TimescaleDB 压缩 / retention | ✅ | 写入 002 DDL（扩展可用时生效） |
| pytest 稳定化（隔离 DB + mock 持仓同步） | ✅ | `tests/conftest.py`；66 passed |
| PG 集成测试脚手架 | ✅ | `tests/test_m4_pg_integration.py`（`-m pg`，需 `BIANCA_PG_E2E=1`） |
| HTTP API E2E 脚本 | ✅ | `scripts/verify_m4_e2e.ps1`（无需 Binance Key） |
| 002 **纯** schema（UUID/TIMESTAMPTZ/CHECK） | ❌ | 设计见 `002_mvp_postgres.sql` |
| TimescaleDB 连续聚合（如 1m→5m） | ❌ | 待写入 DDL + 查询层 |
| TimescaleDB 正式镜像现场验证 | ❌ | 需拉取 `timescale/timescaledb:latest-pg16` |
| M5–M8 PG 栈 Live E2E | ❌ | 需 Demo Key + 代理 + LLM；脚本 `-Live` 已预留 |

---

## 待完成编码任务（按优先级）

### P1 — M4 收尾

1. TimescaleDB 连续聚合 + 趋势策略可选读聚合 K 线
2. `verify_m4_stack.ps1` / `verify_m4_e2e.ps1` 现场跑通并勾选验收项
3. M5–M8 在 PG 栈 Live E2E（策略 tick、半自动 confirm、session summary）

### P2 — MVP 生产化

1. 合约 API 真实对接（当前 `/futures/status` 为 stub）
2. Redis 扩展（行情/K 线缓存，超越 active session）
3. Live 模式全链路验证（`TRADING_MODE=live`）
4. 002 纯 schema 迁移（UUID/TIMESTAMPTZ）

### P3 — 质量与可选

1. Ollama 冒烟（`LLM_PROVIDER=ollama`，宿主机 + Docker 连通）
2. PG/M4 栈 CI 自动化（GitLab job + `BIANCA_PG_E2E`）
3. Web 端展示 `GET /positions` 持仓快照

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

---

## 验收检查表（M4 双栈）

### 基础设施（代码已就绪，待 Docker 现场验证）

- [ ] `docker compose -f docker-compose.yml -f docker-compose.m4.yml up` 启动 PG + Redis + API + Web
- [ ] `GET /api/v1/health` → `database_backend=postgresql`、`schema_mode=mvp`、`redis=ok`、`checkpointer_backend=postgresql`
- [ ] PG 初始化 11 张业务表（含 `positions`、`analysis_reports`、`klines`）
- [ ] 默认 Agent 策略种子 `00000000-0000-4000-8000-000000000001`
- [ ] Web `http://127.0.0.1:3000/` 可访问
- [ ] TimescaleDB 正式镜像（非 `postgres:16-alpine` 降级）

### 业务链路（MVP 栈）

- [x] `GET /api/v1/positions` 可读持仓快照（代码 + pytest mock 栈）
- [x] Checkpointer PostgreSQL 双栈（`checkpoint/store.py`）
- [ ] `scripts/verify_m4_e2e.ps1` 全绿
- [ ] `BIANCA_PG_E2E=1` 下 `pytest -m pg` 全绿
- [ ] M4 栈下策略 / 半自动 / 汇总 Live 全链路

---

## 相关文件

| 用途 | 路径 |
|------|------|
| PG 运行时 DDL | `agent/storage/sql/002_mvp_postgres_compat.sql` |
| Checkpointer 工厂 | `agent/checkpoint/store.py` |
| 持仓同步 | `agent/positions/sync.py` |
| 002 纯 schema 设计参考 | `docs/outline-design/数据库设计/sql/002_mvp_postgres.sql` |
| M4 基础设施验证 | `scripts/verify_m4_stack.ps1` |
| M4 API E2E | `scripts/verify_m4_e2e.ps1` |
| PG pytest 集成 | `tests/test_m4_pg_integration.py`（`-m pg`） |
| Timescale 不可用降级 | `docker-compose.m4.verify.yml` |
| Docker M4 叠加 | `docker-compose.m4.yml` |

---

## M4 快速命令

```powershell
# 1. 启动 M4 栈
docker compose -f docker-compose.yml -f docker-compose.m4.yml up -d --build

# 2. 基础设施验收
powershell -ExecutionPolicy Bypass -File .\scripts\verify_m4_stack.ps1

# 3. API E2E（无需 Binance Key）
powershell -ExecutionPolicy Bypass -File .\scripts\verify_m4_e2e.ps1

# 4. PG pytest（宿主机，PG 暴露 5432）
$env:BIANCA_PG_E2E = "1"
$env:DATABASE_URL = "postgresql+asyncpg://bianca:bianca@127.0.0.1:5432/bianca"
py -m pytest tests/test_m4_pg_integration.py -m pg -v
```
