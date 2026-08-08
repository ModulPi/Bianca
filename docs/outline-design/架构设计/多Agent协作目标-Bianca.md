# Bianca — 多 Agent 协作目标（方案 B）

> 版本：v1.1 | 日期：2026-08-08 | 阶段：M9 实施 | 状态：已对齐产品决策  
> 前置：M0–M8 MVP 已交付（单流水线 Supervisor + 独立 StrategyRunner）

---

## 1. 背景与问题

### 1.1 现状

| 组件 | 行为 | 问题 |
|------|------|------|
| **AgentRunner** | LangGraph 固定链：`fetch → Analysis(LLM) → Risk → Execute` | 只有 LLM 一条决策路径 |
| **StrategyRunner** | 独立后台循环，tick 所有 `running` 策略 | 与 AgentRunner **并行、互不感知** |
| **策略引擎** | 网格 / DCA / 趋势规则评估 | 逻辑成熟，但 **Agent 无法调用** |

同一 symbol 上可能出现 **LLM 买、策略卖** 的冲突；看板只能看到结果，看不到「谁做的主决策」。

### 1.2 目标一句话

> **将 Strategy 从独立 Runner 升级为 Agent 可调用的协作角色，由 Orchestrator 在同一 tick 内调度 Analysis + Strategy，聚合信号后走统一风控与执行。**

---

## 2. 产品目标

### 2.1 北星指标

| 指标 | 目标 |
|------|------|
| 决策可解释 | 每笔成交可追溯到 **Orchestrator 调度记录 + 各 Agent 原始信号 + 聚合理由** |
| 信号冲突率 | 同 symbol 同 tick 多 Agent 冲突时 **100% 有明确聚合策略**（不 silent 覆盖） |
| 架构统一 | **取消双 Runner 并行 tick**；策略仅通过协作图触发（Runner 可保留为恢复/兜底） |
| 运维可见 | 看板 Worker 行展示 **本 tick 参与的 Agent 列表与最终裁决** |

### 2.2 用户价值

- **运维者**：清楚知道「LLM 说的」和「规则策略说的」是否一致，冲突如何处理
- **开发者**：策略模板成为 Agent 能力扩展，而非第二套交易系统
- **后续**：为「自然语言下指令 → 创建/启停策略」留出 Orchestrator 入口

---

## 3. 范围边界

### 3.1 本阶段做（M9 in）

| 项 | 说明 |
|----|------|
| **Orchestrator Agent** | 每个 tick 决定启用 Analysis / Strategy / 跳过 |
| **Analysis Agent** | 复用现有 `run_analysis_agent`，输出结构化信号 |
| **Strategy Agent** | 封装 `evaluate_grid/dca/trend` 为 Tool，按 symbol/策略配置评估 |
| **Merge 节点** | 多信号聚合为 **单一** `llm_signal`（含 `sources[]` 元数据） |
| **统一下游** | Merge → Risk → Execute（半自动 pending 路径不变） |
| **Checkpointer** | 每个 Agent 子步骤写入 checkpoint，支持回放 |
| **配置** | `.env` / DB 配置：主从模式、冲突策略、启用哪些 Agent |
| **看板** | Worker 表增加「参与 Agent / 聚合结果」只读展示 |
| **测试** | 单测（聚合逻辑）+ PG 集成（协作 tick HOLD/BUY/SELL） |

### 3.2 本阶段不做（out）

| 项 | 延后 |
|----|------|
| 自然语言 Command Agent | ~~M10~~ **M9 已纳入** |
| 合约 / 多交易所 | P1 生产化 |
| A 股 / 美股 Agent | P2 |
| 策略参数 Web 编辑器 | 不做（仍 API/DB） |
| 多 LLM 模型互辩 | 不做 |

---

## 4. 目标架构

### 4.1 LangGraph 拓扑（目标态）

```
START
  → fetch_market
  → orchestrator          # 读配置 + 行情摘要，决定本 tick 启用哪些 Agent
  → [parallel 或 sequential]
       ├─ analysis_agent   # LLM → TradeSignal
       └─ strategy_agent   # evaluate_* tools → TradeSignal
  → merge_signals         # 聚合 → 单一 TradeSignal + merge_reason + sources
  → route (HOLD / semi_auto / risk)
  → risk → execute → END
```

