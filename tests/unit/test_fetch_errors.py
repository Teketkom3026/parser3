"""Классификация сетевых ошибок fetch → error_code + человекочитаемые сообщения."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx

from backend.fetcher.fetcher import _classify_exc, _http_reason
from backend.core.errors import human_error


def test_classify_exc_timeouts():
    assert _classify_exc(httpx.ConnectTimeout("t")) == "connect_timeout"
    assert _classify_exc(httpx.ReadTimeout("t")) == "read_timeout"
    assert _classify_exc(httpx.PoolTimeout("t")) == "read_timeout"


def test_classify_exc_connect_variants():
    assert _classify_exc(httpx.ConnectError("Connection refused")) == "conn_refused"
    assert _classify_exc(httpx.ConnectError("Name or service not known")) == "dns_nxdomain"
    assert _classify_exc(httpx.ConnectError("Network is unreachable")) == "conn_error"
    assert _classify_exc(httpx.ReadError("reset")) == "conn_error"


def test_classify_exc_ssl():
    assert _classify_exc(httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] cert")) == "ssl_error"
    assert _classify_exc(Exception("TLS handshake failed")) == "ssl_error"


def test_http_reason_buckets():
    assert _http_reason(403) == "http_403"
    assert _http_reason(429) == "http_429"
    assert _http_reason(404) == "http_4xx"
    assert _http_reason(410) == "http_4xx"
    assert _http_reason(502) == "http_5xx"
    assert _http_reason(500) == "http_5xx"


def test_human_error_messages():
    assert human_error("dns_nxdomain") == "Сайт не существует (DNS err NXDOMAIN)"
    assert human_error("ssl_error") == "Не удалось открыть сайт (проблема с SSL/TLS)"
    assert human_error("http_403") == "Не удалось открыть сайт (403 — вероятно блок по IP)"
    # точный статус уточняет http_4xx/5xx
    assert human_error("http_4xx", status=404) == "Не удалось открыть сайт (HTTP 404)"
    assert human_error("http_5xx", status=502) == "Не удалось открыть сайт (HTTP 502)"
    # таймаут с деталью
    assert human_error("timeout", detail="180с") == "Обработка прервана по таймауту (180с)"
    # неизвестный код — безопасный фолбэк
    assert "неизвестн" in human_error("fetch_failed").lower()
    assert human_error("totally_new_code") == "Не удалось открыть сайт (totally_new_code)"
