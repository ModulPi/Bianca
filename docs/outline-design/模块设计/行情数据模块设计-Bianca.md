
# Bianca — 行情数据模块设计文档

> 版本：v0.8 | 日期：2026-09-24 | 模块代号：Market Data | 阶段：PoC 延伸
> 状态：**阶段一代码已全部落地，但验收未完成**（§6.1）；阶段一验收 9 项中 **6 项通过、1 项部分验证、2 项未验**（§8，其中 24h 连续运行已由用户明确推迟、断网恢复待用户手动执行），另有 3 项因"数据层独立"要求新增（§8 追加，其中 2 项已达成、1 项部分达成）。**未验项不得算作通过 —— 阶段一尚未收尾。**v0.4：全量历史回补完成（4,778,552 行 / 809.2 MB / 覆盖率 99.8197%），修回补并发统计丢失更新，容量估算按实测行宽修正。v0.5：ADR-010 标记为与产品要求冲突，新增提案 ADR-017「数据面作为独立运行单元」；上位架构文档的缺口已定位（§9.2）。v0.6：ADR-017 缺口 1 落地 —— 数据面有了独立入口（`python -m agent.market`）与单实例锁，10 项实测全 PASS；同时实测确认跨进程观测不可用（缺口 4 不只是"端点归属"，是"读不到"）。v0.7：ADR-017 缺口 2 落地 —— 进程守护用「重复唤醒 + `IgnoreNew`」实现（Windows 计划任务），三项实测全 PASS；实现期实测暴露计划任务两个"注册成功但不生效"的静默陷阱（§6.1 纠正 #5），并记录一个未解盲区：卡住但不死的采集器不会被救回。**v0.8（2026-09-24）：止住观测面的跨进程谎报 —— `/health` 与 `/market/status` 重排为「顶层只放跨进程事实 + `session` 装进程内快照」，新增只读锁探针区分"进程没了"与"活着但停滞"，`gap_count_24h` 从"本进程补齐数"更正为"库里缺了多少"（旧口径让一条阶段一验收可以空洞通过）。线上实测：风险 #1 的告警**首次真的响**（§6.1 #31、纠正 #6）**
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

新增 `GET /api/v1/market/status`，沿用 `/api/agent/status` + `RunnerSnapshot` 风格（实际实现字段，`MarketStatusResponse`）。

**分区原则（2026-09-24 起）：顶层只放跨进程可信的事实，进程内快照一律收进 `session`。** 采集器独立成进程后（ADR-017），进程内字段在 API 进程里必然是 `false`/`null`，与跨进程字段平铺在一起就会被读成"采集器没在跑"—— 实测过：数据在流、`lag 9s`，而 `collector_running=false`。收进 `session` 且在没有视角时给 `null`，是让这类谎报在结构上说不出口（`null` 说"我没有这个视角"，`false` 说"它在跑但没连上"，不是一回事）。

| 字段 | 含义 | 读取面 |
|------|------|--------|
| `last_bar_open_time` | 最后落库的 bar 时间 | 库 |
| `last_closed_bar_open_time` | **此刻**最近一根已收盘 bar — 与上一行对比即可看出是否落后 | 库 |
| `lag_seconds` | `now - last_bar_open_time - interval`，**核心健康指标** | 库 |
| `bars_count_24h` | 24h 已落库行数（从库算，非计数器，重启不失真）| 库 |
| `bars_expected_24h` | 同窗口**应有**行数。与上一行并列给出，是为了让 `actual > expected` 这种异常看得见，而不是被子句 `max(…,0)` 吃掉 | 库 |
| `gap_count_24h` | 24h **应有而未落库**的 bar 数（缺口方向：越大越坏）| 库 |
| `data_flowing` | `lag` 未超阈（即 `ok`）。`warming_up` 映射为 `false` —— bool 表达不了三态 | 库 |
| `collector_owner` | `self` / `other` / `none` / `unknown` —— 谁在写这个库（见下）| 内核锁 |
| `lock_file` | 锁文件的**绝对**路径。它由进程 CWD 解析，API 与计划任务 CWD 不一致时会指着两个不同文件，摆出来才看得出错配 | 配置 |
| `database` / `symbols` / `interval` / `interval_error` | 库文件名（确认没连错库）、订阅标的、周期及其配置错误 | 配置 |
| `session` | **本进程**采集器的自述，本进程没有采集器会话时为 `null` | 进程内 |
| └ `connected` | WS 是否处于连接态 | 进程内 |
| └ `bars_written_session` / `reconnects_session` / `gaps_filled` | 本进程内写入行数 / WS 重连次数 / **补齐**的缺口 bar 数（方向与 `gap_count_24h` 相反）| 进程内 |
| └ `backfill_running` / `backfill_last` / `backfill_error` | 本进程发起的历史回补进度与结果 | 进程内 |
| └ `last_gap_check_at` / `last_write_at` / `last_error` / `last_error_at` / `started_at` | 时序诊断 | 进程内 |

`collector_owner` 是**只读探测锁文件**得出的：Windows 上别的进程持锁时读第 0 字节会抛 `PermissionError`（本机子代理实测确认，含"读 0 失败但读 1 成功"的双重确认以免把 ACL 误判成锁），不涉及加锁、无竞态。它把两类以前分不开的故障分开了：

- `lag` 在涨 + `owner=none` → 进程没了，计划任务会在下一次心跳把它拉回来；
- `lag` 在涨 + `owner=other` → 进程活着但停滞（**这正是守护机制的已知盲区**，需要人介入）。

POSIX 的 `flock` 是劝告锁，探测不出结果，那里返回 `unknown` —— 只影响能否把故障分得更细，不会产生误报（见下）。

