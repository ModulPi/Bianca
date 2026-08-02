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

### US-M01：Web 控制台
> **作为** 用户  
> **我想要** 在浏览器中查看持仓、收益、Agent 状态和 **会话汇总（Token 消耗 + 盈亏 + 闭环）**  
> **以便** 不用命令行操作  

**验收标准：**
- [ ] 首页展示 `/summary/session/current` 或最近一次会话
- [ ] 盈亏区分已实现 / 未实现 / 现金净流入
- [ ] 展示 LLM Token 消耗与闭环状态徽章

**优先级：** MVP P1 | **Story Point：** 13

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

### US-M02：半自动确认执行
> **作为** 用户  
> **我想要** LLM/策略信号推送到 Web，手动确认后才下单  
> **以便** 保留人工把关  

**验收标准：**
- [ ] WebSocket 推送 `confirmation_required` 事件
- [ ] `POST /strategies/{id}/confirm` 确认执行
- [ ] 30 分钟超时自动丢弃

**优先级：** MVP P1 | **Story Point：** 8

---

### US-M03：策略模板（网格/DCA/趋势）
> **作为** 投资者  
> **我想要** 选择预设策略模板并配置参数  
> **以便** 不依赖 LLM 也能自动交易  

**优先级：** MVP P1 | **Story Point：** 13

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
| **MVP** | US-M01 ~ US-M05 + 合约/通知等 | ~80+ |
