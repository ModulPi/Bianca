# Bianca — 里程碑清单

> 版本：v0.4 | 日期：2026-08-02

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
| 002 **纯** schema（UUID/TIMESTAMPTZ/CHECK） | ❌ | 设计见 `002_mvp_postgres.sql` |
| TimescaleDB 正式镜像 + 压缩/retention/连续聚合 | ❌ | 验证环境未拉取 `timescale/timescaledb` |
| `klines` 采集写入 | ❌ | 表已建，无入库逻辑 |
| `positions` 表业务读写 | ❌ | 持仓仍从交易所 API 实时查 |
| Checkpointer 迁 PostgreSQL | ❌ | 仍为 `AsyncSqliteSaver` |
| M5–M8 在 PG 栈集成/E2E | ❌ | 仅 health/schema 验证 |

---

## 待完成编码任务（按优先级）

### P1 — M4 深化

1. TimescaleDB 正式环境（镜像 + hypertable 策略 + 压缩/retention）
2. `klines` 采集与写入服务
3. Checkpointer 迁 PostgreSQL
4. M5–M8 在 `docker-compose.m4.yml` 下的集成/E2E 测试

### P2 — MVP 生产化

1. 合约 API 真实对接（当前 `/futures/status` 为 stub）
2. Redis 扩展（行情/K 线缓存等，超越 active session）
3. Live 模式全链路验证（`TRADING_MODE=live`）
4. `positions` 表与持仓同步逻辑

### P3 — 质量与可选

1. 修复 flaky e2e：`test_agent_tick_e2e`、`test_pending_signals`（币安 Demo 网络依赖）
2. Ollama 冒烟（`LLM_PROVIDER=ollama`，宿主机 + Docker 连通）
3. PG/M4 栈 CI 自动化

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

- [x] `docker compose -f docker-compose.yml -f docker-compose.m4.yml up` 启动 PG + Redis + API + Web
- [x] `GET /api/v1/health` 返回 `database_backend=postgresql`、`schema_mode=mvp`、`redis=ok`
- [x] PG 初始化 11 张业务表（含 `positions`、`analysis_reports`、`klines`）
- [x] 默认 Agent 策略种子 `00000000-0000-4000-8000-000000000001`
- [x] Web `http://127.0.0.1:3000/` 可访问
- [ ] TimescaleDB 正式镜像验证（非 alpine 降级）
- [ ] M4 栈下策略/半自动/汇总全链路 E2E

---

## 相关文件

| 用途 | 路径 |
|------|------|
| PG 运行时 DDL | `agent/storage/sql/002_mvp_postgres_compat.sql` |
| 002 纯 schema 设计参考 | `docs/outline-design/数据库设计/sql/002_mvp_postgres.sql` |
| M4 验证脚本 | `scripts/verify_m4_stack.ps1` |
| Timescale 不可用降级 | `docker-compose.m4.verify.yml` |
