# FnAgent — 技术选型建议

> 版本：v1.0 | 日期：2026-07-28

---

## 1. 选型总览

```
┌─────────────────────────────────────────────────────────┐
│                     FnAgent 技术栈                        │
├─────────────────┬──────────────────┬────────────────────┤
│ 前端             │ 网关/服务         │ 基础设施            │
│ React 18         │ FastAPI           │ PostgreSQL 16      │
│ TypeScript 5     │ LangGraph 0.2     │ TimescaleDB 2      │
│ Vite 5           │ LangChain         │ Redis 7            │
│ Tailwind CSS 3   │ ccxt 4            │ Ollama             │
│ Recharts         │ python-binance    │ Docker Compose     │
│ lightweight-charts│ pydantic-settings │                    │
│ Zustand          │ SQLAlchemy 2.0    │                    │
└─────────────────┴──────────────────┴────────────────────┘
```

---

## 2. 后端核心技术选型

### 2.1 Agent 编排框架

| 候选 | LangGraph | CrewAI | AutoGen | 自研 |
|------|-----------|--------|---------|------|
| 显式图控制 | ✅ StateGraph | ❌ 隐式 | ❌ 对话式 | ✅ 完全可控 |
| Supervisor 模式 | ✅ 原生支持 | ❌ | ✅ GroupChat | — |
| 生产成熟度 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ | — |
| 持久化/审计 | ✅ Checkpointer | ❌ | ❌ | — |
| 学习成本 | 中 | 低 | 中 | 高 |
| Python 生态 | ✅ 丰富 | ✅ | ✅ | — |

**选择：LangGraph**

理由：
- `StateGraph` + `add_conditional_edges` 天然匹配「策略→风控→执行」管道
- 风控 Agent 作为必经节点，在图中无法跳过（安全硬保证）
- `MemorySaver` / `SqliteSaver` 开箱即用，满足审计需求
- 行业验证：nof1.ai、TradingAgents 等交易系统均采用 LangGraph

### 2.2 LLM 框架

| 候选 | LangChain | 直接调 Ollama API | LlamaIndex |
|------|-----------|-------------------|------------|
| 提示词模板 | ✅ | ❌ | ✅ |
| Tool Calling | ✅ | ❌ | ✅ |
| 与 LangGraph 集成 | ✅ 官方 | 需手动 | 需适配 |
| Ollama 支持 | ✅ langchain-ollama | ✅ HTTP | ✅ |

**选择：LangChain + langchain-ollama**

理由：与 LangGraph 同属 LangChain 生态，无缝集成。提示词模板 + Tool Calling 开箱即用。但注意：只用于 LLM 分析模块，不参与交易决策链。

### 2.3 Web 框架

| 候选 | FastAPI | Flask | Django |
|------|---------|-------|--------|
| 异步原生 (asyncio) | ✅ | ❌ (需扩展) | ⚠️ 3.x 支持 |
| WebSocket 支持 | ✅ 原生 | ❌ | ✅ Channels |
| 自动 API 文档 | ✅ OpenAPI | ❌ | ❌ |
| 类型校验 | ✅ Pydantic | ❌ | ❌ |
| 性能 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ |

**选择：FastAPI**

理由：异步原生对 WebSocket 行情流推送至关重要。Pydantic 校验保证 API 安全。OpenAPI 自动生成文档方便调试。性能足以支撑 1000+ DAU。

### 2.4 交易所 API 封装

| 候选 | ccxt | python-binance | binance-connector |
|------|------|---------------|-------------------|
| 多交易所支持 | ✅ 100+ | ❌ 仅币安 | ❌ 仅币安 |
| 统一 API | ✅ | ❌ | ❌ |
| 现货+合约 | ✅ | ✅ | ✅ |
| WebSocket | ⚠️ 弱 | ✅ 完善 | ✅ |
| 社区活跃度 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| 更新频率 | 快 | 中 | 快 |

**选择：ccxt (主力) + python-binance (WebSocket 行情补充)**

理由：
- ccxt 统一 API 让「后期扩展多交易所」成本趋近于零（换几个参数即可）
- python-binance 的 WebSocket 多路复用（`multiplex_socket`）更适合多交易对行情
- 两者互补：ccxt 做交易执行，python-binance 做行情采集

### 2.5 数据库

| 候选 | PostgreSQL + TimescaleDB | MySQL | SQLite | MongoDB |
|------|--------------------------|-------|--------|---------|
| 时序数据优化 | ✅ Hypertable + 压缩 | ❌ | ❌ | ⚠️ 时序集合 |
| ACID 事务 | ✅ | ✅ | ✅ | ❌ (最终一致) |
| 复杂查询 (JOIN/聚合) | ✅ | ✅ | ⚠️ 弱 | ❌ |
| 压缩率 (K线数据) | 10-20x | ❌ | ❌ | ⚠️ 3-5x |
| Docker 部署 | ✅ | ✅ | ❌ 不需 | ✅ |
| 运维复杂度 | 中 | 低 | 极低 | 中 |

