from contextlib import asynccontextmanager
import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent import __version__
from agent.api.checkpoint_routes import router as checkpoint_router
from agent.api.metrics_routes import router as metrics_router
from agent.api.market_routes import router as market_router
from agent.api.pending_routes import router as pending_router
from agent.api.routes import router
from agent.api.secrets_routes import router as secrets_router
from agent.api.strategy_routes import router as strategy_router
from agent.api.summary_routes import router as summary_router
from agent.api.validation_routes import (
    futures_router,
    notify_router,
    router as validation_router,
    trading_router,
)
from agent.api.ws_routes import router as ws_router
from agent.config import clear_settings_cache
from agent.confirmation.service import expire_pending_signals
from agent.runner import get_runner
from agent.strategy.runner import get_strategy_runner
from agent.cache.redis_client import close_redis, init_redis
from agent.security.auth import ApiTokenMiddleware
from agent.security.secrets_loader import refresh_effective_settings
from agent.storage.database import close_db, init_db

logger = logging.getLogger(__name__)


async def _expire_pending_loop() -> None:
    while True:
        try:
            await expire_pending_signals()
        except Exception:  # noqa: BLE001
            logger.exception("expire pending signals failed")
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    clear_settings_cache()
    await init_db()
    await refresh_effective_settings()
    await init_redis()
    task = asyncio.create_task(_expire_pending_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await get_runner().stop()
    await get_strategy_runner().stop()
    await close_redis()
    await close_db()


app = FastAPI(title="Bianca", version=__version__, lifespan=lifespan)
app.add_middleware(ApiTokenMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:3000",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
app.include_router(metrics_router)
app.include_router(summary_router, prefix="/api/v1")
app.include_router(checkpoint_router, prefix="/api/v1")
app.include_router(pending_router, prefix="/api/v1")
app.include_router(strategy_router, prefix="/api/v1")
app.include_router(validation_router, prefix="/api/v1")
app.include_router(notify_router, prefix="/api/v1")
app.include_router(trading_router, prefix="/api/v1")
app.include_router(futures_router, prefix="/api/v1")
app.include_router(market_router, prefix="/api/v1")
app.include_router(secrets_router, prefix="/api/v1")
app.include_router(ws_router, prefix="/api/v1")


@app.get("/")
async def root():
    return {"name": "Bianca", "version": __version__, "docs": "/docs"}
