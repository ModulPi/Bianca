# Bianca — 前端控制台设计文档

> 版本：v0.1 | 日期：2026-10-18 | 模块代号：Console | 阶段：F
> 状态：**F-0、F-1 已完成；F-2 页面已落地并通过真实数据接口核对；F-3 未做**
> 关联：[经理 Agent 与对话层设计 v0.27](经理Agent与对话层设计-Bianca.md) · [行情数据模块设计 v0.9](行情数据模块设计-Bianca.md) · [PRD](../../PRD-Bianca.md)

---

## 1. 定位与现状

### 1.1 控制台是什么

控制台是系统**面向人的读取面**：

- 看**现在**：权益、持仓、agent 是否在跑、行情数据面是否在流；
- 看**过去**：每一笔交易、每一张订单回执、每一次决策、每一条风控事件；
- 看**agent 说了什么**：目标、复述、对话消息、计划、规划失败的原始记录。

它**不生产交易决策**。唯一的写入动作是对话层（表达/修改目标、确认、规划）以及既有的显式运维动作（启动/停止、采集快照、重置回撤、回补行情）。订单只能由 agent 的执行链路产生，前端没有下单入口 —— 这是 ADR-018（完整权限交给 agent）在界面上的体现。

### 1.2 现状缺口

当前后端（`/api/v1`）已有 26 个端点，交易、订单、组合、风控、决策、用量、行情状态都能读到。但**读不全**：

| 缺口 | 后果 |
|------|------|
| 目标 / 消息 / 计划没有任何读端点 | 对话记录只能在响应里看当轮，重启或换目标后看不到历史 |
| 交易/订单/决策详情缺归因字段 | 看得到"卖了 0.00449 BTC"，看不到"当时依据什么、属于哪个目标、用的哪份 prompt" |
| 列表 `total = len(items)` | 翻页会骗人：永远显示"共 50 条" |
| 列表无 `offset` / 时间过滤 | 只能看最近 N 条，无法按日期回看 |
| 无 `/market/klines` | 行情库里 478 万根 K 线在界面上不可见 |
| 无权益历史端点 | PnL 曲线画不出来 |

### 1.3 已有前端在哪儿

**当前分支里没有前端。** 四个月前的那条代码线（commit `471714a`，`frontend/`）有一个完整的控制台，但它不是当前代码的祖先，对接的是另一套后端：

| 旧前端的假设 | 当前后端的事实 |
|---|---|
| PG + DDD `backend/`、sessions / checkpoints / strategies | SQLite + `agent/`、goals / messages / plans |
| `GET /dashboard/snapshot` 聚合端点、ETag、WebSocket 事件 | 无聚合端点、无 WS、逐端点轮询 |
| Bearer token + `/login` | 无鉴权（API 只绑 `127.0.0.1`） |
| `POST /agent/chat`、`/summary/*`、`/positions`、`/validation` | `POST /dialogue/*`、`/portfolio`、无门禁体系 |
| `GET /market/klines` | 无此端点 |

**结论：回收它的骨架，重接当前 API。** 不从零选栈，也不假装它能直接跑。

### 1.4 旧页面处置

| 旧页面 | 处置 |
|---|---|
| `DashboardPage` 运维看板 | **保留**，改为逐端点轮询当前 API |
| `ChatPage` | **改造**为「对话 / 目标」页（`/dialogue/*`） |
| `TradesPage` + `TradesTable` | **保留**，补筛选、分页、详情 |
| `PositionsPage` | **保留**（`/portfolio`），补权益曲线 |
| `DecisionsPage` + `DecisionsTable` | **保留**，补详情与 goal/plan 过滤 |
| `RiskPage` + `RiskEventsTable` | **保留**，补 event_type 过滤与重置回撤 |
| `UsagePage` + `TokenUsagePanel` | **保留** |
| `SessionsPage` / `SessionDetailPage` | **改造**为「目标列表 / 目标详情」（goals + messages） |
| `CheckpointsPage` / `CheckpointTimeline` | **改造**为「计划与规划记录」（plans） |
| `StrategiesPage` | **删除**（当前无策略实体；计划在目标详情页里） |
| `ValidationPage` | **删除**（当前无门禁体系） |
| `LoginPage` / `RequireAuth` / `api/token.ts` | **删除**（无鉴权） |
| `DashboardPage` 里的 `useSystemWebSocket` | **删除**（无事件面），用轮询 |
| `KlineChartPanel` | **保留**，手写 SVG 不引图表库 |

