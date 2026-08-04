"""G1: пер-сайт таймаут + очередь воркеров не дают одной странице повесить пачку."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from backend.core.config import settings
from backend.pipeline.site_processor import process_site
from backend.pipeline.task_manager import TaskManager
from backend.storage.db import Database


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class _HangFetcher:
    async def fetch(self, url: str):
        await asyncio.sleep(60)  # «висит» дольше любого теста


def test_process_site_cancellable_on_timeout():
    """Зависший фетч прерывается wait_for — process_site отменяем (не глотает CancelledError)."""
    async def run():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                process_site(_HangFetcher(), "https://x.ru/", mode="fast_start"),
                timeout=0.2,
            )
    _run(run())


class _MixedFetcher:
    """Один хост висит дольше таймаута, остальные отдают валидный footer."""
    _OK = ("<html><body><footer>ООО «Ромашка» ИНН 7701234567 "
           "info@romashka.ru +7 (495) 123-45-67</footer></body></html>")

    def __init__(self, hang_host: str):
        self._hang = hang_host

    async def fetch(self, url: str):
        if self._hang in url:
            await asyncio.sleep(30)
        return self._OK


def test_run_task_hang_site_times_out_others_complete(tmp_path):
    """Один зависший сайт → timeout; остальные обрабатываются; задача завершается
    (а не висит вся пачка на одном слоте)."""
    old_timeout = settings.site_total_timeout_sec
    old_conc = settings.crawler_max_concurrent
    settings.site_total_timeout_sec = 1
    settings.crawler_max_concurrent = 3

    async def run():
        db = Database(str(tmp_path / "t.db"))
        await db.connect()
        try:
            tm = TaskManager(db)
            tm._fetcher = _MixedFetcher("hang.ru")
            urls = ["https://a.ru", "https://hang.ru", "https://b.ru"]
            task_id = await tm.create_task(urls, mode="fast_start")
            # Вся пачка должна уложиться задолго до 20с (hang капается на 1с).
            await asyncio.wait_for(tm.run_task(task_id), timeout=20)

            by = {s["url"]: s for s in await db.list_sites(task_id)}
            assert by["https://hang.ru"]["error_code"] == "timeout"
            assert by["https://a.ru"]["status"] in ("ok", "partial")
            assert by["https://b.ru"]["status"] in ("ok", "partial")
            task = await db.get_task(task_id)
            assert task["status"] == "completed"
        finally:
            await db.close()

    try:
        _run(run())
    finally:
        settings.site_total_timeout_sec = old_timeout
        settings.crawler_max_concurrent = old_conc


def test_contacts_persisted_with_batched_commit(tmp_path):
    """G1-хвост: save_contacts(commit=False) коммитится вместе с финальным update_site —
    контакты доходят до БД и счётчик found_contacts совпадает."""
    async def run():
        db = Database(str(tmp_path / "c.db"))
        await db.connect()
        try:
            tm = TaskManager(db)
            tm._fetcher = _MixedFetcher("never-hangs")
            task_id = await tm.create_task(["https://a.ru", "https://b.ru"], mode="fast_start")
            await asyncio.wait_for(tm.run_task(task_id), timeout=20)
            contacts = await db.list_contacts(task_id)
            assert len(contacts) >= 1, "контакты должны сохраниться (батч-коммит)"
            task = await db.get_task(task_id)
            assert task["found_contacts"] == len(contacts)
            assert task["processed_urls"] == 2
        finally:
            await db.close()
    _run(run())


def test_broadcast_coalesces_on_queue_full(tmp_path):
    """G1-хвост: при переполнении очереди подписчика остаются САМЫЕ СВЕЖИЕ сообщения
    (старые выбрасываются), а не наоборот — прогресс не застывает."""
    async def run():
        db = Database(str(tmp_path / "b.db"))
        await db.connect()
        try:
            tm = TaskManager(db)
            q: asyncio.Queue = asyncio.Queue(maxsize=2)
            tm._subscribers["t"] = [q]
            for i in range(5):
                await tm._broadcast("t", {"i": i})
            got = [q.get_nowait()["i"] for _ in range(q.qsize())]
            assert got == [3, 4], f"ожидали свежие [3,4], получили {got}"
        finally:
            await db.close()
    _run(run())


def test_run_task_processes_all_sites_via_queue(tmp_path):
    """Очередь воркеров обрабатывает ВСЕ сайты (число > числа воркеров)."""
    old_conc = settings.crawler_max_concurrent
    settings.crawler_max_concurrent = 3

    async def run():
        db = Database(str(tmp_path / "q.db"))
        await db.connect()
        try:
            tm = TaskManager(db)
            tm._fetcher = _MixedFetcher("never-hangs")
            urls = [f"https://site{i}.ru" for i in range(12)]
            task_id = await tm.create_task(urls, mode="fast_start")
            await asyncio.wait_for(tm.run_task(task_id), timeout=30)
            task = await db.get_task(task_id)
            assert task["status"] == "completed"
            assert task["processed_urls"] == 12   # все обработаны
        finally:
            await db.close()

    try:
        _run(run())
    finally:
        settings.crawler_max_concurrent = old_conc


class _SlowFetcher(_MixedFetcher):
    """Каждый сайт отдаётся с задержкой — успеваем нажать «паузу» посреди пачки."""
    def __init__(self, delay: float = 0.05):
        super().__init__("никогда")
        self._delay = delay

    async def fetch(self, url: str):
        await asyncio.sleep(self._delay)
        return self._OK


def test_pause_keeps_task_resumable(tmp_path):
    """Пауза НЕ должна завершать задачу.

    Баг (жалоба 02.08): после gather проверялся только 'cancelled', поэтому пауза
    проваливалась в экспорт и ставила status='completed'. Кнопка «Продолжить» в UI
    видна только при 'paused' → она исчезала и возобновить было нельзя.
    """
    async def run():
        db = Database(str(tmp_path / "p.db"))
        await db.connect()
        try:
            tm = TaskManager(db)
            tm._fetcher = _SlowFetcher()
            urls = [f"https://s{i}.ru" for i in range(12)]
            task_id = await tm.create_task(urls, mode="fast_start")

            runner = asyncio.create_task(tm.run_task(task_id))
            await asyncio.sleep(0.12)          # дать обработать пару сайтов
            await tm.pause(task_id)
            await asyncio.wait_for(runner, timeout=20)

            task = await db.get_task(task_id)
            assert task["status"] == "paused", (
                f"после паузы задача должна остаться 'paused', а не {task['status']!r}"
            )
            assert not task.get("output_file"), "на паузе экспорт не должен запускаться"

            # …и её можно доработать: остаток сайтов обрабатывается, задача завершается.
            await asyncio.wait_for(tm.run_task(task_id), timeout=30)
            task = await db.get_task(task_id)
            assert task["status"] == "completed"
            assert task["processed_urls"] == len(urls), (
                "после возобновления должны быть учтены ВСЕ сайты, включая сделанные "
                "до паузы (счётчик не начинается заново)"
            )
        finally:
            await db.close()
    _run(run())


def test_resume_counters_continue_not_restart(tmp_path):
    """Счётчики продолжают прогресс: уже готовые ('ok') сайты учтены сразу при старте."""
    async def run():
        db = Database(str(tmp_path / "r.db"))
        await db.connect()
        try:
            tm = TaskManager(db)
            tm._fetcher = _MixedFetcher("никогда")
            urls = ["https://a.ru", "https://b.ru", "https://c.ru"]
            task_id = await tm.create_task(urls, mode="fast_start")
            await asyncio.wait_for(tm.run_task(task_id), timeout=30)

            # Имитируем «остался один необработанный» и запускаем повторно.
            sites = await db.list_sites(task_id)
            await db.update_site(sites[0]["id"], status="pending")
            await db.commit()
            await asyncio.wait_for(tm.run_task(task_id), timeout=30)

            task = await db.get_task(task_id)
            assert task["processed_urls"] == len(urls), (
                f"ожидали {len(urls)} обработанных, получили {task['processed_urls']} "
                "(счётчик обнулился вместо продолжения)"
            )
        finally:
            await db.close()
    _run(run())
