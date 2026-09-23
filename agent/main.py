from contextlib import asynccontextmanager

from fastapi import FastAPI

from agent import __version__
from agent.api.routes import router
from agent.config import clear_settings_cache, get_settings
from agent.market.collector import get_collector
from agent.market.storage import close_market_db, init_market_db
from agent.runner import get_runner
from agent.storage.database import close_db, init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    clear_settings_cache()
    settings = get_settings()
    await init_db()
    await init_market_db()

    # 采集器与 AgentRunner 相互独立（ADR-010）：行情采集不依赖 LLM 是否就绪，
    # 也不因 Agent 停机而中断。全量历史回补在采集器内部异步跑，不阻塞启动。
    collector = get_collector()
    if settings.market_collector_autostart:
        await collector.start()

    yield

    if collector.running:
        await collector.stop()
    await get_runner().stop()
    await close_market_db()
    await close_db()


app = FastAPI(title="Bianca", version=__version__, lifespan=lifespan)
app.include_router(router)


@app.get("/")
async def root():
    return {
        "name": "Bianca",
        "version": __version__,
        "docs": "/docs",
    }