配套 `POST /api/v1/market/backfill` 手动触发历史回补（幂等可重入，已在跑则直接返回）。

**健康判定**：`/health` 的 `market` 只在两件事上取值 —— 库里的数据新不新鲜，以及内核锁在不在别人手里，**不看进程归属**（可用的信道只有这两条是跨进程读得到的）。`lag_seconds` 超过 `max(180s, 3 个周期)` 时为 `error` → 整体 `degraded`；库里没有任何 bar 时，**持锁**判 `warming_up`（冷启动回补中），**锁空闲**判 `error`（既没有数据也没有采集器）。阈值随周期缩放：写死 180s 在 `MARKET_INTERVAL=1h` 下会在每小时约 94% 的时间里误报。没有 `disabled` 这个状态 —— 数据面停摆就是风险 #1 本身，不存在"健康地不采数据"。

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
| `MARKET_COLLECTOR_AUTOSTART` | `true` | lifespan 中是否自动启动**进程内**采集器。**2026-09-24 起它不再是 `/health` 的判定依据** —— 原先它一置 false，`market_health()` 就短路成 `disabled`，而 `/health` 只在 `error` 时转 `degraded`，于是告警静默失效（§6.1 纠正 #6）。数据面迁到独立进程后线上取值 `false`（否则会出现两个采集器抢锁），"在不在采"改由库与内核锁回答 |

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
- **状态:** ⚠️ **与产品要求冲突，待重开（2026-09-23）** —— 原状态 ✅ 已采纳
- **决策:** 采集器作为独立 asyncio 任务，与 `AgentRunner` 并列，各自独立生命周期与快照
- **替代方案:** 独立进程/容器（隔离更好，但需独立部署与重启策略）
- **已知冲突（2026-09-23 追加）:** 本条只实现了**逻辑**独立（不依赖 LLM 就绪、不随 AgentRunner 停机而中断），**未实现生命周期与部署独立**。产品要求是「数据层必须做成独立模块、自动化运行、不依赖 Agent」—— 本条恰好把「独立进程/容器」列为被否方案，与要求正面冲突。**具体代价已实测暴露**：采集器进程被启动它的 shell 链牵着（实测发现它挂在上一轮 agent 会话的 bash 链下，祖先进程已消失），因此**关机不会自愈、重启无人拉起、关 IDE 是否连带被杀不确定** → **当前形态无法无人值守**（§8）。重开方向见 ADR-017。

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

### ADR-017: 数据面作为独立运行单元（拟重开 ADR-010）
- **状态:** 🔶 **部分实施中（2026-09-24）** | **日期:** 2026-09-23（2026-09-24 更新）
  - 缺口 1（独立入口）**已解**：`python -m agent.market` / `[project.scripts] bianca-market`，附单实例锁 `agent/market/lock.py` 与验收脚本 `scripts/verify_market_standalone.py`（10 项全 PASS）。
  - 缺口 2（进程守护）**已解（2026-09-24）**：`agent/market/task.py` 生成 Windows 计划任务描述符（重复唤醒 + `IgnoreNew`，非 `RestartOnFailure`），`scripts/install_market_task.py` 注册/卸载/查状态，验收脚本 `scripts/verify_market_supervision.py` 三项全 PASS（拉起并落库 / 防重 / 硬杀后下一次心跳复活并续写）。**该机制的两处实测陷阱见 §6.1 纠正 #5。**
  - 缺口 4（观测面耦合）**部分已解（2026-09-24）**：谎报止住了 —— 两个观测端点现在只回答"数据在流吗"，进程内快照收进 `session` 且无视角时给 `null`，`/health` 的 `disabled` 短路拆除（详见 §2.7）。**仍耦合**：端点依旧挂在 API 的 router 上，独立的数据面进程不提供任何端点。
  - 供数接口、部署形态（缺口 3/核心待决）均未动。
  - **遗留的守护盲区（未解）**：重复唤醒按"任务还在不在"判死活，采集器**活着但卡住**（WS 静默停滞、不再落库）时任务仍算在跑，唤醒被 `IgnoreNew` 吞掉。覆盖它需要另一个独立心跳（例如库内 `max(time)` 的年龄），见 §8 该项的 `[~]` 说明。
- **背景:** 产品要求「数据层后期必须做成独立模块、自动化运行，而不是依赖 agent。数据层与 agent 决策层不要混为一谈」。ADR-007 已实现**逻辑**分离（代码依赖方向干净：`agent/market/` 只依赖 `agent.config`，不碰 `llm`/`graph`/`risk`，已实测验证），但 ADR-010 选择了同进程 → **运行期与部署期仍是耦合的**。
- **当前实测缺口（四条，均须先解）:**
  1. ~~**无独立入口**~~ —— **已解（2026-09-23）**：`agent/market/__main__.py` + `[project.scripts]`。附带引入单实例锁，因为独立入口一开，"API 内的采集器"和"独立进程的采集器"就可能同时存在；
  2. ~~**无进程守护**~~ —— **已解（2026-09-24）**：Windows 计划任务（`agent/market/task.py` + `scripts/install_market_task.py`），机制为「每 5 分钟重复唤醒 + `MultipleInstancesPolicy=IgnoreNew`」。原先"四种自启机制全空"的实测结论仍成立 —— 它说明**当时确实无人守护**，也是这条必须补的理由；
  3. **部署形态是单服务** —— 架构文档 §2 容器图与系统设计文档 §6 `docker-compose.yml` 都只有 `api` 一个服务，没有数据面进程；
  4. **观测面耦合** —— `/api/v1/market/status`、`/api/v1/market/backfill` 挂在 API 的 router 上，数据面独立后这些端点的归属需要重新设计。**2026-09-23 实测确认这不只是"归属问题"，而是"读不到"**：`market_status_detail()` 里共 **15 个字段**取自**进程内**快照（`collector_running` / `connected` / `bars_written_session` / `reconnects_session` / `last_write_at` / `started_at` 等），跨进程必然是 `false`/`null`。实证：线上采集器正在写（`lag_seconds=24`），而独立进程执行 `--status-once` 报 `collector_running=false`。**2026-09-24 已按此重排**（本 ADR 的缺口 4 从"未动"变"部分已解"）：顶层只留跨进程读得到的字段，进程内快照整体收进 `session`，本进程没有采集器会话时给 `null` —— 剩下**仍耦合**的是端点归属（数据面自己不开端点）。
     **该条同时更正了一个当时的错误说法**：原文把 `gap_count_24h` 与 `lag_seconds` / `last_bar_open_time` / `bars_count_24h` 并列称为"DB 派生字段"，但它当时取的是 `snap.gaps_filled`（本进程**补齐**的缺口数）—— 既不是 DB 派生，也不是 24h 口径，而且方向与原意相反。已改为真从库里算：`count_bars(窗口起点, 最后一根已收盘 bar) - count_since(窗口起点)`，即"**缺了多少**"。
     跨进程可用的信道只有两条 —— **库**（数据流动）与**内核锁**（进程归属），所以观测天然要落在他们身上，这**替供数接口形态给出了一个证据**：倾向方案 (a) 与此一致。
