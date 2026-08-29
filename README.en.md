# Bianca

English | **[简体中文](./README.md)**

Autonomous trading **Agent engine** — not a trading platform. LLM-driven 24×7 execution, parallel workers, auto-degrade to semi-auto for human intervention.

## Layout

- `frontend/` — React ops dashboard
- `backend/` — layered Python API (`interfaces` / `application` / `domain` / `infrastructure`)

## Quick Start

```bash
cp .env.example .env
docker compose up -d --build
curl -X POST http://127.0.0.1:8000/api/v1/agent/start
```

Markets: **crypto** (live) · **a_share** / **us_stock** (adapter hooks only).

## License

MIT