---

## 2. 设计目标与非目标

**目标**

1. 交易明细、历史交易、agent 对话记录三类数据在浏览器里完整可见，可筛选、可翻页、可看单条依据。
2. 运维态（agent/行情数据面/权益/持仓/风控）一屏可判。
3. 每个界面上的数字都能追到后端的哪张表；空值显示"未知"，不显示 0。
4. 以最小改动复用旧骨架，不引入新的 UI 框架、图表库或状态管理库。

**非目标**

- 不做多用户、权限、登录、审计登录。
- 不做移动端适配（桌面 1280px 起）。
- 不做实时推送（无 WS 事件面）；轮询间隔按页面区分。
- 不做下单入口、K 线技术指标计算、回测界面。
- 不把 `llm_auto_execute` 之类的开关搬进 UI（改配置仍走 `.env`；UI 只显示状态）。

---

## 3. 信息架构

| 路由 | 页面 | 数据来源 | 轮询 |
|---|---|---|---|
| `/` | 看板 | `/health` `/agent/status` `/portfolio` `/usage` `/risk/events?limit=5` | 5s / 10s |
| `/dialogue` | 对话 · 当前目标 | `/dialogue/current`、`start`、`say`、`confirm`、`plan` | 3s（只拉当前） |
| `/goals` | 目标列表（历史会话） | `/goals?limit&offset&status` | 15s |
| `/goals/:id` | 目标详情 · 对话记录 · 计划 | `/goals/{id}`、`/goals/{id}/messages`、`/goals/{id}/plans`、`/plans/{id}` | 10s |
| `/trades` | 交易明细 · 历史交易 | `/trades`、`/trades/{id}`、`/orders` | 10s |
| `/positions` | 持仓 · 权益曲线 | `/portfolio`、`/equity/history` | 10s |
| `/decisions` | 决策回放 | `/decisions`、`/decisions/{id}` | 15s |
| `/risk` | 风控事件 | `/risk/events`、`POST /risk/reset-drawdown` | 10s |
| `/market` | 行情 · K 线 · 数据面 | `/market/status`、`/market/klines`、`POST /market/backfill` | 30s |
| `/usage` | Token 用量 | `/usage` | 30s |

侧栏分组：**看板** · **对话**（对话/目标/计划）· **账本**（交易/持仓/决策）· **风控** · **数据**（行情/用量）。

---

## 4. 后端补口（只读）

### 4.1 既有端点的三个毛病

1. **`total` 不是总数。** 全部列表端点返回 `total = len(items)`，而 `items` 已被 `limit` 截断。加了 `offset` 之后它会变成"本页条数"，彻底失去意义。→ 改为同过滤条件下的 SQL `COUNT(*)`。
2. **只能看最近 N 条。** 没有 `offset`、没有 `since` / `until`，历史交易无法按日期回看。
3. **详情缺归因。** `trade_logs` 里的 `context` / `equity` / `positions` / `goal_id` / `plan_id`，`decision_logs` 里的 `raw_output` / `prompt` / `market_data` / `plan_step`，`orders` 里的 `fee` / `raw_response` —— 都已落库，但 DTO 没带出来。

### 4.2 新增 / 扩展端点