- **核心待决问题：供数接口的形态。** ADR-007 定的"单一供数接口"`get_market_context()` 目前是**同进程函数调用**，这只有在同进程下成立。跨进程后必须二选一：
  - **(a) 共享库直读** —— 控制面以只读方式打开 `data/market.db`（WAL 支持多读者），数据面与控制面之间**无运行时接口**。耦合最低，且天然满足"离线可跑、独立观测"。
  - **(b) IPC / HTTP 端点** —— 数据面自带服务端点，控制面按客户端消费。语义更清晰、可跨机，但引入网络边界与新的失败模式。
  - 倾向 **(a)**：PoC 规模下 SQLite + WAL 的只读并发毫无压力，且能避免"数据面挂了控制面也拿不到数据"的伪耦合。待评估跨进程只读下的 WAL checkpoint 行为。
- **替代方案:** 维持同进程（ADR-010 现状）—— 代价是无法无人值守，与产品要求冲突。

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
| 21 | `/api/v1/market/status` | ✅ 实机返回 `lag_seconds=4`、`database=market.db`（当时 `connected=true` 是**同进程**读到的 —— 迁移到独立进程后该字段移入 `session`，见 #31）|
| 22 | `/api/v1/health` 接入 | ✅ `market: "ok" / market_detail: "lag 40s"`（当时的"采集器停跑 → `degraded`"是靠 `MARKET_COLLECTOR_AUTOSTART` 短路假装的，迁移后失效并已拆除，见 #31）|
| 23 | WAL 生效 | ✅ 运行期 `market.db-wal` / `-shm` 存在（ADR-008 前提成立）|
| 24 | `websockets` 依赖声明 | ❌→✅ 原先只是 `uvicorn[standard]` 的传递依赖，采集器直接 import 后已在 `pyproject.toml` 显式声明 `websockets>=15.0` |
| 25 | 会话泄漏修复 | ✅ 修前 `test_health` 必现 `Unclosed client session`，修后消失 |
| 26 | **全量历史回补** | ✅ `scripts/backfill_market_history.py`，4,788 分片并发 5，**825s 跑完**，`failed_chunks=0`，`rows_received=4,778,533` |
| 27 | **落库校验** | ✅ **4,778,552 行 / 809.2 MB**（+ WAL 5.0 MB）；`distinct(time,symbol,interval) == count` 无重复、OHLCV 无空值、`PRAGMA integrity_check=ok` |
| 28 | **覆盖率** | ✅ **99.8197%**（对理论分钟格子）。35 段缺口共 8,632 分钟，全部落在 2017-2021；2022/2024/2025/2026 一根不缺 |
| 29 | **残余缺口归因** | ✅ 复拉 2018-02-07→10（格子 5,760 分钟）：Binance 仅返回 3,734 根、`rows_inserted=0` —— **缺口是交易所自身停机，非本系统丢失**。独立交叉验证：文档记载的停机窗口与实测缺口逐点吻合（见 §6.1 纠正 #4）|
| 30 | **回补与采集并发** | ✅ 采集器与 14 分钟批量写入并行全程无碍：`reconnects_session=0`、`last_error=null`、`/api/v1/health` 报 `market: ok / lag 11s` |
| 31 | **跨进程观测的真实性**（迁移到独立进程后重验） | ✅ 2026-09-24。`python -m agent.market --status-once`：`data_flowing=true`、`collector_owner="other"`、`session=null`、`gap_count_24h=0`、`bars_count_24h=1439 == bars_expected_24h=1439`；`/api/v1/health` → `market: "ok" / "lag 1s"`（不再是 `disabled`）。**风险 #1 的告警首次真的响**：停用计划任务 + 硬杀采集器 → 2 秒内 `collector_owner` 变 `none`，约 100s 后 `market: "error" / "stale: lag 209s > 180s"`；恢复（重新启用任务 + `/Run`）→ `market: "ok"`、`lag 55s`、`owner: "other"`、`gap_count_24h=0`（约 5 分钟的空窗被自动回补抹平）。`scripts/verify_market_standalone.py` 重跑 10 项仍全 PASS；全套 131 项通过 |

**实测纠正的既有认知（6 条）：**