**选择：PostgreSQL 16 + TimescaleDB 2.x**

理由：
- 交易记录需要 ACID（资金相关零容忍）
- K 线数据用 TimescaleDB 超表 + 压缩，存储成本降 90%+
- 连续聚合预计算 OHLCV，K 线图秒级加载
- PostgreSQL 生态成熟，备份/恢复/主从复制方案完善

### 2.6 缓存

| 候选 | Redis | Memcached | 应用内存 |
|------|-------|-----------|----------|
| 数据结构丰富 | ✅ (ZSET/Hash/List) | ❌ 仅 KV | ❌ |
| 持久化 | ✅ RDB + AOF | ❌ | ❌ |
| Pub/Sub | ✅ | ❌ | ❌ |
| 集群 | ✅ Redis Cluster | ⚠️ 客户端分片 | — |

**选择：Redis 7**

理由：行情缓存用 ZSET（按时间排序）、策略状态用 Hash、熔断状态用 String。后续多进程通信可用 Pub/Sub。

---

## 3. 前端技术选型

### 3.1 核心框架

| 候选 | React | Vue | Svelte |
|------|-------|-----|--------|
| 生态丰富度 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| TypeScript 支持 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| 图表库生态 | 极丰富 | 丰富 | 一般 |
| 招聘难度 | 易 | 易 | 难 |
| Bundle 大小 | 中 | 小 | 极小 |

**选择：React 18 + TypeScript**

理由：TradingView lightweight-charts、Recharts 等图表库 React 适配最完善。C 端主流，社区资源丰富。

### 3.2 图表方案

| 场景 | 选型 | 理由 |
|------|------|------|
| **K 线图** | lightweight-charts (TradingView) | 专业级，流畅，支持实时更新 |
| **收益曲线** | Recharts | React 原生，声明式，够用 |
| **饼图/柱状图** | Recharts | 资产分布、胜率统计 |

### 3.3 状态管理

| 候选 | Zustand | Redux Toolkit | Jotai |
|------|---------|--------------|-------|
| 学习成本 | 极低 | 高 | 低 |
| TypeScript | ✅ | ✅ | ✅ |
| 适合规模 | 中小 | 大 | 中小 |
| Bundle | < 1KB | ~11KB | ~2KB |

**选择：Zustand**

理由：FnAgent 前端状态不复杂（策略、持仓、行情），Zustand 足够且简洁。

---

## 4. 选型风险评估

| 技术 | 风险 | 等级 | 缓解 |
|------|------|------|------|
| LangGraph | API 仍在演进 | 低 | 锁定版本，不追 latest |
| ccxt | 币安 API 变更可能滞后 | 中 | 可降级使用 python-binance 原生接口 |
| TimescaleDB | 社区版功能限制 | 低 | 核心功能（超表+压缩+CA）社区版完全够用 |
| Ollama | 7B 模型分析质量有限 | 中 | 后期可切换云端 LLM（OpenAI/Claude） |
| lightweight-charts | Canvas 渲染，非 SVG | 低 | 性能好，但不支持 CSS 定制（可接受） |

---

## 5. 不推荐的技术（及原因）

| 技术 | 不推荐原因 |
|------|-----------|
| **TensorFlow / PyTorch 直接推理** | 太重，Ollama 封装已经够用 |
| **Kafka / RabbitMQ** | PoC 阶段无必要，Redis Pub/Sub 够用 |
| **Kubernetes** | PoC 阶段 Docker Compose 完全够，K8s 在 V2.0 再上 |
| **MongoDB** | 交易系统需要 ACID 事务，NoSQL 不适合 |
| **Web3.js / ethers.js** | FnAgent 做 CEX 交易，不涉及链上 |
| **Grafana + Prometheus** | PoC 阶段直接用日志，MVP 后期再上 |
| **TradingView Charting Library** | 收费且需审批，lightweight-charts 免费够用 |

---

## 6. 版本锁定建议

```txt
# requirements.txt (核心依赖)
langgraph>=0.2.0,<0.3
langchain>=0.3.0
langchain-ollama>=0.1.0
fastapi>=0.110.0,<1.0
uvicorn[standard]>=0.29.0
ccxt>=4.3.0
python-binance>=1.0.19
sqlalchemy[asyncio]>=2.0.25
asyncpg>=0.29.0
psycopg2-binary>=2.9  # TimescaleDB 需要
redis>=5.0.0
pydantic-settings>=2.1.0
alembic>=1.13.0

# 前端 (package.json)
"react": "^18.3.0"
"typescript": "^5.4.0"
"vite": "^5.2.0"
"tailwindcss": "^3.4.0"
"zustand": "^4.5.0"
"recharts": "^2.12.0"
"lightweight-charts": "^4.1.0"
"axios": "^1.6.0"
```