统一约定：`limit` 1–200（默认 50）、`offset ≥ 0`（默认 0）、时间过滤用 ISO 8601 字符串 `since` / `until`（含端点）。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/goals` | 目标列表，含 `status` 过滤 |
| GET | `/goals/{id}` | 目标详情：原话、解析结果、复述、各维度、风控快照、时间线 |
| GET | `/goals/{id}/messages` | 该目标的全部消息（`limit` / `offset`） |
| GET | `/goals/{id}/plans` | 该目标的所有规划尝试（成功与失败都返回） |
| GET | `/plans/{id}` | 单次规划详情：`plan` JSON、`plan_shape`、`final_text`、`invocations`、`usage`、`errors`；`transcript` 按需返回（可加 `include_transcript=1`） |
| GET | `/decisions/{id}` | 决策详情：`raw_output`、`prompt`、`market_data`、`plan_step`、`goal_id`、`plan_id` |
| GET | `/decisions` | 扩展：`offset`、`since`、`until`、`goal_id`、`plan_id` |
| GET | `/trades` | 扩展：`offset`、`since`、`until`、`goal_id`；`total` 改真实计数 |
| GET | `/trades/{id}` | 扩展：`context`、`equity`、`positions`、`goal_id`、`plan_id` |
| GET | `/orders` | 扩展：`offset`、`since`、`until`、`symbol`、`side`；DTO 补 `fee` |
| GET | `/orders/{id}` | 新增：单张订单详情，含 `raw_response` |
| GET | `/risk/events` | 扩展：`offset`、`since`、`until`、`event_type` |
| GET | `/equity/history` | 权益快照序列（时间正序），用于 PnL 曲线 |
| GET | `/market/klines` | 从 `market.db` 读 K 线：`symbol`、`interval`、`limit`（≤1000）、`end`（epoch ms，可选） |

### 4.3 口径（不变量）

- **空 ≠ 0。** 没有快照返回 404 或空数组；取不到价返回 `null` 并保留在 `unpriced` 里。前端同样不得把 `null` 渲染成 `0`。
- **`total` 与 `items` 必须同源同过滤。** 翻页时 `total` 不随页变化。
- **失败也返回。** 规划失败（`plan = null`）、订单失败（`error`）、被风控拒绝的交易（`status = rejected`）都必须在列表里出现 —— 只显示成功的记录是不真的。
- **`context` / `prompt` / `transcript` 是可能很大的文本。** 列表不返回，只在详情返回；`transcript` 再加一层开关。

---

## 5. 前端结构

### 5.1 技术栈（沿用旧骨架，不升级）

- React 19 + TypeScript + Vite + Tailwind CSS 4 + react-router 7
- 无 UI 组件库、无图表库、无 Redux/React Query；轮询用自写的 `usePolling`
- 暗色主题沿用旧 `index.css` 与 zinc/amber 配色

### 5.2 目录

```
frontend/
  index.html
  package.json
  vite.config.ts          # dev server 127.0.0.1:3001，/api → 127.0.0.1:8000
  tsconfig.json
  src/
    main.tsx
    App.tsx               # 路由；无登录、无守卫
    index.css
    api/
      client.ts           # 全部端点；统一错误类型
    types/
      api.ts              # 与后端 schema 一一对应
    hooks/
      usePolling.ts       # 保留
      useKlinePolling.ts  # 保留
    components/
      Layout.tsx          # 侧栏分组导航
      StatCard.tsx
      CollapsibleSection.tsx
      TradesTable.tsx     # 保留并扩展
      PositionsTable.tsx
      PnLChart.tsx        # 改成读 /equity/history
      KlineChartPanel.tsx # 保留，改 KlineItem 类型
      RiskEventsTable.tsx
      DecisionsTable.tsx
      TokenUsagePanel.tsx
      DialoguePanel.tsx   # 新增：消息流 + 输入框 + 确认/规划按钮
      GoalTimeline.tsx    # 新增：目标状态时间线
    pages/
      DashboardPage.tsx
      DialoguePage.tsx
      GoalsPage.tsx
      GoalDetailPage.tsx
      TradesPage.tsx
      PositionsPage.tsx
      DecisionsPage.tsx
      RiskPage.tsx
      MarketPage.tsx
      UsagePage.tsx
