"""FastAPI dependencies."""
from __future__ import annotations

from fastapi import Request

from backend.pipeline.task_manager import TaskManager
from backend.storage.db import Database


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_task_manager(request: Request) -> TaskManager:
    return request.app.state.task_manager
