"""Application config."""
from __future__ import annotations

import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "parser3"
    root_path: str = "/parser3"

    # Storage
    data_dir: str = "/app/data"
    results_dir: str = "/app/results"
    log_dir: str = "/app/data/logs"
    sqlite_db_path: str = "/app/data/parser3.db"

    # Crawling
    crawler_max_concurrent: int = 4
    crawler_page_timeout_sec: int = 25
    crawler_delay_min_sec: float = 0.3
    crawler_delay_max_sec: float = 1.0
    browser_pool_size: int = 3
    fetch_use_browser: bool = True  # if false — httpx only (for CI/tests)

    # CORS
    cors_origins: str = "*"


settings = Settings()

# Ensure dirs exist (only when running as a service, not during import in tests)
for d in (settings.data_dir, settings.results_dir, settings.log_dir):
    try:
        Path(d).mkdir(parents=True, exist_ok=True)
    except PermissionError:
        pass
