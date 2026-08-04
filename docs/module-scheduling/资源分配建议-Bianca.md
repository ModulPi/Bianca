# Bianca — 资源分配建议

> 版本：v0.4 | 日期：2026-08-04 | 团队：1 人

---

## PoC 阶段（已完成）

| 资源 | 分配 |
|------|------|
| **人力** | 1 名全栈开发者，100% 投入 |
| **工时** | 12 工作日（+ 3 天缓冲） |
| **硬件** | 开发机 8GB+ RAM |
| **外部服务** | 币安 Demo API Key + DeepSeek API Key |

| 模块 | 占比 | 天数 |
|------|------|------|
| 基础设施 + Docker | 17% | 2d |
| Demo 现货对接 | 17% | 2d |
| LLM Analysis Agent | 25% | 3d |
| LangGraph + 风控 + 执行 | 25% | 3d |
| Agent 循环 + API + 验收 | 17% | 2d |

---

## MVP 阶段（已完成）

| 资源 | 分配 |
|------|------|
| **人力** | 1 人全栈 |
| **工时** | ~25 工作日 |
| **硬件** | 建议 32GB RAM（Docker 全套） |
| **新增服务** | PostgreSQL、Redis、Telegram Bot |

| 模块 | 说明 |
|------|------|
| 基础设施双栈 | PG + Redis |
| 风控 + 半自动 | 8 条规则 + pending-signals |
| 汇总 + 门禁 | Summary API + 模拟验证 |
| 合约 + 通知 | U/币本位 Demo + Telegram/邮件 |
| Web | **Agent 运维看板**（非交易控制台） |

---

## Agent 重构 + 看板（2026-08，已完成）

| 模块 | 工时估算 | 说明 |
|------|----------|------|
| Agent 并行 + 降级 | ~3d | runner · degradation · markets |
| 看板 P0–P1 | ~4d | 8 模块 UI + snapshot API |
| 看板优化 | ~2d | ETag · TTL · 折叠 · invalidate |
| 文档对齐 | ~1d | PRD · 用户故事 · 里程碑 |

---

## P2 阶段（规划）

| 模块 | 说明 |
|------|------|
| A股/美股适配 | 需外部行情/券商 API |
| MarketStream | 降低 REST 轮询依赖 |
