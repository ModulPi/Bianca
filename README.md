# Bianca

**[English](./README.en.md)** | 简体中文

**自主交易 Agent 引擎** — 非交易平台。聚焦 LLM 自主决策、24×7 稳定运行、多 symbol 并行执行；异常时自动降级 semi_auto 供人工介入。

## 定位

| 做 | 不做 |
|----|------|
| Agent 24×7 自主买卖 | 交易终端 / K 线看盘 |
| 多 Worker 并行（`AGENT_SYMBOLS`） | 策略商城 / 模板 UI |
| 失败自动降级 + 人工确认 | C 端用户运营向功能 |
| 决策审计（回放 / 会话） | 以人工下单为主的产品 |

**市场：** 加密（已实现）· A 股 / 美股（适配层钩子，未实现）

## 快速开始

```bash
cp .env.example .env
# BINANCE_* · LLM_* · AGENT_SYMBOLS=BTCUSDT,ETHUSDT

docker compose up -d --build
curl http://127.0.0.1:8000/api/v1/health
curl -X POST http://127.0.0.1:8000/api/v1/agent/start
curl http://127.0.0.1:8000/api/v1/agent/status
# 自动降级后人工恢复：
# curl -X POST http://127.0.0.1:8000/api/v1/agent/recover
```

## 运维 Web（极简）

```bash
docker compose up -d api && cd web && npm install && npm run dev
# http://127.0.0.1:3000 — Agent 启停 · Worker 状态 · 降级确认队列 · 审计
```

## 核心配置

| 变量 | 说明 |
|------|------|
| `AGENT_SYMBOLS` | 并行 Worker 交易对，逗号分隔 |
| `TRADE_MARKET` | `crypto`（默认）/ `a_share` / `us_stock` |
| `EXECUTION_MODE` | `auto` / `semi_auto` / `signal_only` |
| `AUTO_DEGRADE_ENABLED` | 连续失败自动切 semi_auto |
| `AGENT_STOP_ON_LOOP_CLOSED` | 默认 `false`（24×7 不因 PoC 闭环停止） |

## 许可证

MIT
