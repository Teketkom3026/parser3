"""Task endpoints."""
from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from backend.api.deps import get_db, get_task_manager
from backend.core.config import settings
from backend.core.logging import get_logger
from backend.pipeline.task_manager import TaskManager
from backend.storage.db import Database


log = get_logger("api.tasks")
router = APIRouter(prefix="/tasks", tags=["tasks"])


class CreateTaskRequest(BaseModel):
    urls: List[str]
    mode: str = "all_contacts"
    target_positions: Optional[List[str]] = None


@router.post("", status_code=201)
async def create_task(
    payload: CreateTaskRequest,
    background: BackgroundTasks,
    tm: TaskManager = Depends(get_task_manager),
):
    if not payload.urls:
        raise HTTPException(status_code=400, detail="urls is required")
    task_id = await tm.create_task(
        urls=payload.urls,
        mode=payload.mode,
        target_positions=payload.target_positions,
    )
    background.add_task(tm.run_task, task_id)
    return {"task_id": task_id}


@router.post("/upload", status_code=201)
async def create_task_from_upload(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    mode: str = Form("all_contacts"),
    target_positions: Optional[str] = Form(None),
    tm: TaskManager = Depends(get_task_manager),
):
    data = await file.read()
    try:
        text = data.decode("utf-8", errors="ignore")
    except Exception:
        raise HTTPException(status_code=400, detail="cannot decode file")
    urls: List[str] = []
    for line in text.splitlines():
        line = line.strip().lstrip("\ufeff")
        # support CSV with optional header
        if "," in line:
            parts = [p.strip() for p in line.split(",")]
            # take first non-empty part that looks like URL/domain
            for p in parts:
                if p and ("." in p) and p.lower() not in ("url", "сайт", "site", "website", "домен"):
                    urls.append(p)
                    break
            continue
        if not line or line.startswith("#"):
            continue
        if line.lower() in ("url", "сайт", "site", "website"):
            continue
        urls.append(line)
    # normalize
    urls = [u if u.startswith("http") else f"https://{u}" for u in urls if u]
    if not urls:
        raise HTTPException(status_code=400, detail="no URLs found in file")
    tp = None
    if target_positions:
        try:
            tp = json.loads(target_positions)
        except Exception:
            tp = [s.strip() for s in target_positions.split(",") if s.strip()]
    task_id = await tm.create_task(
        urls=urls, mode=mode, target_positions=tp, input_file=file.filename or "",
    )
    background.add_task(tm.run_task, task_id)
    return {"task_id": task_id, "urls_count": len(urls)}


@router.get("")
async def list_tasks(db: Database = Depends(get_db)):
    rows = await db.list_tasks()
    return {"tasks": rows}


@router.get("/{task_id}")
async def get_task(task_id: str, db: Database = Depends(get_db)):
    t = await db.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="task not found")
    sites = await db.list_sites(task_id)
    return {"task": t, "sites": sites}


@router.get("/{task_id}/contacts")
async def get_task_contacts(task_id: str, db: Database = Depends(get_db)):
    t = await db.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="task not found")
    contacts = await db.list_contacts(task_id)
    return {"contacts": contacts}


@router.post("/{task_id}/pause")
async def pause_task(task_id: str, tm: TaskManager = Depends(get_task_manager)):
    await tm.pause(task_id)
    return {"status": "paused"}


@router.post("/{task_id}/resume")
async def resume_task(task_id: str, background: BackgroundTasks,
                      tm: TaskManager = Depends(get_task_manager)):
    await tm.resume(task_id)
    background.add_task(tm.run_task, task_id)
    return {"status": "resumed"}


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str, tm: TaskManager = Depends(get_task_manager)):
    await tm.cancel(task_id)
    return {"status": "cancelled"}


@router.delete("/{task_id}")
async def delete_task(task_id: str, db: Database = Depends(get_db)):
    await db.delete_task(task_id)
    return {"status": "deleted"}


@router.get("/{task_id}/download")
async def download_task(task_id: str, db: Database = Depends(get_db)):
    t = await db.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="task not found")
    result_path = t.get("output_file") or t.get("result_path")
    if not result_path or not Path(result_path).exists():
        raise HTTPException(status_code=404, detail="result not ready")
    filename = f"parser3_{task_id}.xlsx"
    return FileResponse(
        result_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
    )