```

### 5.3 复用与丢弃

**原样复用**：`usePolling`、`CollapsibleSection`、`StatCard`、`TradesTable` 的表格结构与配色、`PnLChart`/`KlineChartPanel` 的 SVG 画法、`Layout` 的侧栏样式。

**改造**：`api/client.ts` 重写（旧端点半数不存在）、`types/api.ts` 重写、`Layout` 换导航分组并去掉登录、`DashboardPage` 去掉 WS 与聚合快照、`PnLPanel`/`PnLChart` 接 `/equity/history`。

**丢弃**：`LoginPage`、`RequireAuth`、`api/token.ts`、`useSystemWebSocket`、`useSnapshotPolling`、`SessionSummaryPanel`、`ConfirmQueue`、`WorkerStatusPanel`、`TradingModePanel`、`ExecutionModeBanner`、`LoopClosedBadge`、`BalancePanel`（并入持仓页）、`ActiveTradesSection`、`CheckpointTimeline`、`CheckpointsPage`、`SessionsPage`、`SessionDetailPage`、`StrategiesPage`、`ValidationPage`。

### 5.4 展示规则

- 时间：后端存 UTC ISO 串，前端 `new Date().toLocaleString()` 本地化；详情里同时给出原始 ISO。
- 金额：USDT 保留 2–4 位，币数量保留 8 位（与交易对精度无关，展示用）。
- 状态色：`filled` 绿、`failed`/`rejected` 红、`signal_only`/`pending` 灰；`risk_decision=manual_close` 用琥珀色，与 `approved` 区分。
- 空态文案说明"为什么空"，不写"暂无数据"了事：如"没有 active 目标，先在对话页说一句你想做什么"。

---

## 6. 部署与安全

| 场景 | 方式 |
|---|---|
| 开发 | `npm run dev` → `http://127.0.0.1:3001`，Vite 代理 `/api` 到 `127.0.0.1:8000` |
| 构建 | `npm run build` → `frontend/dist/` |
| 生产（F-3） | FastAPI 在 `frontend/dist` 存在时挂载静态文件；`/api` 照旧 |

**安全前提（必须写在代码里，不只写在文档里）**：

- API **没有鉴权**，因此服务只绑 `127.0.0.1`。控制台一旦对外暴露，等于把账户余额和交易历史公开。
- 前端不保存、不注入任何密钥；`.env` 永不进入 `dist`。
- 静态挂载只读，不提供目录列表；SPA 路由回退只对非 `/api` 路径生效。

---

## 7. 已确认决策（ADR）

### ADR-F01：回收历史前端骨架，不从零新建

`471714a` 的 `frontend/` 有可用的布局、轮询、表格、手写 K 线图。重接 API 的成本远小于重写，且视觉与交互已经验证过。**代价**：需要删除约一半与旧后端绑定的页面/组件，且旧依赖版本较新（React 19 / Vite 8），要确认本机可安装。

### ADR-F02：不新增聚合快照端点，先逐端点轮询

旧前端的 `/dashboard/snapshot` 把十几个数据源拼成一个响应并做 ETag。当前后端没有这个端点，现在加会把"页面布局"固化进后端。**先**让每个页面轮询它真正需要的 2–4 个端点；若实测请求过多或出现明显不一致，再考虑聚合。

### ADR-F03：列表 `total` 必须是数据库计数

`total = len(items)` 在加上 `offset` 后必然说谎。所有列表端点改为同过滤条件下的 `COUNT(*)`，并在测试里钉住"第 2 页的 total 与第 1 页相同"。

### ADR-F04：前端只读，写入只经过既有显式动作

界面不提供下单、改风控阈值、改配置的入口。允许的写操作仅限：对话（表达/确认/规划目标）、启动/停止 agent、采集持仓快照、重置回撤基准（必须填理由）、触发行情回补。这些动作在后端都已存在且有留痕。

---

## 8. 阶段划分与验收

### F-0 骨架恢复

- 从 `471714a` 取出 `frontend/`，放入当前仓库。
- 删除登录、WS、聚合快照与无后端的页面；路由收敛到 §3 的十个页面（可先用空壳）。
- `npm install` 成功，`npm run build` 通过，`npm run dev` 打开空白看板不报错。

