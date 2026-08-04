# Bianca

English | **[简体中文](./README.md)**

A crypto auto-trading Agent platform for retail users. **PoC validated; MVP** delivers Web console, strategy templates, semi-auto confirm, full risk controls, summary APIs, paper gate, and Telegram/email notifications.

## Current Stage: MVP

| Item | Scope |
|------|-------|
| Exchange | Binance Demo spot (+ optional futures U/coin when `FUTURES_ENABLED=true`) |
| Decision | LLM autonomous (BUY/SELL/HOLD) + strategy templates |
| LLM | **DeepSeek API (default)**, switchable to Ollama via config |
| Orchestration | LangGraph Supervisor |
| Risk Control | 9 rules incl. stop-loss, drawdown, circuit breaker |
| Deployment | Docker (API + Web) + SQLite or PostgreSQL + Redis |
| UI | **Web console** (`web/`) + CLI / curl |

## Quick Start (PoC)

```bash
cp .env.example .env
docker compose up -d --build
python start_poc.py

curl http://127.0.0.1:8000/api/v1/health
curl http://127.0.0.1:8000/api/v1/summary/session/latest
```

**Switch to local Ollama:** set `LLM_PROVIDER=ollama` in `.env` and restart the API.

## Quick Start (MVP dual-stack)

```bash
docker compose --profile mvp -f docker-compose.yml -f docker-compose.mvp.yml up -d --build

curl http://127.0.0.1:8000/api/v1/summary/sessions/{session_id}/export.csv
curl http://127.0.0.1:8000/api/v1/market/klines?symbol=BTCUSDT
curl http://127.0.0.1:9090   # Prometheus
curl http://127.0.0.1:3001   # Grafana (admin/bianca)
```

## Web Console (M7)

```bash
docker compose up -d api && cd web && npm install && npm run dev
# http://127.0.0.1:3000

docker compose up -d --build
# Web http://127.0.0.1:3000 · API http://127.0.0.1:8000
```

Pages: dashboard (balance, PnL chart, agent control, confirm queue), trades, sessions, strategies, market klines, checkpoint replay, decisions, risk events, validation gate, settings, token usage.

Semi-auto (`EXECUTION_MODE=semi_auto`): BUY/SELL signals push via WebSocket to the confirm queue; user confirms before risk + execution; 30-minute TTL.

Live trading requires paper validation pass + `LIVE_TRADING_CONFIRMED=true` in `.env`.

## License

MIT
