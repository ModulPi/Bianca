# Bianca

English | **[简体中文](./README.md)**

A crypto auto-trading Agent platform for retail users. **PoC focus:** LLM autonomous decisions + Binance Demo spot + LangGraph orchestration.

## Current Stage: PoC

On Binance Demo spot, the LLM autonomously analyzes market data, produces buy/sell signals, passes minimal risk checks, and completes at least **one buy + one sell** cycle.

| Item | PoC Scope |
|------|-----------|
| Exchange | Binance Demo spot |
| Decision | LLM autonomous (BUY/SELL/HOLD) |
| LLM | **DeepSeek API (default)**, switchable to Ollama via config |
| Orchestration | LangGraph Supervisor |
| Risk Control | Max trade amount + daily loss limit |
| Deployment | Docker (API) + SQLite |
| UI | **Web console** (`web/`) + CLI / curl |

## Tech Stack

| Layer | PoC | MVP (Later) |
|-------|-----|-------------|
| Agent | LangGraph + DeepSeek/Ollama | + strategy templates |
| Backend | FastAPI + Python | Same |
| Exchange | ccxt (Demo spot) | + futures |
| Database | SQLite | PostgreSQL + TimescaleDB |
| Frontend | React + TypeScript (M7 `web/`) | Same + semi-auto confirm |

## Quick Start (PoC)

```bash
cp .env.example .env
# Set BINANCE_API_KEY/SECRET, LLM_API_KEY, BINANCE_PROXY if needed

docker compose up -d --build
python start_poc.py

# Or step by step:
curl http://127.0.0.1:8000/api/v1/health
curl -X POST http://127.0.0.1:8000/api/v1/agent/start
python run_poc_closure.py

curl http://127.0.0.1:8000/api/v1/summary/session/latest
```

**Switch to local Ollama:** set `LLM_PROVIDER=ollama` in `.env` and restart the API.

## Web Console (M7)

```bash
# Dev: hot reload
docker compose up -d api && cd web && npm install && npm run dev

# Prod: nginx + API via Docker Compose
docker compose up -d --build
# Web http://127.0.0.1:3000
```

Pages: dashboard (balance, PnL chart, agent control), trades, sessions, checkpoint replay, decisions, risk events, token usage.

Semi-auto confirm requires M6 WebSocket (placeholder banner on dashboard).

## License

MIT