1. `_client.py` 同时设置 `wsProxy` + `wssProxy` 的后果需要重新定位：ccxt **不在构造时**报错，只有真正开 WS（`check_ws_proxy_settings()`，由 `watch_*` 调用）才抛 `InvalidProxySettings`。已实测复现并修掉（只保留 `wssProxy`，币安行情 WS 是 `wss://`）。另注：删除 `market_stream.py` 后仓库内已无任何 ccxt WS 调用点，此修复属预防性。
2. `insert_rows` 的计数**不能靠 `rowcount`**：实测在 `executemany` + `ON CONFLICT DO NOTHING` 下，Python 的 sqlite3 驱动即使首次插入也返回 0。已改为"先查该范围已存时间戳、只写缺失行"——精确计数，且重放退化为纯读不写。
3. **`stats.rows_inserted += await repo.insert_rows(rows)` 是丢失更新**。增强赋值会**先读左值、再 await**：并发 worker 全都读到同一个旧值，各自加完再写回，互相覆盖。全量跑时它报 416.1 万而库内实际 477.8 万，**少报 61.8 万**（`insert_rows` 的返回值本身是对的，丢在累加环节）。已在 `tests/test_market_backfill.py` 用临时库 + `concurrency=5` 固定住交错窗口复现（修复前 `rows_inserted=50`，应为 250），修法为先 `await` 拿到增量再同步累加。**教训：库里数据正确 ≠ 统计口径正确**，验收若只看 `rows_inserted` 会被误导。
4. **残余缺口归因必须落到"可交叉验证的外部事实"，不能停在像是合理的猜测上。** 第一版把最大的缺口（2018-02-08，2,011 分钟）写成"币安异常交易停机"——错了。查证后确认是**副本数据库集群失步导致的停机升级**（CZ 当时说明 replica DB cluster 数据不同步、需从主库全量重同步；维护自 UTC 2/8 00:00 左右开始，官方宣布 2/9 04:00 UTC 恢复，后又因云厂商遭 DDoS 延后）。而"异常交易"（irregular trading activity）是 2018 年 2 月下旬**另一桩**钓鱼 + Viacoin 操纵事件，两者被我混为一谈。**修正后反而得到了更强的结论**：实测缺口 `2018-02-08 00:28 → 2018-02-09 10:00` 与公开报道的停机窗口逐点吻合（起于维护开始时刻、止于宣布恢复时刻之后）—— 这是独立于币安 API 的**第三方证据**，比"复拉发现没数据"更能证明缺口来自交易所而非本系统。**教训：一个看起来合理的归因（"肯定是交易所停机"）恰好正确时，最容易停止验证；而错在细节上的归因会在日后被引用时变成假事实。**
5. **计划任务的两个"照单全收但不干活"陷阱**（ADR-017 缺口 2 实现期实测；两者都不产生任何报错，属于最难发现的一类失败）：
   - **`<Repetition>` 挂在 `<LogonTrigger>` 上不生效。** schtasks 注册成功、导出后该项也**仍在**，但调度器根本不安排下一次运行 —— `下次运行时间` 恒为 `N/A`，观察 3 分钟零触发（ticks 1→1→1）；补 `<Duration>` 也一样。改成 `<TimeTrigger><StartBoundary>…</StartBoundary><Repetition>…` 才真的反复触发（`下次运行时间` 有真实值、ticks 稳定递增）。`LogonTrigger` 于是只保留"登录后立刻启动"这一个职责。
   - **`<RestartOnFailure>` 不生效。** 注册后该项保留在任务定义里，但无论手动启动还是由真触发器（TimeTrigger）启动，动作以退出码 1 失败后**没有任何重试**（间隔设 1 分钟，分别观察 3.5 分钟与 6 分钟）。计划任务操作日志（`Microsoft-Windows-TaskScheduler/Operational`）默认关闭，未提权也拿不到调度器的判断依据，无法进一步定位。
   - **这两条合起来改变了一个设计决定**：崩溃恢复不再依赖 `RestartOnFailure`，改用「重复唤醒 + `MultipleInstancesPolicy=IgnoreNew`」—— 采集器活着时唤醒被 IgnoreNew 吞掉，死了则由下一次唤醒拉起（实测：硬杀后下一次心跳即拉起并继续落库，`rows 1000 → 2881`）。它顺带覆盖了 `RestartOnFailure` 逻辑上修不了的洞：任务被系统正常结束不算"失败"，不触发重启，而重复唤醒照样能救回来。
   - **教训：`schtasks` 注册成功只说明 XML 合法，不代表设置被执行。** 每一项守护设置都必须单独构造一个**能观测的失败场景**去证明它真的生效 —— 否则就是在无人值守的核心上放了一个静默失效的开关。
6. **观测面的"谎报"不会自己暴露，而它恰好长在告警通道上。** 采集器迁出 API 进程后，观测端点里 15 个取自进程内快照的字段在 API 进程里恒为 `false`/`null`/0，而数据其实在流（实测 `lag_seconds=9` 配 `collector_running=false`）。更糟的是 `/health` 的告警**在迁移前就已经失效**：`market_health()` 一看到 `MARKET_COLLECTOR_AUTOSTART=false` 就返回 `"disabled"`，而 `routes.py` 只在 `market == "error"` 时置 overall 为 `degraded` —— 于是迁移后 `/health` 永远报 `disabled`、overall 永远 `ok`，**采集器真的死了也不会响**。这条被同一条迁移揭开，而不是被任何测试发现：#22 记的"采集器停跑 → `degraded`"从来靠的是那个短路，短路一拆就露馅。**教训：被"假通过"覆盖的验收项与未验证的验收项等价，甚至更坏 —— 它会让人以为这块已经验过了。** 拆掉的是一个从来没抓到过 `_loop` 静默死亡的 `not snap.running` 分支（`running` 只在 `stop()` 里清），拆它没有损失；顺带把 `gap_count_24h` 从 `snap.gaps_filled`（本进程**补齐**数，方向与原意相反）改成真从库里算的"**缺了多少**"—— 旧的写法让阶段一那条验收可以**空洞通过**（见 §8）。

