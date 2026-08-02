from contextlib import asynccontextmanager

from fastapi import FastAPI

from agent import __version__
from agent.api.routes import router
from agent.api.summary_routes import router as summary_router
from agent.config import clear_settings_cache
from agent.runner import get_runner
from agent.storage.database import close_db, init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    clear_settings_cache()
    await init_db()
    yield
    await get_runner().stop()
    await close_db()


app = FastAPI(title="Bianca", version=__version__, lifespan=lifespan)
app.include_router(router)
app.include_router(summary_router, prefix="/api/v1")


@app.get("/")
async def root():
    return {"name": "Bianca", "version": __version__, "docs": "/docs"}
