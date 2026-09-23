
# Bianca — 行情数据模块设计文档

> 版本：v0.4 | 日期：2026-09-23 | 模块代号：Market Data | 阶段：PoC 延伸
> 状态：**阶段一代码已落地并实测**（§6.1）；阶段一验收 9 项中 5 项通过、2 项部分验证、2 项未验（§8）。v0.4：全量历史回补完成（4,778,552 行 / 809.2 MB / 覆盖率 99.8197%），修回补并发统计丢失更新，容量估算按实测行宽修正
> 技术前提已实测验证（见 §6），实现期结论见 §6.1
> 关联：[PRD v0.4](../../PRD-Bianca.md) · [架构设计 v0.4](../架构设计/架构设计文档-Bianca.md) · [数据库设计 v0.3](../数据库设计/数据库设计文档-Bianca.md)

---

## 1. 模块定位

### 1.1 边界划分

本模块是 Bianca 的**数据面（Data Plane）**，与现有 Agent 闭环的**控制面（Control Plane）**分离。

| 面 | 职责 | 现有组件 |
|----|------|----------|
| 控制面 | 做决策、下单、风控 | Supervisor → Analysis(LLM) → Risk → Execute |
| **数据面（本模块）** | 采集、落库、加工、供数、自观测 | 新增 |

**两条解耦边界：**

1. **数据面不感知 LLM。** 它只回答"某标的在某时刻的行情与衍生指标是什么"，不回答"该不该买"。
2. **行情环境与交易环境解耦。** 行情一律取**实盘**（`api.binance.com` / `stream.binance.com`），交易仍走**模拟盘**（`demo-api.binance.com`）。见 ADR-013。

收益：`supervisor.py` 的图结构一行不用改，只替换 `fetch_market_node` 的实现；数据面可离线运行、独立单测、独立观测。

### 1.2 现状缺口

当前 `spot_demo.py::fetch_market_context` 是唯一行情入口：每 tick（默认 300s）现打 REST 拉 ticker + 25 根 1h + 13 根 5m，算出指标塞进 prompt，**随后全部丢弃**。由此产生三个缺口：

| # | 缺口 | 后果 |
|---|------|------|
| 1 | **只见"点"不见"序列"** | LLM 拿到 `rsi14=62.1`，无从判断 RSI 是从 40 爬升还是一直横盘 |
| 2 | **不可复现** | `decision_logs` 无锚点指向当时那根 bar / 那份指标快照，事后无法归因 |
| 3 | **无离线评估地基** | 无历史数据集，策略无法回测，prompt 与指标无法迭代 |

附带技术债（均已实测确认）：

- `agent/exchange/market_stream.py` **是死代码**：全仓库 `MarketStream` 4 处引用全在自身文件内，`watch_ohlcv` 0 处，无任何模块 import，连测试都没有。→ **阶段一已删除**。
- `agent/exchange/_client.py::build_binance_config` **同时设置 `wsProxy` 与 `wssProxy`**，ccxt 的 `check_ws_proxy_settings()` 只允许三者取其一，实测抛出 `InvalidProxySettings`。即：只要配置了 `BINANCE_PROXY`，任何 ccxt WS 调用都会当场失败。（注：ccxt 在**构造时**不校验，只在 `watch_*` 真正开连时才抛。）→ **阶段一已修**，只保留 `wssProxy`。
- `fetch_ohlcv` 的 `limit` 位置参数曾传错导致 prompt 膨胀 7 倍（commit `8fdc838`）——**本模块必须制度化的前车之鉴**。

### 1.3 设计目标与非目标

**目标**
1. 分钟级 K 线自动采集、落库，缺口可检测、可回补、可重放
2. 回补 **2017-08-17** 起的全量 BTCUSDT 1m 历史，构成回测地基
3. 把原始 OHLCV 加工成**已消化**的行情摘要，供 Agent 消费
4. 指标快照落库，使每一次决策都可反查到当时的市场视图
5. 采集链路自身可观测，静默失效可被发现

**非目标**
- 不做策略、不做信号、不碰下单——那是控制面
- 不做 tick（逐笔）数据，1m bar 粒度已满足 PoC 及 ≥1 分钟粒度的全部策略回测

---

## 2. 架构分层

### 2.1 分层视图

```
                  ┌──────────────────────────────────────────┐
   控制面          │  Agent 闭环 (Supervisor→Analysis→...)     │
                  └───────────────────┬──────────────────────┘
                                      │ 供数接口（唯一入口）
                  ┌───────────────────▼──────────────────────┐
   数据面          │ ⑤ 供数层  get_market_context()            │
                  │    输出「已消化摘要」+ 质量元信息，含硬上限  │
                  ├──────────────────────────────────────────┤
                  │ ④ 加工层  indicators.py 扩展              │
                  │    SMA/RSI/EMA/ATR/波动率/滚动分位（纯函数）│
                  ├──────────────────────────────────────────┤
                  │ ③ 存储层  data/market.db（独立库文件）     │
                  │    klines + indicator_snapshots           │
                  ├──────────────────────────────────────────┤
                  │ ② 采集层  原生 WS kline_1m 主 + REST 回补  │
                  │    按 x=true 过滤 · 幂等 · 缺口检测 · 重连  │
                  └───────────────────┬──────────────────────┘
                                      │ 统一经代理（ADR-009/013）
                  ┌───────────────────▼──────────────────────┐
   外部（实盘行情） │  Binance  api.binance.com (REST)          │
                  │           stream.binance.com:9443 (WS)    │
                  └──────────────────────────────────────────┘

   横切：⑥ 观测层  /api/market/status —— 采集健康度、延迟、缺口
```

### 2.2 各层职责