**一个反直觉但重要的事实：`backfill_history()` 补不了"中间的洞"。** 它的续传起点是库内 `max(time) + 1 周期`（§6.1 #19 已验证该短路行为），因此只要库里已有最新 bar，它就判定"已补到最新"直接返回 0 请求 —— 而那种「2017 一段 + 近期一段」的双岛形态恰恰是它完全无法处理的。**启动时的自动回补（`MARKET_BACKFILL_ON_START`）同理，只能跟随尾部，不能自愈历史空洞。** 全量补洞必须走 `backfill_range()` 显式给区间；`POST /api/v1/market/backfill` 端点同样受此限制。这是设计上的已知边界，非缺陷，但运维上要知道：**洞一旦形成，只能靠 `scripts/backfill_market_history.py` 手动补。**

**一个需要说明的边界竞态（非缺陷）：** bar 收盘推送恰在分钟边界发出，与"停机/重连"可能相撞，导致最后一根边界 bar 本次未落库。这不是丢数据：该缺口已包含在 2 天 lookback 内，下次启动的缺口巡检会补上——实测 #18 的 `gaps_filled` 正是这个机制在起作用。

---

## 7. 关键风险与对策

| # | 风险 | 影响 | 对策 |
|---|------|------|------|
| 1 | **采集器静默死亡** | 数据断流但无人知 | 计划任务每 5 分钟唤醒拉起（`IgnoreNew` 保证不重入，ADR-017 缺口 2）；`lag_seconds` 指标 + `/health` degraded；缺口检测兜底。**告警通道本身已于 2026-09-24 排掉一个静默失效**：此前 `/health` 的判定被 `MARKET_COLLECTOR_AUTOSTART` 短路成 `disabled`，采集器真死也不会响（§6.1 纠正 #6、#31 实测响起）。**残余盲区**：进程活着但卡住时唤醒会被 `IgnoreNew` 吞掉，尚无独立心跳识别（见 §8）；`collector_owner=other` + lag 上涨是它的指纹，但现在只能看见、不能自动救 |
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
- [ ] 连续运行 24h，`gap_count_24h` 归零（或残余缺口全部已归因）—— **未跑**（§6.1 #17 最长连续运行 190s），**且当前设备条件不支持，已由用户明确推迟**。`gap_count_24h` 已于 2026-09-24 改语义：现在是"最近 24h **应有而未落库**的 bar 数"，从库里算（此前取 `snap.gaps_filled`，本进程补齐数 —— 那条口径下这条验收可以**空洞通过**，因为它读的是"补了多少"而不是"还缺多少"）。措辞也从"补齐至 0"改为"归零，或残余缺口全部已归因"：**Binance 自身停机造成的缺口补不出来**（§6.1 #29 已证），此时 `gap_count_24h` 不可能为 0，判据必须是"残余缺口已归因"而不是一个恒不可达的 0。**计时已于 2026-09-24 00:16 迁移时清零重算** —— 数据本身连续（新进程从 `max(time)` 续上，5 分钟空窗由自动回补补掉），但"连续跑 24h 不死"这条断言必须从守护形态下重新起算，那才是这次迁移真正要证的东西
- [x] 正常态 `lag_seconds < 90` —— ✅ 实测 4s / 16s / 40s
- [x] 进程重启后自动回补停机期间的缺口 —— ✅ §6.1 #18（2881 / 2 / 3）
- [ ] 断网 → 恢复，采集自动续上且数据无洞 —— **未测**（代理抖动下的重连/重试逻辑已实现，退避未被真实触发）。**2026-09-24 由用户手动断网执行**：`market_health()` 的判定逻辑已在独立进程形态下实测有效（§6.1 #31），本次断网是该逻辑第一次面对真实退避重连
- [x] 采集器死亡时 `/health` 在 180s 内变为 `degraded` —— ✅ **2026-09-24 实测响起**（§6.1 #31）：停用计划任务 + 硬杀采集器，约 100s 后 `market: "error" / "stale: lag 209s > 180s"`。判据同时升级：`data_flowing` 与 `collector_owner` 分开给出，`owner=none`（进程没了，计划任务会拉回来）与 `owner=other`（**活着但停滞**，守护机制的盲区，需人介入）不再混为一谈。阈值从写死的 180s 改为 `max(180s, 3 个周期)`——写死值在 `MARKET_INTERVAL=1h` 下会每小时约 94% 的时间误报

图例：`[x]` 已实测通过 / `[~]` 部分验证 / `[ ]` 未验证。

**追加验收项（2026-09-23，因产品要求"数据层独立、自动化运行"而新增）：**

