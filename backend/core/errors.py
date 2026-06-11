"""Человекочитаемые сообщения об ошибках обработки сайта.

`error_code` — машинный код (для разбивки/группировки в БД и логах),
`error_message` — строка для человека (колонка «Ошибка» в UI, TaskPage.tsx).
Стиль: «Сайт не существует (DNS err NXDOMAIN)», «Не удалось открыть сайт (403 — …)».
"""
from __future__ import annotations

from typing import Optional


# Базовые сообщения по коду. http_4xx/http_5xx/timeout уточняются статусом/деталью.
_MESSAGES = {
    # DNS (process_site precheck, G2)
    "dns_nxdomain":    "Сайт не существует (DNS err NXDOMAIN)",
    "dns_timeout":     "Не удалось открыть сайт (DNS не отвечает)",
    # Соединение (fetcher)
    "ssl_error":       "Не удалось открыть сайт (проблема с SSL/TLS)",
    "conn_refused":    "Не удалось открыть сайт (соединение отклонено)",
    "conn_error":      "Не удалось открыть сайт (ошибка соединения)",
    "connect_timeout": "Не удалось открыть сайт (таймаут подключения)",
    "read_timeout":    "Не удалось открыть сайт (таймаут чтения страницы)",
    # HTTP-статусы
    "http_403":        "Не удалось открыть сайт (403 — вероятно блок по IP)",
    "http_429":        "Не удалось открыть сайт (429 — слишком много запросов)",
    "http_4xx":        "Не удалось открыть сайт (страница недоступна, HTTP 4xx)",
    "http_5xx":        "Не удалось открыть сайт (ошибка на стороне сервера, 5xx)",
    # Контент / пайплайн
    "empty_html":      "Сайт открылся, но без содержимого",
    "timeout":         "Обработка прервана по таймауту",
    "exception":       "Внутренняя ошибка обработки",
    "fetch_failed":    "Не удалось открыть сайт (неизвестная ошибка)",
}


def human_error(code: str, *, status: Optional[int] = None, detail: Optional[str] = None) -> str:
    """Вернуть человекочитаемое сообщение по коду ошибки.

    status — HTTP-статус (уточняет http_4xx/http_5xx точным числом);
    detail — доп. контекст (напр. лимит таймаута «180с»).
    """
    if status is not None and 400 <= status < 600 and code in ("http_4xx", "http_5xx"):
        return f"Не удалось открыть сайт (HTTP {status})"
    base = _MESSAGES.get(code)
    if base is None:
        return f"Не удалось открыть сайт ({code})"
    if detail:
        return f"{base} ({detail})"
    return base
