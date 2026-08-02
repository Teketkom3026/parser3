#!/usr/bin/env python3
"""Пересобрать XLSX уже посчитанной задачи из БД — без повторного парсинга.

Зачем
=====
Экспорт — последний шаг задачи, и его падение обесценивает ВСЮ работу: 02.08 задача
`7b3f9c28c78b` (56 247 сайтов, ~103k контактов) упала на `IllegalCharacterError`
(управляющий символ в должности с сайта) → status=failed, `output_file` пуст, кнопки
скачивания отдают 404, хотя все контакты лежат в БД.

Скрипт берёт контакты и ошибки сайтов из БД и гоняет ТОТ ЖЕ код экспорта, что и
пайплайн (`dedup` → `export_to_xlsx`), поэтому файл получается идентичным штатному.
По умолчанию ещё и проставляет задаче `output_file`/`status=completed`, чтобы в UI
заработали кнопки «XLSX»/«CSV» (можно отключить: --no-db-update).

Запуск (внутри контейнера — там же БД и настройки):
    docker exec parser3-dev-backend python tools/reexport_task.py 7b3f9c28c78b

Проверить, ничего не меняя в БД:
    docker exec parser3-dev-backend python tools/reexport_task.py 7b3f9c28c78b --no-db-update
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core.config import settings
from backend.deduper.deduper import dedup
from backend.exporter.excel import export_to_xlsx
from backend.pipeline.task_manager import _error_to_contact
from backend.storage.db import Database


async def reexport(task_id: str, update_db: bool = True) -> str:
    db = Database(settings.sqlite_db_path)
    await db.connect()
    try:
        task = await db.get_task(task_id)
        if not task:
            raise SystemExit(f"задача {task_id} не найдена")

        raw = await db.list_contacts(task_id)
        print(f"контактов в БД: {len(raw)}")
        contacts = dedup(raw)
        print(f"после дедупа:   {len(contacts)}")

        sites = await db.list_sites(task_id)
        errors = [
            {"url": s["url"], "error_code": s.get("error_code") or "",
             "error_message": s.get("error_message") or ""}
            for s in sites if s.get("status") == "error"
        ]
        print(f"сайтов-ошибок:  {len(errors)}")

        out_path = Path(settings.results_dir) / f"parser3_{task_id}.xlsx"
        task_meta = {
            "id": task_id,
            "mode": task.get("mode") or "all_contacts",
            "created_at": task.get("created_at") or "",
            "status": "completed",
            "total_urls": task.get("total_urls") or len(sites),
            "processed_urls": task.get("processed_urls") or 0,
            "errors_count": len(errors),
        }
        rows = list(contacts) + [_error_to_contact(e) for e in errors]
        print(f"строк в файл:   {len(rows)} → {out_path}")

        # Экспорт синхронный и тяжёлый — как в пайплайне, уводим в тред.
        await asyncio.to_thread(export_to_xlsx, rows, str(out_path), task_meta)
        size_mb = out_path.stat().st_size / 1024 / 1024
        print(f"ГОТОВО: {out_path} ({size_mb:.1f} МБ)")

        if update_db:
            await db.update_task(
                task_id, status="completed", output_file=str(out_path),
                completed_at=(task.get("completed_at") or datetime.utcnow().isoformat()),
            )
            await db.commit()
            print("БД обновлена: status=completed, output_file проставлен "
                  "→ в UI заработают кнопки XLSX/CSV")
        return str(out_path)
    finally:
        await db.close()


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        raise SystemExit(1)
    asyncio.run(reexport(args[0], update_db="--no-db-update" not in sys.argv))


if __name__ == "__main__":
    main()