- [x] **数据面可脱离 API 进程独立运行** —— **2026-09-23 达成**。`python -m agent.market` + `[project.scripts] bianca-market`（ADR-017 缺口 1）。`scripts/verify_market_standalone.py` 10 项实测全 PASS（用临时库，线上采集器在跑时也可安全执行）：独立启动并落库 2881 根、跨进程互斥（退出码 3 且报错含持有者 pid）、硬杀后锁由内核释放、Ctrl-Break 优雅退出（退出码 0）
- [~] **进程守护：关机/崩溃后自动恢复** —— **机制已于 2026-09-24 实测通过，但覆盖面有已知盲区**。`scripts/verify_market_supervision.py` 三项全 PASS（用临时库 + 一次性任务名，无需提权，线上采集器照常运行）：① 计划任务能拉起采集器并落库；② `IgnoreNew` 挡住第二次实例；③ 硬杀进程后，下一次心跳把它复活并继续落库（实测 `pid 22168 → 11124`，`rows 1000 → 2881`，`lag 131s`）。**盲区**：唤醒按"任务还在不在"判死活，采集器**活着但卡住**（WS 静默停滞、不再落库）时任务仍算在跑，唤醒被 `IgnoreNew` 吞掉，无人救它 —— 覆盖它需要另一个独立心跳（如库内 `max(time)` 的年龄）。另："关机后自动恢复"依赖 `LogonTrigger`，即**必须有人登录**（`InteractiveToken`）；真做到"开机即采集、无需登录"需改用存储凭据
- [x] **实测确认采集器进程的稳定归属** —— **2026-09-24 达成**。线上采集器已迁到独立入口并由计划任务托管，父进程是任务计划服务（实测 `pid 21156 ← 2064`），不再挂在启动它的 shell 链下。原先那条"老进程不持单实例锁、可与新采集器并行而不报警"的窗口也随迁移关闭（迁移时实测：老进程被杀后新实例立刻拿到锁）。**迁移方式**：`.env` 置 `MARKET_COLLECTOR_AUTOSTART=false` → 停掉 API 内的老采集器 → `scripts/install_market_task.py --install --start`。**迁移期实测**（在真任务上做的杀活验收，不只是验收脚本）：硬杀 `pid 16176` → 下次心跳（00:16:30）拉起 `pid 21156`，日志 `resuming backfill for BTCUSDT from stored max(time)`、WS 重连成功、`lag_seconds` 回到 39s

**上线前必须先补的两项（原三项中的"全量历史回补"已于 2026-09-23 完成）：** **24h 连续运行**、**断网恢复**。前者决定数据底座是否可信，后者决定无人值守时会不会静默断流。

