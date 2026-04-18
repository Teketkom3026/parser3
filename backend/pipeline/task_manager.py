"""Task manager: worker pool + progress broadcast."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.exporter.excel import generate_excel
from backend.fetcher.fetcher import BrowserPool, Fetcher
from backend.pipeline.site_processor import process_site
from backend.storage.db import Database


log = get_logger("task_manager")


class TaskManager:
    def __init__(self, db: Database):
        self.db = db
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}
        self._workers_running: Dict[str, bool] = {}
        self._browser_pool: Optional[BrowserPool] = None
        self._fetcher: Optional[Fetcher] = None

    async def start(self):
        self._browser_pool = BrowserPool(size=settings.browser_pool_size)
        await self._browser_pool.start()
        self._fetcher = Fetcher(browser_pool=self._browser_pool)
        await self._fetcher.start()
        # Reset stale states
        try:
            await self.db.execute("UPDATE tasks SET status='paused' WHERE status='running'")
            await self.db.execute("UPDATE sites SET status='pending' WHERE status='processing'")
            await self.db.commit()
        except Exception:
            pass
        log.info("task_manager_started")

    async def stop(self):
        if self._fetcher:
            await self._fetcher.stop()
        if self._browser_pool:
            await self._browser_pool.stop()

    def subscribe(self, task_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.setdefault(task_id, []).append(q)
        return q

    def unsubscribe(self, task_id: str, q: asyncio.Queue):
        if task_id in self._subscribers:
            try:
                self._subscribers[task_id].remove(q)
            except ValueError:
                pass

    async def _broadcast(self, task_id: str, msg: Dict):
        for q in list(self._subscribers.get(task_id, [])):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    async def create_task(self, urls: List[str], mode: str = "all_contacts",
                          target_positions: List[str] | None = None,
                          input_file: str = "") -> str:
        task_id = uuid.uuid4().hex[:12]
        urls = [u.strip() for u in urls if u.strip()]
        await self.db.create_task(task_id, mode, total_urls=len(urls),
                                  input_file=input_file, target_positions=target_positions)
        for u in urls:
            await self.db.add_site(task_id, u)
        return task_id

    async def run_task(self, task_id: str):
        if self._workers_running.get(task_id):
            return
        self._workers_running[task_id] = True
        try:
            await self.db.update_task(task_id, status="running")
            sites = await self.db.fetchall(
                "SELECT id, url FROM sites WHERE task_id=? AND status IN ('pending','error')",
                (task_id,),
            )
            total = await self.db.fetchone(
                "SELECT total_urls FROM tasks WHERE id=?", (task_id,)
            )
            total_urls = total["total_urls"] if total else len(sites)

            # Load task settings (mode, target_positions) once — passed to every site
            task_cfg = await self.db.get_task(task_id) or {}
            task_mode = task_cfg.get("mode") or "all_contacts"
            task_target_positions = task_cfg.get("target_positions") or []
            if isinstance(task_target_positions, str):
                import json as _json
                try:
                    task_target_positions = _json.loads(task_target_positions) or []
                except Exception:
                    task_target_positions = []

            sem = asyncio.Semaphore(settings.crawler_max_concurrent)
            processed = {"done": 0, "ok": 0, "err": 0, "contacts": 0}

            async def worker(site):
                async with sem:
                    # Check cancel/pause
                    task_row = await self.db.get_task(task_id)
                    if not task_row or task_row["status"] in ("cancelled", "paused"):
                        return
                    await self.db.update_site(site["id"], status="processing")
                    await self._broadcast(task_id, {
                        "type": "progress", "task_id": task_id, "status": "running",
                        "stage": "fetching", "current_url": site["url"],
                        "processed": processed["done"], "total": total_urls,
                        "found_contacts": processed["contacts"],
                        "sites_ok": processed["ok"], "sites_error": processed["err"],
                    })
                    try:
                        result = await process_site(
                            self._fetcher,
                            site["url"],
                            mode=task_mode,
                            target_positions=task_target_positions,
                        )
                    except Exception as e:
                        log.exception("worker_error", url=site["url"])
                        result = {"status": "error", "error_code": "exception",
                                  "error_message": str(e)[:200], "contacts": [],
                                  "pages_visited": 0}

                    contacts = result.get("contacts") or []
                    # Attach site_id and company info for storage
                    for c in contacts:
                        c["site_id"] = site["id"]
                    if contacts:
                        await self.db.save_contacts(task_id, contacts)
                    await self.db.update_site(
                        site["id"],
                        status=result.get("status") or "error",
                        error_code=result.get("error_code") or None,
                        error_message=result.get("error_message") or None,
                        pages_visited=result.get("pages_visited", 0),
                        contacts_found=len(contacts),
                        processing_time_ms=result.get("processing_time_ms", 0),
                    )
                    processed["done"] += 1
                    processed["contacts"] += len(contacts)
                    if result.get("status") == "ok":
                        processed["ok"] += 1
                    else:
                        processed["err"] += 1
                    await self.db.update_task(
                        task_id,
                        processed_urls=processed["done"],
                        found_contacts=processed["contacts"],
                        errors_count=processed["err"],
                    )
                    await self._broadcast(task_id, {
                        "type": "progress", "task_id": task_id, "status": "running",
                        "stage": "extracted", "current_url": site["url"],
                        "processed": processed["done"], "total": total_urls,
                        "found_contacts": processed["contacts"],
                        "sites_ok": processed["ok"], "sites_error": processed["err"],
                    })

            await asyncio.gather(*[worker(s) for s in sites])

            # Check if task was cancelled mid-way
            task_row = await self.db.get_task(task_id)
            if task_row and task_row["status"] == "cancelled":
                await self._broadcast(task_id, {"type": "cancelled", "task_id": task_id})
                return

            # Generate Excel
            await self._broadcast(task_id, {"type": "progress", "task_id": task_id,
                                            "status": "running", "stage": "exporting",
                                            "processed": processed["done"], "total": total_urls,
                                            "found_contacts": processed["contacts"]})
            all_contacts = await self.db.list_contacts(task_id)
            errors = [
                {"url": s["url"], "error_code": s.get("error_code") or "",
                 "error_message": s.get("error_message") or ""}
                for s in await self.db.list_sites(task_id)
                if s.get("status") == "error"
            ]
            out_path = Path(settings.results_dir) / f"parser3_{task_id}.xlsx"
            task_meta = {
                "task_id": task_id,
                "created_at": task_row.get("created_at") if task_row else "",
                "status": "completed",
                "total_urls": total_urls,
                "processed_urls": processed["done"],
            }
            await asyncio.to_thread(generate_excel, all_contacts, str(out_path), task_meta, errors)

            await self.db.update_task(
                task_id, status="completed",
                output_file=str(out_path),
                completed_at=datetime.utcnow().isoformat(),
            )
            await self._broadcast(task_id, {"type": "completed", "task_id": task_id,
                                            "output_file": str(out_path),
                                            "found_contacts": processed["contacts"]})
        except Exception as e:
            log.exception("run_task_failed", task_id=task_id)
            await self.db.update_task(task_id, status="failed")
            await self._broadcast(task_id, {"type": "failed", "task_id": task_id, "error": str(e)[:200]})
        finally:
            self._workers_running.pop(task_id, None)

    async def cancel(self, task_id: str):
        await self.db.update_task(task_id, status="cancelled")

    async def pause(self, task_id: str):
        await self.db.update_task(task_id, status="paused")

    async def resume(self, task_id: str):
        await self.db.update_task(task_id, status="pending")
        asyncio.create_task(self.run_task(task_id))
