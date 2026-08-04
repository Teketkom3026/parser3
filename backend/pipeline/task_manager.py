"""Task manager: worker pool + progress broadcast."""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from backend.core.config import settings
from backend.core.errors import human_error
from backend.core.logging import get_logger
from backend.deduper.deduper import dedup
from backend.exporter.excel import export_to_xlsx
from backend.fetcher.fetcher import BrowserPool, Fetcher
from backend.pipeline.site_processor import process_site
from backend.storage.db import Database


# G1-хвост: как часто писать прогресс задачи в БД (в сайтах). UI получает живые числа
# через WS на каждый сайт; в БД достаточно реже — меньше коммитов на общем соединении.
_PROGRESS_DB_EVERY = 20


def _error_to_contact(err: dict) -> dict:
    """Convert pipeline-level site error into a contact row with status='error'.

    EXPORTER-002: errors used to be a separate arg of generate_excel(); the
    exporter now consumes them uniformly via contacts[*].status == "error".
    """
    from urllib.parse import urlparse
    url = err.get("url") or ""
    try:
        domain = urlparse(url).netloc or url
    except Exception:
        domain = url
    code = err.get("error_code") or ""
    msg  = err.get("error_message") or ""
    comment = f"{code}: {msg}" if code or msg else "site error"
    return {
        "company_name": "",
        "domain": domain,
        "page_url": url,
        "status": "error",
        "norm_method": "empty",
        "comment": comment,
        "scan_date": "",
    }


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
        except Exception as e:
            # Не глушим молча: сбой сброса зависших задач на старте мог скрыть проблему с БД.
            log.warning("reset_stale_states_failed", error=str(e)[:200])
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
            # BUG-007: убрать пустой список, иначе task_id → [] копится в словаре.
            if not self._subscribers[task_id]:
                del self._subscribers[task_id]

    async def _broadcast(self, task_id: str, msg: Dict):
        for q in list(self._subscribers.get(task_id, [])):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                # G1-хвост: очередь медленного/застрявшего подписчика переполнена.
                # Раньше дропали НОВОЕ сообщение → UI застывал на старом числе. Теперь
                # выбрасываем самое старое и кладём свежее — прогресс сходится к актуальному.
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
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
            # Не тихо: клик «Продолжить» по задаче, чей предыдущий прогон ещё
            # доигрывает (например, длинный экспорт), раньше просто ничего не делал и
            # выглядел как сломанная кнопка. Теперь это видно в логах.
            log.warning("run_task_already_running", task_id=task_id)
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

            # Счётчики ПРОДОЛЖАЮТ уже сделанное, а не начинаются с нуля: в работу выше
            # берутся только 'pending'/'error', а завершённые ('ok') не перепарсиваются —
            # значит их надо учесть сразу. Без этого после «Продолжить» UI показывал
            # «40/9214» вместо «5620/9214» и пугал тем, что задача якобы стартовала заново.
            done_row = await self.db.fetchone(
                "SELECT COUNT(*) AS done, COALESCE(SUM(contacts_found), 0) AS contacts "
                "FROM sites WHERE task_id=? AND status='ok'",
                (task_id,),
            ) or {}
            done_before = int(done_row.get("done") or 0)
            processed = {"done": done_before, "ok": done_before, "err": 0,
                         "contacts": int(done_row.get("contacts") or 0)}

            # ETA: average wall-clock per completed site × remaining. Because `done`
            # accumulates across the concurrent workers, the throughput it implies
            # already accounts for the crawler concurrency.
            t_start = time.monotonic()

            def _eta_seconds():
                # Скорость — по сайтам ТЕКУЩЕГО прогона (elapsed относится только к нему,
                # иначе после resume ETA считался бы по чужому времени и врал в разы),
                # остаток — по общему прогрессу задачи.
                done_now = processed["done"] - done_before
                if done_now <= 0:
                    return None
                elapsed = time.monotonic() - t_start
                remaining = max(0, total_urls - processed["done"])
                return round(elapsed / done_now * remaining)

            async def _process_one(site):
                # Check cancel/pause
                task_row = await self.db.get_task(task_id)
                if not task_row or task_row["status"] in ("cancelled", "paused"):
                    return
                await self.db.update_site(site["id"], status="processing")
                await self._broadcast(task_id, {
                    "type": "progress", "task_id": task_id, "status": "running",
                    "stage": "fetching", "current_url": site["url"],
                    "site_current": site["url"],
                    "processed": processed["done"], "done": processed["done"],
                    "total": total_urls, "eta_seconds": _eta_seconds(),
                    "found_contacts": processed["contacts"],
                    "sites_ok": processed["ok"], "sites_error": processed["err"],
                })
                try:
                    # G1: жёсткий пер-сайт таймаут — wait_for отменяет process_site,
                    # если сайт завис (зависший сервер, редирект-петля, медленные
                    # страницы). Иначе один сайт держит слот воркера и вешает пачку.
                    result = await asyncio.wait_for(
                        process_site(
                            self._fetcher,
                            site["url"],
                            mode=task_mode,
                            target_positions=task_target_positions,
                        ),
                        timeout=settings.site_total_timeout_sec,
                    )
                except asyncio.TimeoutError:
                    log.warning("site_timeout", url=site["url"],
                                limit_sec=settings.site_total_timeout_sec)
                    result = {"status": "error", "error_code": "timeout",
                              "error_message": human_error(
                                  "timeout", detail=f"{settings.site_total_timeout_sec}с"),
                              "contacts": [], "pages_visited": 0}
                except Exception as e:
                    log.exception("worker_error", url=site["url"])
                    result = {"status": "error", "error_code": "exception",
                              "error_message": human_error("exception"),
                              "contacts": [], "pages_visited": 0}

                contacts = result.get("contacts") or []
                # Attach site_id and company info for storage
                for c in contacts:
                    c["site_id"] = site["id"]
                if contacts:
                    try:
                        # G1-хвост: не коммитим тут — контакты закоммитятся вместе с
                        # финальным update_site ниже (1 коммит/сайт, атомарно).
                        await self.db.save_contacts(task_id, contacts, commit=False)
                    except Exception as _save_err:
                        log.exception("save_contacts_failed", url=site["url"])
                        # Don't leave site stuck in "processing" — fall through to update_site
                        contacts = []
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
                # G1-хвост: прогресс в БД пишем не на КАЖДЫЙ сайт (это лишние коммиты на
                # общем соединении — WAL сериализует запись), а раз в _PROGRESS_DB_EVERY.
                # Точные итоги доводит финальный flush после цикла. Живые числа в UI идут
                # через WS-броадкаст ниже — он остаётся на каждый сайт.
                if processed["done"] % _PROGRESS_DB_EVERY == 0:
                    await self.db.update_task(
                        task_id,
                        processed_urls=processed["done"],
                        found_contacts=processed["contacts"],
                        errors_count=processed["err"],
                    )
                await self._broadcast(task_id, {
                    "type": "progress", "task_id": task_id, "status": "running",
                    "stage": "extracted", "current_url": site["url"],
                    "site_current": site["url"],
                    "processed": processed["done"], "done": processed["done"],
                    "total": total_urls, "eta_seconds": _eta_seconds(),
                    "found_contacts": processed["contacts"],
                    "sites_ok": processed["ok"], "sites_error": processed["err"],
                })

            # G1: пул из N постоянных воркеров тянет сайты из очереди. НЕ создаём
            # len(sites) корутин разом — на 10k это лишняя память и нагрузка на
            # планировщик. Конкуренция ограничена числом воркеров (без отдельного
            # семафора). При паузе/отмене _process_one выходит рано — очередь
            # быстро пустеет.
            queue: asyncio.Queue = asyncio.Queue()
            for s in sites:
                queue.put_nowait(s)

            async def _worker_loop():
                while True:
                    try:
                        site = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    try:
                        await _process_one(site)
                    except Exception:
                        log.exception("worker_loop_error", url=site.get("url"))
                    finally:
                        queue.task_done()

            n_workers = max(1, min(settings.crawler_max_concurrent, len(sites)))
            await asyncio.gather(*[_worker_loop() for _ in range(n_workers)],
                                 return_exceptions=True)

            # G1-хвост: финальный flush точных счётчиков (троттлинг мог пропустить хвост).
            await self.db.update_task(
                task_id,
                processed_urls=processed["done"],
                found_contacts=processed["contacts"],
                errors_count=processed["err"],
            )

            # Check if task was cancelled mid-way
            task_row = await self.db.get_task(task_id)
            if task_row and task_row["status"] == "cancelled":
                log.info("task_done", task_id=task_id, status="cancelled",
                         sites=total_urls, processed=processed["done"],
                         ok=processed["ok"], err=processed["err"],
                         contacts=processed["contacts"], sec=round(time.monotonic() - t_start))
                await self._broadcast(task_id, {"type": "cancelled", "task_id": task_id})
                return

            # ПАУЗА: воркеры вышли рано, но задача НЕ завершена — выходим, сохранив
            # status='paused'. Раньше проверялся только 'cancelled', и пауза проваливалась
            # сюда дальше: задача экспортировалась по неполным данным и получала
            # status='completed'. Кнопка «Продолжить» в UI показывается только при
            # 'paused' → она пропадала, и возобновить было нельзя (жалоба 02.08).
            if task_row and task_row["status"] == "paused":
                log.info("task_paused", task_id=task_id,
                         sites=total_urls, processed=processed["done"],
                         ok=processed["ok"], err=processed["err"],
                         contacts=processed["contacts"], sec=round(time.monotonic() - t_start))
                await self._broadcast(task_id, {
                    "type": "paused", "task_id": task_id,
                    "processed": processed["done"], "total": total_urls,
                    "found_contacts": processed["contacts"],
                })
                return

            # Generate Excel
            await self._broadcast(task_id, {"type": "progress", "task_id": task_id,
                                            "status": "running", "stage": "exporting",
                                            "processed": processed["done"], "total": total_urls,
                                            "found_contacts": processed["contacts"]})
            all_contacts = dedup(await self.db.list_contacts(task_id))
            errors = [
                {"url": s["url"], "error_code": s.get("error_code") or "",
                 "error_message": s.get("error_message") or ""}
                for s in await self.db.list_sites(task_id)
                if s.get("status") == "error"
            ]
            out_path = Path(settings.results_dir) / f"parser3_{task_id}.xlsx"
            task_meta = {
                "id": task_id,
                "mode": task_mode,
                "created_at": task_row.get("created_at") if task_row else "",
                "status": "completed",
                "total_urls": total_urls,
                "processed_urls": processed["done"],
                "errors_count": processed["err"],
            }
            contacts_with_errors = list(all_contacts) + [_error_to_contact(e) for e in errors]
            await asyncio.to_thread(export_to_xlsx, contacts_with_errors, str(out_path), task_meta)

            await self.db.update_task(
                task_id, status="completed",
                output_file=str(out_path),
                completed_at=datetime.utcnow().isoformat(),
            )
            log.info("task_done", task_id=task_id, status="completed",
                     sites=total_urls, processed=processed["done"],
                     ok=processed["ok"], err=processed["err"],
                     contacts=processed["contacts"], sec=round(time.monotonic() - t_start))
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
