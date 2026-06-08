"""Golden-тест: гоняет харнесс по кейсам из cases.yaml.

Три уровня проверки на кейс:
  1. min_people  — счётчик-метка (дёшево размечается человеком: «тут N человек»);
  2. must_find / must_not_find — точечные подстроки (фамилия, email, ИНН / мусор);
  3. snapshot     — полная сводка заморожена в snapshots/<id>.json; падает на ЛЮБОМ
                    изменении вывода. Это автоматизация ручного A/B на 241 сайт.

Бутстрап: если snapshot ещё нет — он создаётся, кейс помечается SKIP («review & commit»).
Принять новый baseline после ОСОЗНАННОГО изменения:  GOLDEN_UPDATE=1 pytest tests/golden
Нет файла фикстуры — кейс SKIP (скелеты из писем клиента ждут сохранённого HTML).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from tests.golden.harness import summarize

_ROOT = Path(__file__).resolve().parent
_FIXTURES = _ROOT.parent / "fixtures" / "html"
_SNAP_DIR = _ROOT / "snapshots"
_CASES = yaml.safe_load((_ROOT / "cases.yaml").read_text(encoding="utf-8")) or []
_UPDATE = os.environ.get("GOLDEN_UPDATE") == "1"


def _case_id(case: dict) -> str:
    return case.get("id") or case.get("file") or "?"


@pytest.mark.parametrize("case", _CASES, ids=[_case_id(c) for c in _CASES])
def test_golden(case: dict):
    cid = _case_id(case)
    fx = _FIXTURES / case["file"]
    if not fx.exists():
        pytest.skip(f"нет фикстуры {case['file']} — сохраните: "
                    f"python tools/save_fixture.py {case.get('url', '<url>')} "
                    f"{Path(case['file']).stem}")

    html = fx.read_text(encoding="utf-8", errors="ignore")
    summary = summarize(html, case["url"], case.get("page_score"))

    # 1) счётчик
    min_people = case.get("min_people")
    if min_people is not None:
        assert summary["n_people"] >= min_people, (
            f"{cid}: ожидалось >= {min_people} ФИО, получено {summary['n_people']}"
        )

    # 2) точечные подстроки (регистронезависимо)
    blob = json.dumps(summary, ensure_ascii=False).lower()
    for needle in case.get("must_find") or []:
        assert str(needle).lower() in blob, f"{cid}: must_find отсутствует: {needle!r}"
    for needle in case.get("must_not_find") or []:
        assert str(needle).lower() not in blob, f"{cid}: must_not_find присутствует: {needle!r}"

    # 3) snapshot
    _SNAP_DIR.mkdir(exist_ok=True)
    snap = _SNAP_DIR / f"{cid}.json"
    current = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
    if _UPDATE or not snap.exists():
        snap.write_text(current + "\n", encoding="utf-8")
        if not _UPDATE:
            pytest.skip(f"{cid}: snapshot создан — просмотрите snapshots/{cid}.json и закоммитьте")
        return
    expected = snap.read_text(encoding="utf-8").rstrip("\n")
    assert current == expected, (
        f"{cid}: вывод изменился относительно snapshot.\n"
        f"Посмотрите diff snapshots/{cid}.json. Если изменение осознанное — "
        f"примите новый baseline: GOLDEN_UPDATE=1 pytest tests/golden -k {cid}"
    )