| 层 | 职责 | 新增/改动 |
|----|------|-----------|
| ② 采集 | 订阅 bar、按 `x=true` 过滤关闭事件、幂等落库、缺口检测与回补、断线重连 | ✅ `agent/market/collector.py`、`backfill.py` |
| ③ 存储 | 独立库文件、独立 session factory、时序写入 | ✅ `agent/market/models.py`、`storage.py`、`repository.py` |
| — | 纯函数：解析 / 时间对齐 / 缺口检测 / 分片 | ✅ `agent/market/bars.py`（17 单测，无网络）|
| ④ 加工 | 纯函数指标计算、多周期窗口摘要 | ⬜ 扩展 `agent/exchange/indicators.py`（阶段二）|
| ⑤ 供数 | 面向 Agent 的上下文组装、prompt 预算裁剪 | ⬜ 新增 `agent/market/context.py`（阶段二）|
| ⑥ 观测 | 采集健康状态暴露 | ✅ `/api/v1/market/status`、`/api/v1/market/backfill`，并接入 `/api/v1/health` |
| — | 删除死代码，修复代理冲突 | ✅ `market_stream.py` 已删除；`_client.py` 只保留 `wssProxy` |

### 2.3 采集层

**通道策略：原生 WS 主 + REST 回补**

| 通道 | 用途 | 实现 |
|------|------|------|
| WebSocket `kline_1m` | 主通道，实时接收 bar 关闭事件 | **原生 `websockets` 库**，非 ccxt.pro |
| REST `fetch_ohlcv` | 冷启动 + 历史回补 + 断线补洞 | `httpx`（或 ccxt.async_support） |

**为什么不用 ccxt.pro（实测结论）：** ccxt.pro 的 WS 经 aiohttp 走代理时无法建立连接（`NetworkError: Cannot connect to host ... ssl:default`），而原生 `websockets.connect(url, proxy=...)` 到同一主机一次成功。既然字段口径也要求原生 payload（见 §3.1），两个理由指向同一结论。详见 §6 实测记录。

**核心不变量**

1. **只在 bar 关闭后落库。** Binance 每根 bar 内会推送多次增量更新（实测 23 秒内推送 11 次），**必须按 `k.x == true` 过滤**——这是协议自带的权威关闭标志，无需再用时间推断。现有散落在消费端的"丢弃最新一根未完成 K 线"逻辑（`spot_demo.py:97`）在采集层被 `x` 标志取代。
2. **幂等写入。** 主键 `(time, symbol, interval)`，写入用 `ON CONFLICT DO NOTHING`。回补可随意重放。
3. **缺口是常态而非异常。** 实测代理存在偶发抖动（回补测试中出现过一次读超时）。重连采用指数退避；重连后**先做缺口检测再恢复订阅**。
4. **时间口径统一。** 库内 `time` 一律 epoch 毫秒（INTEGER），与 Binance `k.t` 直接对齐。
5. **统一经代理。** 实测 WS 可直连、REST 直连超时——网络策略随时可变，不做双路径，一律走 `BINANCE_PROXY`。

**缺口检测算法**

```
期望 bar 数 = (now - last_stored_time) / interval_ms
若 缺失 > 0 → REST fetch_ohlcv(since=last_stored_time) 分页拉取，幂等写回
```

保留期取 10 年（ADR-015），实际等于不清理，"保留期陷阱"在 PoC 阶段不适用；但检测算法仍从配置读保留期参数，以免 MVP 引入清理策略时返工。

### 2.4 存储层

**决策：独立 SQLite 库文件，不提前上 TimescaleDB。**

分钟级数据量很小（见 §3.3），选型理由不是"数据量大"，而是**写入模式隔离**。因此：

| 库文件 | 内容 | 说明 |
|--------|------|------|
| `data/bianca.db` | `trade_logs` / `decision_logs` / `risk_events` / `agent_config` | 维持现状不动 |
| `data/market.db` | `klines` / `indicator_snapshots` | **本模块新增** |

独立 session factory，独立 `init_db`，启用 WAL。新增配置项 `MARKET_DATABASE_URL`。

### 2.5 加工层

`agent/exchange/indicators.py` 的纯函数设计（无网络/IO、可离线单测）是有价值的既有资产，**扩展而非重写**：

| 指标 | 现状 | 本模块 |
|------|------|--------|
| SMA(5/20) | ✅ 已有 | 保留 |
| RSI(14) | ✅ 已有 | 保留 |
| trend 判定 | ✅ 已有（含死区） | 保留 |
| EMA(12/26) | — | 新增 |
| ATR(14) | — | 新增（波动率） |
| 滚动波动率 | — | 新增 |
| 滚动分位 | — | 新增 |
| 买卖压力比 | — | 新增（由 `taker_buy_base / volume` 得出，见 §3.1） |

**多周期**：PoC 只落 1m，5m/15m/1h 由查询时从 1m 聚合（可加轻量缓存）。MVP 改由 TimescaleDB continuous aggregate 物化。

### 2.6 供数层

供数层是数据面对外的**唯一入口**，也是防 prompt 膨胀的闸门。

```python
async def get_market_context(
    symbol: str,
    *,
    windows: Sequence[str] = ("5m", "1h"),
    max_bars: int = 25,
    max_chars: int = 2000,
) -> dict[str, Any]: ...
```

返回结构：

```python
{
    "symbol": "BTCUSDT",
    "as_of": 1790163900000,          # 最新已完成 bar 的 open_time
    "ticker": { ... },                # 24h 快照
    "series": {                       # 每周期：已消化摘要，非原始 bar 数组
        "5m": {"bars": 25, "sma5": ..., "sma20": ..., "rsi14": ...,
               "trend": "up", "change_pct": ..., "volatility_pct": ...,
               "buy_pressure": 0.71},
        "1h": { ... },
    },
    "quality": {"lag_seconds": 42, "gaps": 0, "stale": False},
    "truncated": False,               # max_chars 触发降采样时为 True
}
```

**契约铁律：输出已消化摘要，绝不输出原始 bar 数组。** 返回体不含 `candles` 字段。两道闸：`max_bars` 限制参与计算的 bar 数，`max_chars` 硬上限超出则降采样并置 `truncated`。

### 2.7 观测层

