"""Pytest bootstrap — делает `pytest tests/` запускаемым без ручных env-переменных.

`backend/core/config.py` читает пути хранилища из окружения на этапе импорта и
делает под них mkdir. Здесь направляем их во временную папку и выключаем браузер,
чтобы прогон был герметичным. Используем setdefault — явно заданный env всё ещё
побеждает (например, команда из HANDOFF.md с DATA_DIR=/tmp/p3/...).
"""
import os
import sys
import tempfile
from pathlib import Path

# Корень проекта в sys.path, чтобы работали `import backend...` и `import tests...`.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_TMP = Path(tempfile.gettempdir()) / "parser3_tests"
os.environ.setdefault("DATA_DIR", str(_TMP / "data"))
os.environ.setdefault("RESULTS_DIR", str(_TMP / "results"))
os.environ.setdefault("LOG_DIR", str(_TMP / "logs"))
os.environ.setdefault("SQLITE_DB_PATH", str(_TMP / "parser3.db"))
os.environ.setdefault("FETCH_USE_BROWSER", "false")