### 4.2 Agent 角色定义

| Agent | 输入 | 输出 | 失败行为 |
|-------|------|------|----------|
| **Orchestrator** | market_data, settings, running_strategies | `{use_analysis, use_strategy, strategy_ids[]}` | 默认仅 Analysis |
| **Analysis** | market_data | `TradeSignal` + decision_id | HOLD + 记录错误 |
| **Strategy** | market_data, strategy row | `TradeSignal` + strategy_id | HOLD + 记录错误 |
| **Merge** | Signal[] | 单一 `TradeSignal` | 冲突 → HOLD 或按策略降级 |
| **Risk / Execute** | 现有实现 | 不变 | 不变 |

### 4.3 信号聚合策略（默认，可配置）

| 模式 | 配置键 | 行为 |
|------|--------|------|
| **LLM 主** | `SIGNAL_MERGE_MODE=llm_primary` | LLM 非 HOLD 时采用 LLM；否则采用 Strategy |
| **策略主** | `strategy_primary` | 策略有 actionable 信号时优先策略 |
| **一致才动** | `consensus` | 两者同向（都 BUY 或都 SELL）才执行，否则 HOLD |
| **保守取弱** | `min_confidence` | 取 confidence 较低者，或冲突则 HOLD |

默认：**`llm_primary`（AI 为主；AI 非 HOLD 时采用 AI，冲突听 AI）**。

### 3.1 产品决策（2026-08-08）

| 项 | 决策 |
|----|------|
| 交易对 | BTC + ETH 并行（`AGENT_SYMBOLS=BTCUSDT,ETHUSDT`） |
| 环境 | 仅币安模拟盘 |
| 合并模式 | `llm_primary` |
| 策略范围 | 趋势必做；网格/DCA 延后 |
| StrategyRunner | 默认不自启（`STRATEGY_RUNNER_AUTO_START=false`） |
| 聊天指挥 | M9 纳入（`POST /api/v1/agent/chat`） |
| 看板 | 三栏详情 + checkpoint 回放 + Worker 参与 Agent |
| 登录 | `API_TOKEN` 简单密码 |

---

## 5. Strategy 工具化（取代独立 Runner）

### 5.1 Tool 清单

| Tool | 作用 |
|------|------|
| `list_running_strategies(symbol?)` | 返回 running 策略列表 |
| `evaluate_strategy(strategy_id)` | 单次评估，不执行 |
| `evaluate_trend(symbol, params?)` | 无持久化策略的趋势评估 |
| `evaluate_grid(symbol, params?)` | 网格评估 |
| `evaluate_dca(symbol, params?)` | DCA 评估 |

Strategy Agent 节点内部调用上述 Tool；**不**在 tick 外独立循环（`StrategyRunner` 默认关闭，仅 API 显式 `start` 时作兼容兜底）。

### 5.2 与现有代码映射

| 现有 | 目标 |
|------|------|
| `agent/strategy/runner.py` | 降级为可选兜底；默认不随 API 启动 |
| `agent/strategy/engine.py` | 核心逻辑复用，`evaluate_*` + `execute_signal_pipeline` |
| `agent/graph/supervisor.py` | 扩展为协作图入口 |
| `agent/graph/analysis_agent.py` | 拆为独立节点，逻辑不变 |

---

## 6. 数据与可观测性

### 6.1 状态扩展（TradeState）

```python
# 新增字段（示意）
orchestrator_plan: dict          # 本 tick 调度计划
agent_signals: list[dict]       # [{agent, signal, raw_reason}]
merge_meta: dict                # {mode, conflict, winner, reason}
```

### 6.2 持久化

| 数据 | 存储 |
|------|------|
| 各 Agent 原始信号 | `decision_logs` 扩展字段或 `agent_signal_logs` 新表 |
| 聚合结果 | 写入现有 `trade_logs.decision_reason` 前缀 `[merge:consensus]` |
| Checkpoint | 每节点快照，thread_id 仍为 `{session_id}:{symbol}` |

### 6.3 看板（M9 最小）

