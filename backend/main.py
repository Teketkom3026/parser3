"""FastAPI entrypoint."""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
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


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Логируем каждый HTTP-запрос: метод/путь/статус/длительность + IP и User-Agent.

    IP берём из X-Forwarded-For (за nginx реальный клиент там), иначе X-Real-IP,
    иначе прямой peer. /health пропускаем — его дёргает docker healthcheck каждые 30с.
    Пишет в stdout (docker logs), существующие логи не трогает.
    """
    if request.url.path.endswith("/health"):
        return await call_next(request)
    t0 = time.monotonic()
    response = await call_next(request)
    xff = request.headers.get("x-forwarded-for", "")
    client_ip = (
        xff.split(",")[0].strip()
        or request.headers.get("x-real-ip")
        or (request.client.host if request.client else "-")
    )
    log.info(
        "http_request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        ms=int((time.monotonic() - t0) * 1000),
        ip=client_ip,
        ua=request.headers.get("user-agent", "-")[:200],
    )
    return response


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