**最大风险故障模式是采集器静默死亡。** 必须让它自己喊出来。

新增 `GET /api/v1/market/status`，沿用 `/api/agent/status` + `RunnerSnapshot` 风格（实际实现字段，`MarketStatusResponse`）：

| 字段 | 含义 |
|------|------|
| `collector_running` / `connected` | 采集任务是否存活 / WS 是否处于连接态 |
| `last_bar_open_time` | 最后落库的 bar 时间 |
| `last_closed_bar_open_time` | **此刻**最近一根已收盘 bar — 与上一行对比即可看出是否落后 |
| `lag_seconds` | `now - last_bar_open_time - interval`，**核心健康指标** |
| `bars_written_session` | 本进程内写入行数 |
| `bars_count_24h` | 24h 已落库行数（从库算，非计数器，重启不失真）|
| `gap_count_24h` | 本进程内补齐的缺口 bar 数 |
| `reconnects_session` | 本进程内 WS 重连次数（多连接后按连接维度统计）|
| `backfill_running` / `backfill_last` / `backfill_error` | 历史回补进度与结果 |
| `last_gap_check_at` / `last_write_at` / `last_error` / `last_error_at` | 时序诊断 |
| `database` | 库文件名（确认没连错库）|

配套 `POST /api/v1/market/backfill` 手动触发历史回补（幂等可重入，已在跑则直接返回）。

`lag_seconds` 超过 3 个 bar 周期（180s，`collector._STALE_LAG_S`）时，`/health` 的 `market` 字段标记为 `error` → 整体 `degraded`。冷启动首根 bar 落库前返回 `warming_up`，不算故障。

---

## 3. 数据模型

### 3.1 klines — 分钟级 K 线

字段直接对应 Binance 原始 WS 事件（**全部实测确认存在**，见 §6）：

| 列名 | 类型 | 来源字段 | 说明 |
|------|------|---------|------|
| `time` | INTEGER | `k.t` | bar 开始时间，epoch 毫秒（主键）|
| `symbol` | TEXT | `k.s` | 如 BTCUSDT（主键）|
| `interval` | TEXT | `k.i` | 如 `1m`（主键）|
| `open` / `high` / `low` / `close` | REAL | `k.o/h/l/c` | 价格 |
| `volume` | REAL | `k.v` | **基础币**成交量 |
| `quote_volume` | REAL | `k.q` | 计价币成交额 |
| `trades` | INTEGER | `k.n` | 成交笔数 |
| `taker_buy_base` | REAL | `k.V` | 主动买基础币量 |
| `taker_buy_quote` | REAL | `k.Q` | 主动买计价币额 |

主键 `(time, symbol, interval)`；索引 `(symbol, interval, time DESC)`。

> **`quote_volume` / `taker_buy_*` 是本模块相对 MVP DDL 的扩展。** MVP 的 `002_mvp_postgres.sql` 中 `klines` 只到 `volume`，迁移前需同步补齐这几列（见 §9）。这几列是"买卖压力"信号的来源，也是不做 tick 采集就能拿到订单流信息的中间解。

### 3.2 indicator_snapshots — 指标快照（可复现性锚点）

| 列名 | 类型 | 说明 |
|------|------|------|
| `id` | TEXT PK | UUID |
| `symbol` | TEXT | 标的 |
| `as_of` | INTEGER | 快照对应的 bar 时间（epoch ms）|
| `window` | TEXT | 周期，如 `1h` |
| `bar_count` | INTEGER | 参与计算的 bar 数 |
| `metrics` | TEXT | JSON：全部指标值 |
| `context_digest` | TEXT | JSON：实际交给 LLM 的摘要原文（**含当时 ticker 的 bid/ask，该值不可重现**）|
| `created_at` | TEXT | ISO8601 |

`decision_logs` 新增可空列 `market_snapshot_id`，指向本表。沿用现有 `_ensure_columns` 幂等迁移模式（`storage/database.py`）补齐列，不引入 alembic。

**写入频率：每 tick 一次**（与 Agent 决策 1:1，288 行/天）。指标序列本身可从 klines 重算（纯函数），不冗余落库；只有 `context_digest` 这类不可重现的内容才必须持久化。

### 3.3 容量估算

单标的 BTCUSDT：

| 项 | 数值 |
|----|------|
| 历史跨度 | 2017-08-17 ~ 今 ≈ 9.1 年 |
| 历史 bar 数（理论格子） | 4,787,179 根；Binance 实有 4,778,533 根 |
| **历史 bar 数（实测落库）** | **4,778,552 根** |
| **历史落库体积（实测）** | **809.2 MB**（+ WAL 5.0 MB）|
| **均摊行宽（实测）** | **169.6 B/行** |
| 10 年保留上限 | ~5,256,000 行 → **~890 MB**（按实测行宽外推）|
| 日常增量 | 1,440 行/天 ≈ 0.24 MB/天 ≈ 89 MB/年 |
| 快照增量 | 288 行/天 |

> 体积估算修正在 2026-09-23 全量回补后：原估 ~550 MB / 10 年偏低 47%，真实行宽 **169.6 B/行**而非纸面算的 ~105 B/行。原因是纸面算法漏了开销：① 12 个列全宽存储（`time` INTEGER + `symbol`/`interval` TEXT + 4 个 REAL 价格 + 4 个 REAL 量 + `trades` INTEGER）；② **两个索引**而非一个 —— 复合主键 `(time, symbol, interval)` 不是 rowid 别名，SQLite 自动建了 `sqlite_autoindex_klines_1`，模型里又显式建了 `idx_klines_symbol_interval_time`，两者列序不同各有用途但确实有冗余；③ 页填充率（实测 `freelist_count=0`，无空洞）与 WAL 冗余。**结论不变**（SQLite 仍毫无压力，容量不构成选型理由），但 `MARKET_SYMBOLS` 扩标的时容量要按 **169.6 B/行 × 标的数** 重算 —— 加 10 个标的即约 8.9 GB/10 年，届时才需要重新评估。

