#!/usr/bin/env python3
"""Сгенерировать список URL для нагрузочного / анти-зависание теста парсера.

Зачем
=====
Проверить фикс возврата слота BrowserPool (fetcher.py): даже при массе таймаутов
(отмена пер-сайт `asyncio.wait_for` в task_manager) пул контекстов не должен
исчерпываться, а задача — доходить до конца, а не виснуть. До фикса после
`browser_pool_size` отменённых браузерных фетчей все воркеры зависали на
`_contexts.get()` — это и есть репорт «парсер зависает на малых объёмах».

Состав файла (доли настраиваются)
=================================
  * bait  — IP из TEST-NET (RFC5737: 192.0.2/198.51.100/203.0.113). Не
            маршрутизируются → висят до connect/навигационного таймаута, гоняя путь
            timeout → cancel → ВОЗВРАТ слота (ровно баг, который чинили).
  * dead  — несуществующие домены (.invalid) → быстрый NXDOMAIN (путь DNS fail-fast).
  * real  — реальные домены из seed-файла (--seed) или маленького встроенного списка,
            повторяются по кругу → нормальный фетч + браузер.

Вывод — текстовый файл, ПО ОДНОМУ URL В СТРОКЕ. Это формат, который ест загрузчик
(`/api/v1/tasks` разбирает upload как utf-8 построчно; xlsx НЕ подходит — он бинарный).

Использование
=============
  # быстрый анти-зависание тест (bait-heavy, немного сайтов):
  python tools/gen_load_urls.py 400 anti_hang.txt --bait 0.6 --dead 0.1

  # масштабный прогон на 10k (подмешать СВОЙ реальный список):
  python tools/gen_load_urls.py 10000 load_10k.txt --seed my_real_urls.txt

Как гонять на DEV
=================
  1) Скопировать файл на сервер, загрузить через UI (или API):
       curl -F "file=@anti_hang.txt" http://localhost:8769/api/v1/tasks/upload
  2) Смотреть логи: `make logs | grep -E "site_timeout|fetch_done|task_done"`.
     Признак ИСПРАВНОСТИ: задача доходит до `task_done` (completed), прогресс не
     застывает; счётчик site_timeout растёт, но воркеры продолжают брать сайты.
     Признак БАГА (если бы фикс не помог): прогресс замирает после ~browser_pool_size
     таймаутов, task_done не наступает.
  3) Здоровье пула под нагрузкой ориентировочно: время до completed ≈
     (число сайтов / concurrency) × среднее время, без «полки» (зависания).
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

# Небольшой встроенный seed реальных доменов (если не передан --seed). Намеренно
# короткий — при большом N реальная доля будет бить по ним многократно, для честного
# масштабного теста используйте свой список через --seed.
_REAL_SEED = [
    "https://www.rikor-electronics.ru/kontakty",
    "https://sosnovgeo.ru",
    "https://bautex.ru/sotrudniki/",
    "https://cigapan.ru/contacts",
    "https://chechencement.com/our-teams/",
    "https://bra-zis.ru/contact/",
]

# TEST-NET блоки (RFC5737) — не маршрутизируются, дают connect/навигационный таймаут.
_BAIT_NETS = ["192.0.2", "198.51.100", "203.0.113"]


def _bait_url(i: int) -> str:
    net = _BAIT_NETS[i % len(_BAIT_NETS)]
    host = 1 + (i // len(_BAIT_NETS)) % 254
    # http:// (не https) — чтобы упереться в connect, а не в TLS
    return f"http://{net}.{host}/contacts"


def _dead_url(i: int) -> str:
    return f"https://nonexistent-{i}-{random.randint(1000, 9999)}.invalid/"


def _parse_args(argv: list[str]):
    if len(argv) < 2:
        print(__doc__)
        sys.exit(1)
    n = int(argv[1])
    out = argv[2] if len(argv) > 2 and not argv[2].startswith("--") else f"load_{n}.txt"
    bait = dead = None
    seed_file = None
    rest = argv[2:]
    it = iter(rest)
    for a in it:
        if a == "--bait":
            bait = float(next(it))
        elif a == "--dead":
            dead = float(next(it))
        elif a == "--seed":
            seed_file = next(it)
    return n, out, (bait if bait is not None else 0.3), (dead if dead is not None else 0.1), seed_file


def main() -> None:
    n, out, bait_frac, dead_frac, seed_file = _parse_args(sys.argv)
    if bait_frac + dead_frac > 1.0:
        print("ОШИБКА: --bait + --dead > 1.0"); sys.exit(1)

    real = _REAL_SEED
    if seed_file:
        lines = [l.strip() for l in Path(seed_file).read_text(encoding="utf-8", errors="ignore").splitlines()]
        real = [(l if l.startswith("http") else f"https://{l}") for l in lines if l and not l.startswith("#")]
        if not real:
            print(f"ОШИБКА: в {seed_file} нет URL"); sys.exit(1)

    n_bait = int(n * bait_frac)
    n_dead = int(n * dead_frac)
    n_real = n - n_bait - n_dead

    urls: list[str] = []
    urls += [_bait_url(i) for i in range(n_bait)]
    urls += [_dead_url(i) for i in range(n_dead)]
    urls += [real[i % len(real)] for i in range(n_real)]
    random.shuffle(urls)  # перемешать, чтобы bait шёл вперемешку (стресс пула в течение всего прогона)

    Path(out).write_text("\n".join(urls) + "\n", encoding="utf-8")
    print(f"OK: {out} — {len(urls)} URL  (bait={n_bait}, dead={n_dead}, real={n_real})")
    print(f"Грузить на dev:  curl -F 'file=@{out}' http://localhost:8769/api/v1/tasks/upload")
    print("Смотреть:        make logs | grep -E 'site_timeout|task_done'  → ждём task_done (completed), без зависания")


if __name__ == "__main__":
    main()
