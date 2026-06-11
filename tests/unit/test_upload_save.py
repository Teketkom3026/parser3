"""Сохранение сырого загруженного файла задачи (для повторного прогона 1:1)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.api import routes_tasks as rt
from backend.core.config import settings


def test_save_and_find_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    data = b"site1.ru\nsite1.ru\nhttps://site2.ru/\n"  # с дублем — сохраняем как есть
    saved = rt._save_upload("abc123def456", "../Список клиента.txt", data)
    assert saved is not None

    found = rt._find_upload("abc123def456")
    assert found is not None
    # содержимое 1:1, включая дубль
    assert found.read_bytes() == data
    # имя без путей и небезопасных символов, с префиксом task_id
    assert found.name.startswith("abc123def456__")
    assert "/" not in found.name and ".." not in found.name


def test_find_upload_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    assert rt._find_upload("nope") is None
