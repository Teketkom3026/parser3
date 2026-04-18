"""FastAPI entrypoint."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api import routes_blacklist, routes_catalog, routes_tasks, ws
from backend.core.config import settings
from backend.core.logging import setup_logging, get_logger
from backend.pipeline.task_manager import TaskManager
from backend.storage.db import Database


setup_logging()
log = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = Database(settings.sqlite_db_path)
    await db.connect()
    await db.migrate()
    tm = TaskManager(db)
    await tm.start()
    app.state.db = db
    app.state.task_manager = tm
    log.info("startup_complete")
    try:
        yield
    finally:
        await tm.stop()
        await db.close()
        log.info("shutdown_complete")


app = FastAPI(
    title="parser3",
    version="1.0.0",
    root_path=settings.root_path,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# REST
app.include_router(routes_tasks.router, prefix="/api/v1")
app.include_router(routes_blacklist.router, prefix="/api/v1")
app.include_router(routes_catalog.router, prefix="/api/v1")
# WebSocket
app.include_router(ws.router)


@app.get("/health")
async def health():
    return {"status": "ok", "app": settings.app_name}


@app.get("/")
async def root():
    return {"name": "parser3", "docs": "/docs", "health": "/health"}