结论：**PoC 规模下 SQLite 单表毫无压力**，容量不构成选型理由。

---

## 4. 集成点

### 4.1 与 Agent 的集成（阶段二）

仅替换 `supervisor.py::fetch_market_node` 的实现：

```
阶段一（数据面独立运行）：
  Agent 闭环 ──REST──▶ Binance demo       （现状不变）
  采集器     ──WS────▶ Binance 实盘 ──▶ market.db

阶段二（Agent 切换为数据面消费者）：
  Agent 闭环 ──▶ get_market_context() ──▶ market.db ──▶ Binance
                 （不再直连 Binance 取行情）
```

`fetch_market_node` 与 `TradeState` 的接口不变，`analysis_agent` / `risk_agent` / `execute_agent` 全部无感。

### 4.2 配置项新增

全部已在 `agent/config.py` 与 `.env.example` 落地：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MARKET_REST_BASE_URL` | `https://api.binance.com` | **实盘** REST（ADR-013）|
| `MARKET_WS_BASE_URL` | `wss://stream.binance.com:9443/ws` | **实盘** WS |
| `MARKET_DATABASE_URL` | `sqlite+aiosqlite:///./data/market.db` | 行情独立库（ADR-008）|
| `MARKET_SYMBOLS` | `BTCUSDT` | 逗号分隔；按订阅集合建模（ADR-016）|
| `MARKET_INTERVAL` | `1m` | 采集周期 |
| `MARKET_MAX_CONTEXT_CHARS` | `2000` | prompt 预算硬上限（ADR-012，阶段二启用）|
| `MARKET_RETENTION_DAYS` | `3650` | 保留期（10 年，ADR-015）|
| `MARKET_BACKFILL_START` | `2017-08-17` | 历史回补起点（ADR-014）|
| `MARKET_BACKFILL_ON_START` | `true` | 启动时后台回补全历史（不阻塞服务启动）|
| `MARKET_BACKFILL_CONCURRENCY` | `5` | 回补并发分片数（1–20）|
| `MARKET_COLLECTOR_AUTOSTART` | `true` | lifespan 中自动启动采集器 |

代理不新增变量：统一复用 `BINANCE_PROXY`（`Settings.market_proxy`），不做直连/代理双路径（ADR-009 不变量 5）。

---

## 5. 已确认决策（ADR）

> 以下 ADR 后续应合并入[架构设计文档](../架构设计/架构设计文档-Bianca.md) §4 主 ADR 列表，编号顺延 ADR-006 之后。

### ADR-007: 数据面与控制面分离
- **状态:** ✅ 已采纳 | **日期:** 2026-09-23
- **决策:** 行情采集/加工/供数独立成模块，不感知 LLM；控制面通过单一供数接口消费
- **理由:** 边界清晰、可离线测试与观测、Agent 图结构零改动

### ADR-008: 独立 SQLite 库文件（PoC 不提前上 TimescaleDB）
- **状态:** ✅ 已采纳 | **日期:** 2026-09-23
- **决策:** 行情数据落 `data/market.db`，与业务库分离；Schema 对齐并扩展 MVP DDL
- **理由:** 数据量小（实测 ~890 MB / 10 年，单标的，见 §4），选型理由是写入模式隔离而非容量

### ADR-009: 原生 WebSocket 主通道 + REST 回补
- **状态:** ✅ 已采纳 | **日期:** 2026-09-23
- **决策:** 用原生 `websockets` 库订阅 `kline_1m`，**不用 ccxt.pro**；REST 负责冷启动与回补
- **理由（三条，均经实测）:**
  1. 字段：ccxt 的 6 列 OHLCV 丢弃 `x`/`n`/`q`/`V`/`Q`，其中 `x` 是 bar 关闭的权威标志
  2. 连通性：ccxt.pro 经 aiohttp 走代理无法建立 WS 连接，原生 `websockets` 同主机成功
  3. 依赖：ccxt 声明依赖中无 `websockets`，其 WS 走 aiohttp；原生路径少一层封装
- **替代方案:** ccxt.pro（实测在本环境不可用）

### ADR-010: 采集器与 API 同进程 asyncio 任务
- **状态:** ✅ 已采纳 | **日期:** 2026-09-23
- **决策:** 采集器作为独立 asyncio 任务，与 `AgentRunner` 并列，各自独立生命周期与快照
- **替代方案:** 独立进程/容器（隔离更好，但需独立部署与重启策略）

### ADR-011: 指标快照落库
- **状态:** ✅ 已采纳 | **日期:** 2026-09-23
- **决策:** 每 tick 加工的指标与上下文摘要落 `indicator_snapshots`，`decision_logs` 外键关联
- **理由:** 可复现性是决策归因的前提；这是缺口 2 的直接解药
- **代价:** 写入量与 schema 复杂度上升一档（288 行/天，可忽略）

### ADR-012: 供数层强制 prompt 预算
- **状态:** ✅ 已采纳 | **日期:** 2026-09-23
- **决策:** 供数层只输出已消化摘要，绝不出原始 bar 数组；设 `max_bars` 与 `max_chars` 双闸
- **理由:** `8fdc838` 已证明 prompt 膨胀是真实且高发的故障模式

### ADR-013: 行情取实盘，交易走模拟盘
- **状态:** ✅ 已采纳 | **日期:** 2026-09-23
- **决策:** 数据面连 `api.binance.com` / `stream.binance.com:9443`（实盘）；交易仍走 `demo-api.binance.com`
- **理由:** 数据必须能与真实历史对账才能支撑回测；行情环境与交易环境无需绑定
- **实测补充:** Demo REST 与实盘 REST 返回**字节级一致**（同一 bar 同一数值），Demo WS 收盘事件约晚一根 bar。即 Demo 无独立价格体系，分离的成本极低。