> 2026-09-24：**24h 连续运行已由用户明确推迟**（当前设备条件不支持连续开机 24h），**不计入阻塞项**。断网恢复由用户手动断网执行。两项未完成前，阶段一按"数据底座尚未验收"对待 —— 这不是措辞问题：**未验证的验收项与验过的等价性只存在于纸面上**。

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
| `agent/market/__main__.py` | ✅ 新增。数据面独立入口（ADR-017 缺口 1）：`python -m agent.market`。`--status-once` 只读打一份状态（stdout 纯 JSON、说明走 stderr）、`--status-interval` 周期打状态行。**状态行刻意不用 `market_health()`** —— 那里只需要一句"连上了没"的粗话，而 `market_health()` 走的是共享判定、还牵扯锁探针。信号用 `signal.signal` + `call_soon_threadsafe`（Windows 的 ProactorEventLoop 不支持 `loop.add_signal_handler`），第二次信号硬退出。（2026-09-24：`connected` 等字段移进 `session` 后，状态行读 `session`；被守护的进程**没有终端**，状态行是它唯一的遥测，一条会说谎的状态行比没有更糟 —— 改结构时它不会报错，只会静默降级成 `connected=no reconnects=None`，所以这条接线必须与结构改动同批改）|
| `agent/market/lock.py` | ✅ 新增。采集器单实例锁。用 OS 管理的文件锁而非 PID 文件判活 —— Windows 上 `os.kill(pid, 0)` **会真的终止目标进程**，不能探活；内核锁随进程退出自动释放，故不存在"陈旧锁"。锁文件格局是**第 0 字节加锁、第 1 字节起写 pid**，被 Windows 强制锁逼出来的（字节范围锁对任何其他句柄生效，含本进程新开的句柄，线索写在第 0 字节谁都读不到，连持有者自己都吃 `PermissionError`）。报错文案不叫人删锁文件：实测持锁期间 `unlink` 报 `PermissionError`，且锁绑句柄不绑路径 |
| `agent/market/lock.py`（补）| ✅ 2026-09-24 增补**只读锁探针** `probe_lock_path()` / `probe_lock_state()`：旁观者不加锁地问"现在有没有采集器"。这是"**卡住但不死**"唯一能自动分辨的信号（配数据新鲜度：lag 涨 + 持锁 = 活着但停滞；lag 涨 + 空闲 = 进程没了）。判据是"读第 0 字节失败**且**读第 1 字节成功"才算 `held` —— 不能只看 PermissionError，ACL/杀软/只读介质给的是同一种（CPython 经 CRT 只拿得到 errno 13，`winerror` 是 `None`，区分不了），那就只能报 `unknown`。另外 `os.close(fd)` 必须在 `finally`（异常是在 `read` 上抛的、不在 `open` 上，每次调用漏一个 fd）。**`__del__` → `release()`** 也是这一批加的：`CollectorLock(path).acquire()` 这种不接住返回值的一次性写法会留下**幽灵持有者**（句柄不关、锁不放、对象已不可达），把真正的采集器挡在门外 —— 这个坑是写"探针不该顺手加锁"的测试时被测试逼出来的 |
| `scripts/verify_market_standalone.py` | ✅ 新增。独立运行的验收检查，**永远用临时库**，所以线上采集器在跑时也能安全执行。10 项实测全 PASS：独立启动并落库 2881 根 / 跨进程互斥（退出码 3 且报错含持有者 pid）/ 硬杀后内核释放锁、新实例照样起得来 / Ctrl-Break 优雅退出（退出码 0）|
| `tests/test_market_lock.py` | ✅ 新增 15 个单测，不出网（`_loop` 换成空转）。含用**真子进程被 kill** 验证"锁随进程死亡自动释放"，以及一条 `skipif(nt)` 钉住"持锁期间删不掉锁文件"这个事实 —— 报错文案的前提若变了这里会红。2026-09-24 补锁探针 6 条（真子进程持锁 → `held`；空闲/零字节文件 → `free`；目录 → `unknown`；非 Windows → `unknown`）、一条钉住"**别把 `_read_holder()` 当探针**"（同一个持锁文件，它读得到线索、而探针说的是 `held` —— 拿它判活会 100% 报空闲）、一条"探针不加锁"（探针每次 `/health` 都跑，顺手加锁就会有把采集器关在门外的窗口）、以及一条 `__del__` 释放句柄（幽灵持有者）|
| `agent/market/collector.py`（补）| ✅ 2026-09-24 重排判定与响应结构。**共享判定** `_flow_verdict()` —— 让 `/health` 与 `/market/status` 由同一处得出结论，否则两个面会互相矛盾；**判定只看数据流动与内核锁，不看进程归属**（跨进程读得到的只有这两条信道）。`market_health()` 去掉 `MARKET_COLLECTOR_AUTOSTART` 短路与 `not snap.running` 分支（后者**从来没抓到过 `_loop` 静默死亡** —— `running` 只在 `stop()` 里清，留着会让人以为它承重），词汇表收敛为 `ok`/`warming_up`/`error`，**没有 `disabled`**（数据面停摆就是风险 #1 本身）。阈值从写死的 `_STALE_LAG_S=180` 改为 `max(180s, 3 个周期)`：写死值在 `MARKET_INTERVAL=1h` 下每小时约 94% 的时间误报，此前被 `disabled` 短路掩盖着。空库用锁探针一分为二：**持锁** → `warming_up`（冷启动回补中），**空闲** → `error`（既没有数据也没有采集器）。`market_status_detail()` 顶层只留跨进程字段，进程内快照收进 `session`（无会话时 `null`）；`gap_count_24h` 改为真从库里算"**缺了多少**"并并列给出 `bars_expected_24h`（好让 `actual > expected` 这种越窗回补异常看得见，而不是被 `max(…,0)` 吃掉）|
| `agent/api/schemas.py`（补）| ✅ 2026-09-24 `MarketStatusResponse` 重写为新结构（`collector_owner` / `data_flowing` / `session`），新增 `MarketSessionResponse`。`HealthResponse.market` 的默认值 `"disabled"` 成为死值 → 改 `"error"`（默认值应偏向大声失败）|
| `scripts/smoke_market_collector.py`、`scripts/install_market_task.py`（补）| ✅ 2026-09-24 随结构同步改取值路径（前者按顶层/`session` 分开取值并在 `session is None` 时提前返回；后者的 `--status` 关键行过滤加入 `data_flowing` / `collector_owner` / `bars_expected_24h`）。这两处都是**会静默降级**的消费方：`detail["connected"]` 改从 `session` 取，不改就是 `KeyError`（好一点）或读到 `None`（更坏）|
| `pyproject.toml` | ✅ 新增 `[project.scripts] bianca-market`，与 `-m` 入口共用同一个 `main()` |
| `agent/market/__main__.py`（补）| ✅ 增补 `--log-file`：带 `RotatingFileHandler`（5 MB × 3 份，UTF-8）。理由：被守护的进程**没有终端**，不给它落文件就看不到重启与退出原因 —— 守护的价值一半在"看得见" |
| `agent/market/task.py` | ✅ 新增。计划任务描述符（ADR-017 缺口 2），**纯函数生成 XML，不碰系统** —— 所以能在任何平台单测，注册/卸载留给脚本。恢复机制是「重复唤醒 + `IgnoreNew`」。每一处非默认设置都对应一个会让无人值守静默失效的默认值：`ExecutionTimeLimit=PT0S`（默认 72h 会静默杀掉长跑任务）、两条电池设置（默认拔电源即停）、`StartWhenAvailable`、`StopAtDurationEnd=false`。两个实测陷阱写在 docstring 里：`<Repetition>` 挂 `LogonTrigger` 上不生效、`RestartOnFailure` 不生效 |
| `scripts/install_market_task.py` | ✅ 新增。注册/卸载/查状态（`--dry-run` / `--install` / `--uninstall` / `--status`）。XML 以 UTF-16 写临时文件再 `schtasks /Create /XML`。**`--status` 额外跑一次 `--status-once`** —— 计划任务的"上次运行结果"只说进程怎么退出的，不告诉你数据还在不在流 |
| `scripts/verify_market_supervision.py` | ✅ 新增。守护的验收检查，同样是临时库 + 一次性任务名，**无需提权**、线上采集器不受影响。注入临时库的办法是换掉 `<Actions>` 块而**保留全部 Settings/Triggers**（用 `.cmd` 包一层），这样验的就是真实设置 |
| `tests/test_market_task.py` | ✅ 新增 22 个单测，全部离线。重点钉住两类东西：会**静默失效的默认值**（限时、电池、`IgnoreNew`、`StartWhenAvailable`），以及上面两个实测陷阱的**反向断言**（`LogonTrigger` 上不得有 `Repetition`、描述符里不得有 `RestartOnFailure`），免得日后有人以为它们在兜底 |
| `tests/test_market_storage.py`（补）| ✅ 2026-09-24 五个 `market_health` 测试全部重写（`collector` 参数没了、绑定 AUTOSTART 的那个按决策删除），改由 `lock_free` / `lock_held` 两个 monkeypatch fixture 摆布锁状态；新增一条**关键回归** `test_market_health_ignores_the_autostart_switch`（钉住"开关不再是判定依据"）、阈值随周期缩放、空库的三种归宿、空标的列表 → `error`。状态部分用 `_CROSS_PROCESS_KEYS` 冻结顶层 key 集合 —— **按 key 集合断言，防止谎报日后以新名字回来**；另有 `gap_count_24h` 的算术（含 `actual > expected` 不被子句吞掉）、缺口语义不是"补齐数"的回归（对上 `snap.gaps_filled` 那个 bug）、以及坏周期不得 500 |
| `.env.example`（补）| ✅ 2026-09-24 补 `MARKET_COLLECTOR_AUTOSTART` 的真实语义：它只管 lifespan 要不要拉起进程内的采集器，**不再是 `/health` 的判定依据**（原先它一关，`market_health()` 就返回 `disabled`，告警静默失效）|
| 文档大版本号 | ✅ 本设计文档 **v0.8**：v0.3 补 §2.7 可观测字段与健康判定、§6.1 实现期实测、§8 阶段一验收实况；v0.4 补全量回补实测（§6.1 #26-30）、并发统计丢失更新（纠正 #3）、`backfill_history()` 补洞边界 + 缺口归因的外部交叉验证（纠正 #4）、§4 容量估算按实测行宽修正；v0.5 标记 ADR-010 与产品要求冲突、新增提案 ADR-017、定位上位文档缺口（§9.2）；v0.6 落地 ADR-017 缺口 1（独立入口 + 单实例锁）并实测跨进程观测不可用；v0.7 落地 ADR-017 缺口 2（进程守护）并实测暴露计划任务两个静默陷阱（§6.1 纠正 #5）；v0.8 止住观测面的跨进程谎报（§2.7 分区重排 + 锁探针 + `gap_count_24h` 语义更正），实测风险 #1 的告警首次真的响（§6.1 #31、纠正 #6）|