**验收**：构建零错误；`/` 能显示 API 的 `/health` 真实结果；浏览器控制台无 404。

**完成（2026-10-18）**：`frontend/` 已恢复并重接当前 API；`npm run build` 零错误
（50 modules，JS 306.30 kB / CSS 24.20 kB）；Vite dev 3001 代理 8000 冒烟通过。

### F-1 后端只读补口

- 实现 §4.2 的全部端点与 DTO 扩展。
- 补测试：真实计数、offset 翻页不重不漏、过滤条件、空值语义、详情归因字段。
- 用当前 `data/bianca.db` 做一次真机 curl 冒烟（只读，不写库）。

**验收**：`pytest` 全绿；新端点在真实库上返回的数据能对上 SQL 直查结果；`total` 与 `COUNT(*)` 一致。

**完成（2026-10-18）**：`pytest` 623 passed（新增 `tests/test_api_read.py` 9 条，
覆盖真实计数、offset 翻页不重不漏、过滤、空值语义、详情归因、K 线、权益、目标/消息/计划）。
真实库冒烟：`/trades` 25、`/orders` 3、`/risk/events` 3、`/equity/history` 20、
`/goals` 2、`/market/klines` 4,813,526 —— 与 `COUNT(*)` 逐项一致；全部 25 条交易详情、
32 条决策详情、2 个目标的 messages/plans 均返回 200。

### F-2 页面落地

按优先级：

1. **交易明细 / 历史交易 / 订单回执**（用户点名的第一项）
2. **对话记录 / 目标 / 计划**（用户点名的第二项）
3. 看板与持仓
4. 决策、风控、行情、用量

**验收**：三类数据都能从界面追溯到数据库里的具体行；翻页、筛选、详情可用；空值与失败记录如实显示。

**进行中（2026-10-18）**：十个路由全部落地（看板、对话、目标历史、目标详情、交易/订单、
持仓权益、决策、风控、行情、用量）。已核对每个页面调用的端点与实际 JSON 字段一致；真实库上
所有详情端点 200。修复了四处内联 fetcher 导致的轮询环（每次渲染都会立即重发请求）。
浏览器渲染本身尚未逐页人工点过 —— 这台机器没有 Playwright/Puppeteer。

### F-3 静态托管（可选）

- `npm run build` 产物由 FastAPI 挂载，单端口 `8000` 打开控制台。
- 验证 SPA 深链接刷新不 404、`/api` 不被静态路由吞掉。

---

## 9. 关键风险与对策

| 风险 | 对策 |
|---|---|
| 旧前端依赖版本装不上（Vite 8 / TS 6） | F-0 第一步就 `npm install` 验证；装不上则按主版本回退到本机可用版本，不动业务代码 |
| 轮询打爆代理/交易所端点（`/exchange/*`、`/portfolio/snapshot`） | 页面只轮询只读端点；`/exchange/balance`、`/exchange/ticker` 仅在手动刷新时调用 |
| `market.db` 很大（809 MB / 478 万行） | `/market/klines` 永远带 `limit` 与索引条件（`symbol, interval, time`），不做全表扫描 |
| 详情里的 `transcript` / `prompt` 过大 | 列表不返回；计划 transcript 需显式 `include_transcript=1`，决策 prompt 只在单条详情返回 |
| offset 分页在数据增长后变慢 | 当前量级（交易数百、消息数千）足够；超过阈值再改 cursor，接口形状预留 `offset` |
| 无鉴权却误绑 0.0.0.0 | 启动日志打印绑定地址；文档与 README 同时写明 |

---

## 10. 开放问题

1. **前端是否需要「结束目标」按钮？** 后端目前只有 CLI/脚本能结束目标；界面是否需要，等对话页落地后再定。
2. **订单 `raw_response` 是否要在界面上完整展示？** 倾向于详情页折叠展示，默认不加载。
3. **权益历史返回全部还是抽样？** 当前每 tick 一张，量不大；超过 5000 点后再做下采样。