### ADR-014: 回补 2017 起全量历史
- **状态:** ✅ 已采纳 | **日期:** 2026-09-23
- **决策:** 一次性回补 BTCUSDT 1m 自 2017-08-17 04:00 UTC 起的全部历史（~483.6 万根）
- **理由:** 10 年保留的初衷是回测地基；从今日起正向累积则数年内无可回测数据
- **实测:** 限频 12/12 无限制，延迟中位 698ms；串行约 55 分钟，适度并发（5-8 路）约 10-20 分钟
- **结果（2026-09-23）:** 并发 5 实跑 **825s（13.8 分钟）**，落在预估区间内；4,788 分片 `failed_chunks=0`（代理的偶发读超时被 `_MAX_RETRIES=4` 的指数退避完全吸收，一次都没耗尽）；落库 4,778,552 行 / 809.2 MB / 覆盖率 99.8197%
- **约束:** 必须带重试（实测代理存在偶发读超时）；回补与日常采集共用同一套分页 + 幂等写入逻辑
- **已知边界:** 回补入口必须**显式给区间** —— `backfill_history()` 的 `max(time)` 续传基点使其无法补历史空洞（§6.1 纠正 #3）

### ADR-015: 保留期 10 年
- **状态:** ✅ 已采纳 | **日期:** 2026-09-23
- **决策:** `MARKET_RETENTION_DAYS=3650`，PoC 阶段实际不清理
- **理由:** 10 年 ≈ 890 MB（实测外推），SQLite 可承受；保留完整历史是回测价值的前提
- **影响:** MVP 迁移时 `002_mvp_postgres.sql` 中的 `add_retention_policy(..., INTERVAL '90 days')` 需移除；**保留（retention）与压缩（compress）是两回事**，应保留数据、只做压缩

### ADR-016: 采集层按"订阅集合"建模
- **状态:** ✅ 已采纳 | **日期:** 2026-09-23
- **决策:** 采集器接口接受 symbol 集合（当前仅 `{BTCUSDT}`），而非单 symbol
- **理由:** Binance 对单连接订阅数与建连频率有上限，多标的标准做法是合并 stream 到单连接。现在按集合建模，未来扩标的是改配置而非改架构，成本近零

---

## 6. 实测验证记录

> 数据来源：`scripts/probe_binance_market.py`，2026-09-23 实机运行。环境：Windows 11，proxy `http://127.0.0.1:7897`（Clash Verge）。

| # | 验证项 | 结果 |
|---|--------|------|
| 1 | 实盘 / Demo REST 连通 | ✅ 均 HTTP 200（3627ms / 1547ms，均为冷启动）|
| 2 | 1m 历史最早 bar | ✅ **2017-08-17 04:00:00 UTC** |
| 3 | 历史规模 | ~4,836,000 根 → ~4,836 次 REST 请求 @ limit=1000 |
| 4 | 原始 WS payload 字段 | ✅ 全部存在：`x` `n` `q` `V` `Q`（见下）|
| 5 | bar 关闭标志 | ✅ `k.x == True`，23.4s 内收到一次收盘事件 |
| 6 | bar 内推送频率 | 23 秒内推送 11 次增量（`n` 从 1123 涨到 1674）→ **必须按 `x` 过滤** |
| 7 | ccxt.pro WS 经代理 | ❌ `NetworkError: Cannot connect to host ... ssl:default` |
| 8 | 原生 websockets 经代理 | ✅ 同一主机一次成功 |
| 9 | klines 限频 | ✅ 12/12 无限制；延迟 min 155ms / 中位 698ms / max 1526ms |
| 10 | Demo REST vs 实盘 REST | ✅ **字节级一致**（同一 bar 同一数值）|
| 11 | Demo WS vs 实盘 WS | ⚠️ 收盘事件约晚一根 bar（单次采样）|
| 12 | REST 直连（不走代理）| ❌ 超时 |
| 13 | WS 直连（不走代理）| ✅ 可通（但不可作为设计前提）|

**实测得到的原始 WS 收盘事件完整字段：**

```json
{ "e": "kline", "E": 1790163720000, "s": "BTCUSDT",
  "k": { "t": 1790163720000, "T": 1790163779999, "i": "1m", "s": "BTCUSDT",
         "o": "85497.29000000", "c": "85493.70000000",
         "h": "85497.29000000", "l": "85476.00000000",
         "v": "8.27187000",   // 基础币成交量
         "q": "707101.04629110",  // 计价币成交额
         "n": 1682,               // 成交笔数
         "V": "5.83506000",       // 主动买基础币量
         "Q": "498789.85468940",  // 主动买计价币额
         "f": 6706393771, "L": 6706395452,  // 首/末成交 ID
         "x": true,               // ← 权威 bar 关闭标志
         "B": "0" } }
```

**两个已知的库级问题（非本项目代码）：**

1. `websockets` 16.0 在连接被重置时，其 `connection_lost` 清理路径抛 `AttributeError: 'ClientConnection' object has no attribute 'recv_messages'`。不影响采集逻辑，但会向日志注入噪声。经代理的长连接会频繁遇到重置，**需在采集器中抑制该异常或固定 websockets 版本**。
2. `ccxt.pro` 的 WS 经 aiohttp 代理在本环境不可用（见 #7）。修复方向是改用原生 WS（ADR-009），而非修补 ccxt 配置。

### 6.1 实现期实测（阶段一）

> 数据来源：`scripts/smoke_market_collector.py`（真实连 Binance WS，走代理）、`python -m pytest`、uvicorn 实机启动，2026-09-23。

