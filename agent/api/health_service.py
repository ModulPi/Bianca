from __future__ import annotations

from agent.api.schemas import HealthResponse
from agent.cache.redis_client import redis_health
from agent.config import get_settings
from agent.exchange.spot_demo import check_binance_demo, check_binance_live
from agent.llm.analyzer import check_llm, check_ollama
from agent.storage.database import get_engine, schema_mode


async def build_health_response() -> HealthResponse:
    settings = get_settings()

    db_status = "ok"
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.exec_driver_sql("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        db_status = f"error: {exc}"

    binance = await check_binance_demo()
    binance_live = await check_binance_live()
    llm = await check_llm(settings)
    ollama = await check_ollama(settings)
    redis = await redis_health()

    overall = "ok"
    if db_status != "ok" or binance["status"] == "error" or llm["status"] == "error":
        overall = "degraded"
    if redis.get("status") == "error":
        overall = "degraded"

    llm_status = llm["status"]
    llm_detail = llm.get("detail")
    if llm_status == "not_configured":
        llm_detail = llm_detail or "Set LLM_API_KEY in .env to enable P2"

    return HealthResponse(
        status=overall,
        database=db_status,
        database_backend=settings.database_backend,
        schema_mode=schema_mode(),
        redis=redis.get("status", "not_configured"),
        redis_detail=redis.get("detail"),
        api_auth_enabled=settings.api_auth_enabled,
        encryption_configured=settings.encryption_configured,
        runtime_secrets_loaded=settings.binance_configured or settings.llm_configured,
        metrics_enabled=settings.metrics_enabled,
        ollama=ollama.get("status"),
        ollama_detail=ollama.get("detail"),
        binance_demo=binance["status"],
        binance_demo_detail=binance.get("detail"),
        binance_live=binance_live.get("status"),
        binance_live_detail=binance_live.get("detail"),
        binance_detail=binance.get("detail"),
        llm_provider=settings.llm_provider,
        llm=llm_status,
        llm_detail=llm_detail,
    )