### 9.2 待同步更新（**尚未改动**）

| 目标 | 待更新内容 |
|------|-----------|
| [数据库设计文档](../数据库设计/数据库设计文档-Bianca.md) §2 | 移除"PoC 无 `klines` 持久化"的说明 |
| [数据字典](../数据库设计/数据字典.md) | 新增 `klines`、`indicator_snapshots` 条目 |
| [002_mvp_postgres.sql](../数据库设计/sql/002_mvp_postgres.sql) | `klines` 补齐 `quote_volume` / `taker_buy_base` / `taker_buy_quote`；**移除 90 天 retention 策略**（ADR-015）|
| [架构设计文档](../架构设计/架构设计文档-Bianca.md) | ADR-007~017 并入 §4；§6 MVP 扩展表补充数据面。**另外两处必须改，否则文档与产品要求直接矛盾：** ① §2 容器图只有 `api` 一个容器、§3 拓扑写"Supervisor ← 加载行情"、§5 时序图无采集通道 —— 数据面在架构文档里**完全不存在**，且被画成控制面内部的一个动作；② 需新增"数据面为独立运行单元"的容器与 ADR-017 决策，并写明供数接口形态（ADR-017） |
| [系统设计文档](../../system-design/系统设计文档-Bianca.md) §2.1 / §3 / §6 | §2.1 分层图底部"内存行情缓存"改为"独立行情数据层（独立进程）"；§2.2 目录树 `market_stream.py` 移除、补 `market/`；**§3 模块设计表完全缺数据模块**；§6 部署 `docker-compose.yml` 只有单个 `api` 服务，需体现数据面独立运行单元 |
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
| 10 | **数据面独立运行单元**（重开 ADR-010） | 架构、部署、无人值守 | 🔶 **部分已解（提案 ADR-017）**。逻辑分离已达成（ADR-007，代码依赖方向已实测干净）。运行期独立四项中**三项已解**：独立入口（缺口 1，2026-09-23）、进程守护（缺口 2，2026-09-24）、观测面谎报（缺口 4 的一半，2026-09-24 —— 端点不再报进程内的假状态，但仍挂在 API 的 router 上）；**未解**：部署形态仍是单服务（缺口 3）。核心待决仍是**供数接口形态** —— 同进程函数调用 vs 共享库只读直读 vs IPC/HTTP，倾向前者中的"共享库只读" |
| 11 | 供数接口跨进程形态（ADR-017 的子问题） | 耦合度、失败模式 | ⬜ 待定。若选共享库直读，需评估跨进程只读下的 **WAL checkpoint 行为** 与"数据面未运行时控制面读到陈旧数据的降级语义" |
| 12 | 卡住但仍存活的采集器谁负责救 | 无人值守的残余盲区 | ⬜ 待定。缺口 2 的守护按"任务还在不在"判死活，进程活着但 WS 静默停滞时不触发（见 §8）。2026-09-24 起**至少看得见了**：`collector_owner=other` 配 `lag` 持续上涨就是"活着但停滞"的指纹，`owner=none` 则是"进程没了"—— 两者以前分不开。但"看见"不等于"有人救"，自动救援仍需另一个独立心跳。候选判据：轮询库内 `max(time)` 的年龄，超阈主动退出让计划任务重拉 —— 但"主动退出"与"优雅退出码 0"的语义要分开，否则与 `ExecutionTimeLimit` 那类"正常结束不重启"的坑重逢 |
| 13 | 采集器（持锁）与 `POST /market/backfill`（不持锁）并发写同一个库 | 数据正确性、锁的语义边界 | ⬜ **待定，本次刻意不动**。单实例锁保护的是 `MarketCollector` 实例，管不到 API 的这条路由：采集器由计划任务托管并持锁时，`POST /api/v1/market/backfill` 照样能在 API 进程里跑一次全量回补 —— **同一库上的两个写者**。落库幂等（`ON CONFLICT DO NOTHING`）所以数据不会错，但两个进程同时写 SQLite 会争锁、且回补期间的统计口径会混（两个进程各报自己那份）。**这是迁移前就存在的条件，不是本次引入的**（那时 API 内采集器与同一 API 的回补路由也在同一进程并发写）。把端点改成"检测到别人持锁就拒绝"会砍掉阶段一交付物的一项功能，属另一个决定 |
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