| # | 验证项 | 结果 |
|---|--------|------|
| 14 | 回补幂等（3000 根区间跑两遍） | ✅ 首次 `rows_inserted=3000`，重放 `rows_inserted=0`，库内行数不变 |
| 15 | 回补吞吐 | ✅ 3 分片并发 5，2.2s 完成 → 与"全量约 10-20 分钟"的估算一致 |
| 16 | 回补起点对齐 | ✅ 首根 `1502942400000` = 2017-08-17 04:00:00 UTC，与探针 #2 一致 |
| 17 | WS 采集（190s 实跑） | ✅ 写入 3 根，恰为每分钟 1 根；`lag_seconds=16` |
| 18 | 重连补缺口 | ✅ 冷启动 `gaps_filled=2881`（补齐 2 天 lookback 全窗口）；后续每次启动补上"上一次停机时撞掉的边界 bar"（`gaps_filled` 2 / 3）|
| 19 | 断点续传基点 | ✅ 库内已是最新时 `backfill_history` 返回 `requests=0 / ranges=0`，直接短路 |
| 20 | 停机干净 | ✅ `stop()` 耗时 1.36s（含 WS 关闭握手），无异常、无 teardown 噪声 |
| 21 | `/api/v1/market/status` | ✅ 实机返回 `lag_seconds=4`、`connected=true`、`database=market.db` |
| 22 | `/api/v1/health` 接入 | ✅ `market: "ok" / market_detail: "lag 40s"`；采集器停跑或落后 >180s → `degraded` |
| 23 | WAL 生效 | ✅ 运行期 `market.db-wal` / `-shm` 存在（ADR-008 前提成立）|
| 24 | `websockets` 依赖声明 | ❌→✅ 原先只是 `uvicorn[standard]` 的传递依赖，采集器直接 import 后已在 `pyproject.toml` 显式声明 `websockets>=15.0` |
| 25 | 会话泄漏修复 | ✅ 修前 `test_health` 必现 `Unclosed client session`，修后消失 |
| 26 | **全量历史回补** | ✅ `scripts/backfill_market_history.py`，4,788 分片并发 5，**825s 跑完**，`failed_chunks=0`，`rows_received=4,778,533` |
| 27 | **落库校验** | ✅ **4,778,552 行 / 809.2 MB**（+ WAL 5.0 MB）；`distinct(time,symbol,interval) == count` 无重复、OHLCV 无空值、`PRAGMA integrity_check=ok` |
| 28 | **覆盖率** | ✅ **99.8197%**（对理论分钟格子）。35 段缺口共 8,632 分钟，全部落在 2017-2021；2022/2024/2025/2026 一根不缺 |
| 29 | **残余缺口归因** | ✅ 复拉 2018-02-07→10（格子 5,760 分钟）：Binance 仅返回 3,734 根、`rows_inserted=0` —— **缺口是交易所自身停机，非本系统丢失**。独立交叉验证：文档记载的停机窗口与实测缺口逐点吻合（见 §6.1 纠正 #4）|
| 30 | **回补与采集并发** | ✅ 采集器与 14 分钟批量写入并行全程无碍：`reconnects_session=0`、`last_error=null`、`/api/v1/health` 报 `market: ok / lag 11s` |

**实测纠正的两个既有认知：**

1. `_client.py` 同时设置 `wsProxy` + `wssProxy` 的后果需要重新定位：ccxt **不在构造时**报错，只有真正开 WS（`check_ws_proxy_settings()`，由 `watch_*` 调用）才抛 `InvalidProxySettings`。已实测复现并修掉（只保留 `wssProxy`，币安行情 WS 是 `wss://`）。另注：删除 `market_stream.py` 后仓库内已无任何 ccxt WS 调用点，此修复属预防性。
2. `insert_rows` 的计数**不能靠 `rowcount`**：实测在 `executemany` + `ON CONFLICT DO NOTHING` 下，Python 的 sqlite3 驱动即使首次插入也返回 0。已改为"先查该范围已存时间戳、只写缺失行"——精确计数，且重放退化为纯读不写。
3. **`stats.rows_inserted += await repo.insert_rows(rows)` 是丢失更新**。增强赋值会**先读左值、再 await**：并发 worker 全都读到同一个旧值，各自加完再写回，互相覆盖。全量跑时它报 416.1 万而库内实际 477.8 万，**少报 61.8 万**（`insert_rows` 的返回值本身是对的，丢在累加环节）。已在 `tests/test_market_backfill.py` 用临时库 + `concurrency=5` 固定住交错窗口复现（修复前 `rows_inserted=50`，应为 250），修法为先 `await` 拿到增量再同步累加。**教训：库里数据正确 ≠ 统计口径正确**，验收若只看 `rows_inserted` 会被误导。
4. **残余缺口归因必须落到"可交叉验证的外部事实"，不能停在像是合理的猜测上。** 第一版把最大的缺口（2018-02-08，2,011 分钟）写成"币安异常交易停机"——错了。查证后确认是**副本数据库集群失步导致的停机升级**（CZ 当时说明 replica DB cluster 数据不同步、需从主库全量重同步；维护自 UTC 2/8 00:00 左右开始，官方宣布 2/9 04:00 UTC 恢复，后又因云厂商遭 DDoS 延后）。而"异常交易"（irregular trading activity）是 2018 年 2 月下旬**另一桩**钓鱼 + Viacoin 操纵事件，两者被我混为一谈。**修正后反而得到了更强的结论**：实测缺口 `2018-02-08 00:28 → 2018-02-09 10:00` 与公开报道的停机窗口逐点吻合（起于维护开始时刻、止于宣布恢复时刻之后）—— 这是独立于币安 API 的**第三方证据**，比"复拉发现没数据"更能证明缺口来自交易所而非本系统。**教训：一个看起来合理的归因（"肯定是交易所停机"）恰好正确时，最容易停止验证；而错在细节上的归因会在日后被引用时变成假事实。**

**一个反直觉但重要的事实：`backfill_history()` 补不了"中间的洞"。** 它的续传起点是库内 `max(time) + 1 周期`（§6.1 #19 已验证该短路行为），因此只要库里已有最新 bar，它就判定"已补到最新"直接返回 0 请求 —— 而那种「2017 一段 + 近期一段」的双岛形态恰恰是它完全无法处理的。**启动时的自动回补（`MARKET_BACKFILL_ON_START`）同理，只能跟随尾部，不能自愈历史空洞。** 全量补洞必须走 `backfill_range()` 显式给区间；`POST /api/v1/market/backfill` 端点同样受此限制。这是设计上的已知边界，非缺陷，但运维上要知道：**洞一旦形成，只能靠 `scripts/backfill_market_history.py` 手动补。**

