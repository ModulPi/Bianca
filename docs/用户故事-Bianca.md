# Bianca — 用户故事集

> 版本：v0.3 | 日期：2026-07-31 | 基于 PRD v0.3

---

## PoC 用户故事

### US-P01：Demo 现货 API 连通
> **作为** Bianca 开发者  
> **我想要** 通过 API 连接币安 Demo 现货并查询余额、获取行情  
> **以便** 后续 Agent 能在此环境交易  

**验收标准：**
- [ ] 使用 `demo-api.binance.com` 端点
- [ ] 能查询 Demo 账户余额
- [ ] 能获取 BTCUSDT 实时 ticker / K 线
- [ ] API Key 从 `.env` 读取，日志脱敏

**优先级：** PoC P0 | **Story Point：** 3

---

### US-P02：LLM 自主产出交易信号
> **作为** Agent 引擎  
> **我想要** 调用 DeepSeek API（或切换后的 Ollama）分析行情并产出 BUY/SELL/HOLD  
> **以便** 由 AI 自主决定买卖时机  

**验收标准：**
- [ ] 默认使用 DeepSeek API（`LLM_PROVIDER=deepseek`）
- [ ] 修改 `.env` 为 `LLM_PROVIDER=ollama` 后可切换至本地模型，无需改代码
- [ ] 输出结构化信号：`{action, symbol, amount, confidence, reason}`
- [ ] HOLD 时不触发下单
- [ ] 单次推理超时可降级为 HOLD 并记录

**优先级：** PoC P0 | **Story Point：** 8

---

### US-P03：AI 建议自动执行（可配置）
> **作为** 开发者  
> **我想要** 通过 `LLM_AUTO_EXECUTE` 控制 LLM 信号是否自动下单  
> **以便** 调试时可只看信号、验收时可自动闭环  

**验收标准：**
- [ ] `LLM_AUTO_EXECUTE=true` 时 BUY/SELL 进入风控链
- [ ] `LLM_AUTO_EXECUTE=false` 时仅记录信号，不下单
- [ ] 配置变更无需改代码，重启 API 生效

**优先级：** PoC P0 | **Story Point：** 3

---

### US-P04：最小风控拦截
> **作为** 投资者  
> **我想要** 单笔金额和日亏损有硬上限  
> **以便** Demo 环境也不会因 Agent 失控产生异常行为  

**验收标准：**
- [ ] 单笔订单金额 > `MAX_TRADE_AMOUNT` 时拒绝
- [ ] 当日累计亏损 ≥ `DAILY_LOSS_LIMIT` 时拒绝所有新单
- [ ] 拒绝原因写入 `trade_logs` 和 `risk_events`

**优先级：** PoC P0 | **Story Point：** 5

---

### US-P05：自主完成买卖闭环
> **作为** 开发者  
> **我想要** Agent 在 Demo 现货上自主完成至少 1 次买入和 1 次卖出  
> **以便** 验证 PoC 核心链路  

**验收标准：**
- [ ] 日志中存在 1 条 BUY + 1 条 SELL，均为 `filled` 状态
- [ ] 每条记录含 LLM 决策理由和风控结果
- [ ] LangGraph Checkpointer 可回放决策过程

**优先级：** PoC P0 | **Story Point：** 8

---

### US-P06：CLI 启停与查询
> **作为** 开发者  
> **我想要** 通过 curl/CLI 启动 Agent、查询状态和交易日志  
> **以便** PoC 阶段无需前端即可操作  

**验收标准：**
- [ ] `POST /api/v1/agent/start` 启动 Agent 循环
- [ ] `POST /api/v1/agent/stop` 停止
- [ ] `GET /api/v1/trades` 查看交易记录
- [ ] `GET /api/v1/agent/status` 查看运行状态

**优先级：** PoC P0 | **Story Point：** 5

---

## MVP 用户故事（PoC 后）

### US-M01：Agent 运维看板

> **作为** Agent 运维者  
> **我想要** 在浏览器看板中查看 Agent 运行态、实时行情、仓位、进行中交易、实盘信息与收益、Token 消耗  
> **以便** 24×7 监控自主交易 Agent，异常时快速介入，而无需使用交易终端  

**定位：** Agent 运行态监控（非交易平台、非 K 线看盘）。详细设计见 [Agent运维看板设计](./outline-design/架构设计/Agent运维看板设计-Bianca.md)。

**验收标准：**

- [x] 看板展示 Agent 启停、执行模式、降级状态、并行 Worker 表
- [x] 展示各 symbol 轻量实时 ticker（last/bid/ask），非 K 线终端
- [x] 展示仓位（USDT + base 资产 + 名义价值）与 PnL 四分项（已实现/未实现/现金净流入/合计）
- [x] 展示 demo/live 模式与模拟验证状态（只读）
- [x] 展示进行中交易（submitted/pending/filled）与风控拒绝
- [x] 降级/semi_auto 时展示待确认队列（WS + 确认/拒绝）
- [x] 展示 Token 用量（今日 + 当前会话）
- [x] 快捷入口：成交明细、会话汇总、决策回放

**优先级：** MVP P0 | **Story Point：** 13

---

### US-M01-legacy：Web 控制台（已 superseded）

> 原「C 端交易平台式控制台」需求已由 **US-M01 Agent 运维看板** 替代。策略商城、K 线页、密钥 Web CRUD 等不再作为产品范围。

---

### US-M06：会话汇总与复盘
> **作为** 开发者/用户  
> **我想要** Agent 一次启停周期结束后自动得到 Token、成交与盈亏汇总  
> **以便** 复盘 PoC/MVP 运行效果，无需手动查库  

**验收标准：**
- [ ] `POST /agent/stop` 后可 `GET /summary/session/latest` 获取完整快照
- [ ] 汇总含 `loop_closed`、Token 统计、PnL 分项
- [ ] 历史会话可分页查询

**优先级：** MVP P1 | **Story Point：** 5

---

### US-M02：半自动确认执行（降级兜底）
> **作为** 运维者  
> **我想要** Agent 异常降级后，在**看板**上看到待确认信号并手动 approve/reject  
> **以便** 人工介入而不中断 24×7 运行  

**验收标准：**
- [ ] WebSocket 推送 `confirmation_required` 事件
- [ ] 看板确认队列：`POST /pending-signals/{id}/confirm`
- [ ] 30 分钟超时自动丢弃
- [ ] 连续失败自动降级 semi_auto（`AUTO_DEGRADE_ENABLED`）

**优先级：** MVP P0 | **Story Point：** 8

---

### US-M03：策略模板（已移出产品范围）

> 原网格/DCA/趋势模板 UI 与 Agent 自主定位冲突，**不再作为 MVP 交付**。后端代码保留供实验，不挂载产品入口。

**状态：** 取消 | 原 Story Point：13

---

### US-M04：完整风控（8 条规则）
> **作为** 投资者  
> **我想要** 止损、回撤、仓位上限等完整保护  
> **以便** 实盘前风险可控  

**优先级：** MVP P1 | **Story Point：** 8

---

### US-M05：模拟→实盘门禁
> **作为** 投资者  
> **我想要** 模拟交易验证达标后才能开启实盘  
> **以便** 策略经过充分验证  

**优先级：** MVP P1 | **Story Point：** 5

---

## 优先级总览

| 阶段 | 故事 | Story Points |
|------|------|-------------|
| **PoC** | US-P01 ~ US-P06 | **32** |
| **MVP** | US-M01 看板 · US-M02 降级 · US-M04~M06 | ~50+ |
