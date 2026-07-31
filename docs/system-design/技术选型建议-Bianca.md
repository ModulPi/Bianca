# Bianca — 技术选型建议

> 版本：v0.4 | 日期：2026-07-31

---

## 1. PoC 技术栈

```
┌─────────────────────────────────────────────────┐
│                  Bianca PoC                      │
├──────────────┬──────────────┬───────────────────┤
│ Agent 编排    │ 交易所        │ 基础设施           │
│ LangGraph    │ ccxt (Demo)  │ SQLite            │
│ LangChain    │ python-binance│ 内存行情缓存       │
│              │ (WS 行情)    │ Docker (仅 API)   │
├──────────────┼──────────────┼───────────────────┤
│ LLM (默认)   │ LLM (可切换)  │ 配置              │
│ DeepSeek API │ Ollama 本地   │ pydantic-settings │
│ OpenAI 兼容  │ OpenAI 兼容  │ LLM_PROVIDER      │
└──────────────┴──────────────┴───────────────────┘
```

## 2. LLM 提供商选型

| 提供商 | 阶段 | 接入方式 | 切换配置 |
|--------|------|----------|----------|
| **DeepSeek** | PoC 默认 | HTTPS OpenAI 兼容 API | `LLM_PROVIDER=deepseek` |
| **Ollama** | PoC 后期 / MVP | 宿主机 HTTP OpenAI 兼容 | `LLM_PROVIDER=ollama` |

**统一封装：** `llm/provider.py` 根据 `LLM_PROVIDER` 构造客户端，Analysis Agent 无感知切换。

```bash
# DeepSeek（默认）
LLM_PROVIDER=deepseek
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat

# 切换 Ollama
LLM_PROVIDER=ollama
LLM_BASE_URL=http://host.docker.internal:11434
LLM_MODEL=qwen2.5:7b
```

## 3. MVP 增量技术栈

| 层 | 新增 |
|----|------|
| 前端 | React 18 + TypeScript + Vite + Tailwind |
| 数据库 | PostgreSQL 16 + TimescaleDB 2.x |
| 缓存 | Redis 7 |
| 通知 | Telegram Bot API |

## 4. 关键选型理由（PoC）

### LangGraph
- Supervisor 模式编排 LLM → 风控 → 执行
- SqliteSaver 满足决策审计

### DeepSeek API（PoC 默认）
- 开箱即用，无需本地 GPU
- OpenAI 兼容，切换 Ollama 时代码复用

### ccxt (Demo 现货)
- 统一 API，MVP 扩展合约时复用

### SQLite
- 零配置，PoC 单用户足够

## 5. 版本要求

| 组件 | PoC 版本 |
|------|----------|
| Python | ≥ 3.11 |
| LangGraph | ≥ 0.2 |
| openai (SDK) | ≥ 1.x（DeepSeek / Ollama 兼容调用） |
| FastAPI | ≥ 0.110 |
| ccxt | ≥ 4.x |

## 6. 明确不选（PoC）

| 组件 | 原因 |
|------|------|
| 强制 Ollama | PoC 先用 DeepSeek 快速验证；Ollama 通过配置切换 |
| PostgreSQL/TimescaleDB | 数据量小，SQLite 足够 |
| React 前端 | PoC 用 CLI/curl |