| 展示 | 数据源 |
|------|--------|
| Worker 行「参与 Agent」 | `agent/status` 扩展 `last_agents[]` |
| 决策回放 | checkpoint 中 `orchestrator_plan` + `agent_signals` |
| 冲突标记 | merge_meta.conflict=true 时 Worker 行黄色 |

---

## 7. 验收标准

### 7.1 功能验收

- [ ] 单 symbol tick 内，Orchestrator 可仅开 Analysis、仅开 Strategy、或两者皆开
- [ ] 两者皆开且信号冲突时，按 `SIGNAL_MERGE_MODE` 产出确定结果，**无双重下单**
- [ ] `StrategyRunner` 默认不在 API 启动时自动运行；协作图可独立完成策略信号 → 成交
- [ ] Demo 现货完成 **1 BUY + 1 SELL filled**，且 `decision_logs` 可看到多 Agent 来源
- [ ] 半自动 / 降级路径与现网行为一致（pending → confirm → risk → execute）
- [ ] PG 栈 pytest 新增 `test_multi_agent_collaboration_*` ≥ 3 条通过

### 7.2 非功能验收

- [ ] 单 tick 延迟：相对现网 Analysis-only **增加 ≤ 2s**（Strategy 评估为本地规则）
- [ ] Token 消耗：Orchestrator 轻量调用 **≤ 500 tokens/tick**（或规则路由零 Token 模式可配置）
- [ ] 向后兼容：`EXECUTION_MODE` / 风控 8 条 / Worker 多 symbol 行为不退化

---

## 8. 里程碑拆分（M9）

| 子项 | 交付 | 估时 |
|------|------|------|
| **M9.1** | Tool 层 + Strategy Agent 节点（不并入主图） | 3d |
| **M9.2** | Merge 节点 + 聚合策略配置 | 2d |
| **M9.3** | Orchestrator + 协作图替换 `build_trade_graph` | 3d |
| **M9.4** | 停默认 StrategyRunner + API/看板扩展 | 2d |
| **M9.5** | 测试 + Demo 闭环验收 | 2d |

**合计：约 12 工作日（2–2.5 周）**

---

## 9. 风险与缓解

| 风险 | 缓解 |
|------|------|
| LLM Orchestrator 不稳定 | 提供 **规则 Orchestrator** 模式（按配置固定启用 Agent，零 LLM） |
| 聚合逻辑难测 | 聚合纯函数 + 表驱动单测，与 LangGraph 解耦 |
| 延迟增加 | Strategy 评估本地计算；Orchestrator 可缓存 plan |
| 回放兼容性 | 新 checkpoint 字段 optional，旧 thread 仍可回放 |

---

## 10. 相关文档

| 文档 | 关系 |
|------|------|
| [架构设计文档-Bianca.md](./架构设计文档-Bianca.md) | C4 / 现网 Supervisor 拓扑 |
| [Agent运维看板设计-Bianca.md](./Agent运维看板设计-Bianca.md) | M9 看板扩展 |
| [里程碑清单-Bianca.md](../../module-scheduling/里程碑清单-Bianca.md) | 追加 M9 条目 |
| [PRD-Bianca.md](../../PRD-Bianca.md) | 产品定位不变 |

---

## 11. 成功态示意

**一次 tick 的期望日志：**

```
orchestrator: use_analysis=true, use_strategy=true, strategies=[trend-btc]
analysis:     BUY BTCUSDT conf=0.72 "突破短期均线"
strategy:     HOLD BTCUSDT conf=0.55 "趋势未确认"
merge:        HOLD (consensus conflict) "LLM BUY vs Strategy HOLD"
risk:         skipped
status:       signal_only
```

**一次一致成交的期望日志：**

```
orchestrator: use_analysis=true, use_strategy=true
analysis:     BUY BTCUSDT conf=0.81
strategy:     BUY BTCUSDT conf=0.76
merge:        BUY BTCUSDT conf=0.78 "consensus BUY"
risk:         approved
execute:      filled
```

---

> **下一步：** 评审本目标稿 → 在里程碑清单登记 M9 → 从 M9.1 Tool 层开始实现。
