# Bianca — 开发排期计划

> 版本：v0.4 | 日期：2026-08-04 | 团队：1 人（独立开发）

---

## 项目信息

| 项目 | 内容 |
|------|------|
| 项目名称 | Bianca |
| **当前定位** | **自主交易 Agent 引擎** + **运维看板**（非交易平台） |
| PoC 目标 | Demo 现货 LLM 自主完成 1 买 1 卖闭环 |
| MVP 目标 | 24×7 多 Worker · 完整风控 · 降级人工介入 · 运维看板 |
| 团队人数 | 1 人 |
| PoC 工期 | 12 工作日（已完成） |
| MVP + 看板 | ~25 + 10 工作日（已完成） |

---

## PoC 排期（12 天）— 已完成

| 阶段 | 名称 | 里程碑 |
|------|------|--------|
| P0 | 基础设施 | — |
| P1 | Demo 现货 API | M0 |
| P2 | LLM Analysis Agent | M1 |
| P3 | 风控 + 执行 + LangGraph | M2 |
| P4 | Agent 循环 + API + 验收 | M3 |

```
P0(2d) → P1(2d) → P2(3d) → P3(3d) → P4(2d) = 12d
         M0        M1        M2        M3
```

---

## MVP 排期（PoC 后）— 已完成

| 阶段 | 名称 | 里程碑 | 状态 |
|------|------|--------|------|
| M4 | PG + Redis 双栈 | 基础设施 | ✅ |
| M5 | 策略模板引擎 | 实验代码 | ✅ 无产品入口 |
| M6 | 8 条风控 + 半自动确认 | 风控 + 降级 | ✅ |
| M6.5 | Summary 模块 | 会话汇总 | ✅ |
| M7 | 合约 + 模拟→实盘门禁 | 全品种钩子 | ✅ |
| M8 | 通知 + Live 钩子 | MVP 交付 | ✅ |

---

## Agent 重构 + 运维看板（2026-08）— 已完成

| 阶段 | 内容 | 产出 | 状态 |
|------|------|------|------|
| **R1** | 产品定位对齐 | 砍交易平台 UI；多 Worker；24×7；MarketAdapter | ✅ |
| **R2** | 看板 P0 | 单页 A–H 模块（复用 API） | ✅ |
| **R3** | 看板 P1 | `/dashboard/snapshot` · 批量 ticker · Worker Token | ✅ |
| **R4** | 看板 P1+ | ETag/304 · 分层 TTL · invalidate · 可折叠 | ✅ |
| **R5** | 看板 P2 | 多 symbol 仓位 · 市场字段（A股/美股钩子） | ✅ |

设计详见 [Agent运维看板设计](../outline-design/架构设计/Agent运维看板设计-Bianca.md)。

---

## 后续排期（P2，MVP 跑通后再做）

> **A 股 / 美股**：等 crypto MVP 全链路稳定验收后再启动，当前仅占位钩子。

| 阶段 | 内容 | 优先级 |
|------|------|--------|
| P2.1 | A 股 `MarketAdapter` 行情 + 下单 | 延后 |
| P2.2 | 美股 `MarketAdapter` 行情 + 下单 | 延后 |
| P2.3 | MarketStream 接入 Agent tick | 低 |
| P2.4 | analysis_reports 落库 | 低 |

---

## 风险与缓冲

| 风险 | 缓解 |
|------|------|
| LLM 输出不稳定 | 结构化 Prompt + 解析失败默认 HOLD |
| Demo API 限流 | 降低 tick 间隔；snapshot 分层 TTL + ETag |
| 24×7 连续失败 | `AUTO_DEGRADE_ENABLED` → semi_auto + 看板确认 |
| 看板轮询压力 | snapshot 聚合 + 304 + 交易所数据缓存 |