**一个需要说明的边界竞态（非缺陷）：** bar 收盘推送恰在分钟边界发出，与"停机/重连"可能相撞，导致最后一根边界 bar 本次未落库。这不是丢数据：该缺口已包含在 2 天 lookback 内，下次启动的缺口巡检会补上——实测 #18 的 `gaps_filled` 正是这个机制在起作用。

---

## 7. 关键风险与对策

| # | 风险 | 影响 | 对策 |
|---|------|------|------|
| 1 | **采集器静默死亡** | 数据断流但无人知 | `lag_seconds` 指标 + `/health` degraded；缺口检测兜底 |
| 2 | **代理不稳定** | 连接重置、读超时、数据缺失 | 指数退避重连；回补带重试（实测代理有过偶发超时）；重连后先补缺口 |
| 3 | **prompt 膨胀** | token 成本激增、决策劣化 | ADR-012 双闸 + `truncated` 标志；对供数层写契约测试 |
| 4 | **未完成 bar 入库** | 指标基于半根 bar，信号失真 | 采集层按 `x=true` 过滤（实测可靠） |
| 5 | **websockets 16.0 teardown bug** | 日志噪声，可能掩盖真实错误 | ✅ 已在 `collector.install_ws_noise_filter()` 抑制该特定 `AttributeError`，其余异常仍走默认 handler（见 §6）|
| 6 | **回补中途失败** | 历史数据不完整 | 幂等写入使回补可重放；按区间记录进度，断点续传 |
| 7 | **SQLite 写入锁竞争** | Agent tick 阻塞 | 独立库文件（ADR-008）+ WAL |
| 8 | **网络策略变化** | 直连/代理路径失效 | 不做双路径，统一走代理（ADR-009 不变量 5）|

---

## 8. 阶段划分与验收标准

### 阶段一：数据积累 + 历史回补（Agent 完全不动）

**交付：** 采集器 + `market.db` + 1m 落库 + 缺口回补 + 历史回补 + `/api/v1/market/status` + `/api/v1/market/backfill`

**代码已全部就位**（`agent/market/`，`agent/main.py` lifespan 已接线），验收项状态如下 —— 未验项不得算作通过：

- [x] 一次性回补 2017-08-17 起全量历史，~483.6 万根，落库后行数校验通过 —— ✅ 2026-09-23 完成，**4,778,552 行 / 809.2 MB / 覆盖率 99.8197%**，无重复无空值 `integrity_check=ok`（§6.1 #26-29）。**实测体积比文档原估的 550 MB 高 47%** —— 均摊 **169.6 B/行**（原估约 105 B/行），§4 各处的容量估算已按实测值修正
- [x] 残余缺口已归因 —— ✅ 8,632 分钟 / 35 段全部落在 2017-2021，复拉验证为**币安自身停机**，非本系统丢失（§6.1 #29）。**该结论改变了"覆盖率必须 100%"的预期：100% 是达不到的，99.82% 已是 Binance 历史数据的上限**
- [x] 回补可重放：同一区间重复回补不产生重复行 —— ✅ §6.1 #14
- [~] 回补可断点续传：中断后重启从断点继续，不从头开始 —— 续传基点已验（库空→从头 / 库满→0 请求，§6.1 #19），**飞行中杀进程的中断测试未做**
- [ ] 连续运行 24h，`gap_count_24h` 自动补齐至 0 —— **未跑**（当前最长连续运行 190s，§6.1 #17）
- [x] 正常态 `lag_seconds < 90` —— ✅ 实测 4s / 16s / 40s
- [x] 进程重启后自动回补停机期间的缺口 —— ✅ §6.1 #18（2881 / 2 / 3）
- [ ] 断网 → 恢复，采集自动续上且数据无洞 —— **未测**（代理抖动下的重连/重试逻辑已实现，退避未被真实触发）
- [~] 采集器死亡时 `/health` 在 180s 内变为 `degraded` —— 判定逻辑已实现并有单测（`test_market_health_error_when_lag_exceeds_threshold`）+ 实机返回 `market: "ok"`，**真实死亡注入未做**

图例：`[x]` 已实测通过 / `[~]` 部分验证 / `[ ]` 未验证。

**上线前必须先补的两项（原三项中的"全量历史回补"已于 2026-09-23 完成）：** **24h 连续运行**、**断网恢复**。前者决定数据底座是否可信，后者决定无人值守时会不会静默断流。

### 阶段二：接入 Agent

**交付：** 供数层接入 `fetch_market_node` + 多周期指标 + 快照落库 + `decision_logs` 关联

**验收：**
- [ ] Agent tick 不再直连 Binance 取行情
- [ ] 上下文体积有硬上限，超限时 `truncated=true` 并可观测
- [ ] 任意一笔 `decision_log` 可反查到对应 `indicator_snapshot` 及其 `context_digest`
- [ ] `indicators.py` 新增指标保持纯函数、可离线单测

### 阶段三：MVP 演进

多标的（订阅集合已就绪，ADR-016）、TimescaleDB + continuous aggregate、压缩（不删数据）、Redis 缓存。

---

## 9. 对现有文档的后续影响

### 9.1 已在阶段一落地

