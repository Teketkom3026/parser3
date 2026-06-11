"""Task endpoints."""
from __future__ import annotations

import asyncio
import io
import json
import re
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from backend.api.deps import get_db, get_task_manager
from backend.core.config import settings
from backend.core.logging import get_logger
from backend.pipeline.task_manager import TaskManager
from backend.storage.db import Database


log = get_logger("api.tasks")
router = APIRouter(prefix="/tasks", tags=["tasks"])


def _uploads_dir() -> Path:
    return Path(settings.data_dir) / "uploads"


def _save_upload(task_id: str, filename: Optional[str], data: bytes) -> Optional[str]:
    """Сохранить СЫРОЙ загруженный файл клиента в DATA_DIR/uploads/<task_id>__<имя>.

    Нужно, чтобы прогон можно было повторить 1:1 (с дублями/порядком исходного файла) —
    в `sites` хранятся только уникальные нормализованные URL, оригинал из них не восстановить.
    Best-effort: ошибка записи НЕ валит создание задачи. Возвращает путь или None.
    """
    try:
        up = _uploads_dir()
        up.mkdir(parents=True, exist_ok=True)
        # имя без путей + только безопасные символы
        safe = re.sub(r"[^\w.\-]", "_", Path(filename or "input.txt").name)[:120] or "input.txt"
        path = up / f"{task_id}__{safe}"
        path.write_bytes(data)
        return str(path)
    except Exception as e:  # noqa: BLE001
        log.warning("save_upload_failed", task_id=task_id, error=str(e)[:200])
        return None


def _find_upload(task_id: str) -> Optional[Path]:
    """Найти сохранённый оригинал по task_id (uploads/<task_id>__*)."""
    tid = re.sub(r"[^\w]", "", task_id)  # task_id из URL → только безопасные символы
    if not tid:
        return None
    up = _uploads_dir()
    if not up.is_dir():
        return None
    matches = sorted(up.glob(f"{tid}__*"))
    return matches[0] if matches else None


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
    # Сохраняем сырой загруженный файл (для повторного прогона 1:1). Best-effort.
    _save_upload(task_id, file.filename, data)
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


@router.get("/{task_id}/input")
async def download_task_input(task_id: str):
    """Скачать СЫРОЙ загруженный файл задачи (как клиент загрузил, с дублями/порядком)."""
    path = _find_upload(task_id)
    if not path:
        raise HTTPException(status_code=404, detail="original upload not saved for this task")
    return FileResponse(str(path), filename=path.name, media_type="application/octet-stream")


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


@router.get("/{task_id}/download/csv")
async def download_task_csv(task_id: str, db: Database = Depends(get_db)):
    t = await db.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="task not found")
    result_path = t.get("output_file") or t.get("result_path")
    if not result_path or not Path(result_path).exists():
        raise HTTPException(status_code=404, detail="result not ready")
    try:
        import openpyxl
        wb = openpyxl.load_workbook(result_path, read_only=True, data_only=True)
        ws = wb["Все контакты"]
        buf = io.StringIO()
        import csv as csv_mod
        writer = csv_mod.writer(buf, delimiter=";")
        for row in ws.iter_rows(values_only=True):
            writer.writerow(["" if v is None else str(v) for v in row])
        wb.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"csv export failed: {e}")
    # Filename includes the task date+time so repeated downloads are distinguishable,
    # e.g. parser3_0c9f047bbc1f_2026-06-02_14-03-11.csv (completion → creation → now).
    # Time uses '-' separators because ':' is not allowed in filenames.
    from datetime import datetime, timezone
    raw_ts = (t.get("completed_at") or t.get("created_at") or "").strip().replace("T", " ")
    dt = None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw_ts[:26], fmt)
            break
        except ValueError:
            continue
    if dt is None:
        dt = datetime.now()
    else:
        # Stored timestamps are UTC (datetime.utcnow / SQLite CURRENT_TIMESTAMP) →
        # render in server-local time so the filename matches the wall clock.
        dt = dt.replace(tzinfo=timezone.utc).astimezone()
    stamp = dt.strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"parser3_{task_id}_{stamp}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