| 目标 | 处置 |
|------|------|
| `.env.example` | ✅ 新增 §4.2 全部 `MARKET_*` 配置项 |
| `pyproject.toml` | ✅ 显式声明 `websockets>=15.0`（原先只是 `uvicorn[standard]` 的传递依赖，采集器直接 import 后必须显式化）|
| `agent/exchange/market_stream.py` | ✅ 死代码，已删除；能力由新采集层取代 |
| `agent/exchange/_client.py` | ✅ 已只保留 `wssProxy`。注意实测结论：ccxt **不在构造时**报错，只有 `watch_*` 调用到 `check_ws_proxy_settings()` 才抛 `InvalidProxySettings` |
| `agent/exchange/spot_demo.py` | ✅ 修复 `__aenter__` 里 `load_markets()` 失败时不关会话的泄漏 —— `__aenter__` 抛异常时 Python 不调 `__aexit__`，而币安不可达正是该路径最常见的失败点，`/health` 每次失败都漏一个 aiohttp 会话 |
| `agent/api/routes.py` + `schemas.py` | ✅ 新增 `/api/v1/market/status`、`/api/v1/market/backfill`，`/health` 接入 `market` / `market_detail` |
| `agent/market/repository.py` | ✅ 新增 `times_in_range` / `first_bar_time` / `count_since` / `count_all`；`recent_closes` 供阶段二供数层使用 |
| `agent/main.py` | ✅ lifespan 接入 `init_market_db()` + 采集器启停；`/` 返回 name/version/docs |
| `scripts/backfill_market_history.py` | ✅ 新增。全量补洞专用 —— 显式区间驱动 `backfill_range()`，带进度/ETA，跑完自检覆盖率与最大残余缺口（`_largest_gaps` 用 SQL 窗口函数在库内算，不把 480 万行时间戳拉进 Python）。**存在的理由就是 `backfill_history()` 补不了中间的洞**（§6.1 纠正 #3）|
| `tests/test_market_backfill.py` | ✅ 新增 4 个单测，全程不出网（`_fetch_chunk` 被替换）。锁定并发下的统计口径（`rows_inserted == 库内实际行数`）、重放幂等、失败分片计数、进度回调次数 |
| `agent/market/backfill.py` | ✅ 修并发丢失更新（§6.1 纠正 #3）|
| 文档大版本号 | ✅ 本设计文档 **v0.4**：v0.3 补 §2.7 可观测字段与健康判定、§6.1 实现期实测、§8 阶段一验收实况；v0.4 补全量回补实测（§6.1 #26-30）、并发统计丢失更新（纠正 #3）、`backfill_history()` 补洞边界 + 缺口归因的外部交叉验证（纠正 #4）、§4 容量估算按实测行宽修正 |

### 9.2 待同步更新（**尚未改动**）

| 目标 | 待更新内容 |
|------|-----------|
| [数据库设计文档](../数据库设计/数据库设计文档-Bianca.md) §2 | 移除"PoC 无 `klines` 持久化"的说明 |
| [数据字典](../数据库设计/数据字典.md) | 新增 `klines`、`indicator_snapshots` 条目 |
| [002_mvp_postgres.sql](../数据库设计/sql/002_mvp_postgres.sql) | `klines` 补齐 `quote_volume` / `taker_buy_base` / `taker_buy_quote`；**移除 90 天 retention 策略**（ADR-015）|
| [架构设计文档](../架构设计/架构设计文档-Bianca.md) | ADR-007~016 并入 §4；§6 MVP 扩展表补充数据面 |
| [系统设计文档](../../system-design/系统设计文档-Bianca.md) §2.1 | 分层图"内存行情缓存"改为"独立行情数据层"；`market_stream.py` 从文件清单移除 |
| [PRD](../../PRD-Bianca.md) §3.1 | P0"WebSocket 行情"目前实为 REST 轮询且 WS 路径从未运行，需对齐实际状态 |

---

## 10. 开放问题

| # | 问题 | 影响面 | 状态 |
|---|------|--------|------|
| 1 | `indicator_snapshots` 写入频率 | 写入量、复现精度 | ✅ 已定：每 tick |
| 2 | 保留策略 | 运维、磁盘 | ✅ 已定：10 年 |
| 3 | volume 口径 | 指标准确性 | ✅ 已定：存原始全字段 |
| 4 | 是否需要 tick 数据 | 采集架构、容量 | ✅ 已定：不需要 |
| 5 | 多标的共享 WS 连接 | 配额、连接数 | ✅ 已定：按订阅集合建模 |
| 6 | `websockets` 版本固定 vs 异常抑制 | 依赖稳定性 | ✅ 已定：**两者都做** —— 声明 `websockets>=15.0` 下限 + `install_ws_noise_filter()` 抑制该特定异常（§6.1 #20）|
| 7 | 回补并发度（1 / 5-8 / 更高） | 回补耗时 vs 被限流风险 | ✅ **已定：5 够用**。全量实测 4,788 分片 / 825s（≈5.8 分片/秒，17.4s 每千分片），`failed_chunks=0`、全程无 429/418。按 klines 权重 2 计约 276 权重/分钟，远低于 6000/分的上限 —— 提到 8-10 只会把收益递减掉，还压缩限流余量 |
| 9 | 历史空洞的自愈 | 运维、数据可信度 | ⬜ 待定（现状：`backfill_history()` 与启动自动回补受 `max(time)` 基点限制，**补不了中间的洞**，须手动跑 `scripts/backfill_market_history.py`；是否让采集器周期任务接管见 §6.1 纠正 #3）|
| 8 | 是否同时回补 5m/1h 等周期 | 存储、查询性能 | ⬜ 待定（当前倾向：只存 1m，查询时聚合。订阅集合已就绪，加周期只需改 `MARKET_INTERVAL`）|

---

## 11. 术语

| 术语 | 含义 |
|------|------|
| 数据面 / 控制面 | 分别指数据采集加工链路 / 决策执行链路 |
| bar 关闭 | K 线周期结束、数值确定的时刻；WS 事件中由 `k.x == true` 标志 |
| 缺口（gap） | 应存在但未采集到的 bar |
| 回补（backfill） | 通过 REST 历史接口补回缺失 bar |
| 供数接口 | `get_market_context()`，数据面对控制面的唯一入口 |
| 指标快照 | 某一时刻全部指标值与上下文摘要的持久化记录 |
| 订阅集合 | 采集器持有的 symbol 集合，当前为 `{BTCUSDT}` |
