"""Phone extraction/normalization via phonenumbers.

Output format: digits-only with country code, no '+' (e.g. '79161234567').
Filters out toll-free / hotline numbers (8-800, 7-800).
"""
from __future__ import annotations

import re
from typing import List


# Toll-free / hotline prefixes (after normalization to digits-only).
_HOTLINE_PREFIXES = ("7800", "8800")

_MIN_DIGITS = 10
_MAX_DIGITS = 15


def _is_hotline(digits: str) -> bool:
    if not digits:
        return False
    if digits.startswith(_HOTLINE_PREFIXES):
        return True
    # Defensive: stripped-CC variants ("8001234567")
    if len(digits) == 10 and digits.startswith("800"):
        return True
    return False


def _digits_only(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def extract_phones(text: str, default_region: str = "RU") -> List[str]:
    """Extract phone numbers from text.

    Returns a list of digits-only strings with country code (no '+').
    Filters:
      - hotline / toll-free numbers (8-800, 7-800)
      - invalid numbers (per `phonenumbers.is_valid_number`)
      - numbers shorter than 10 digits
    """
    if not text:
        return []
    results: List[str] = []
    seen = set()
    try:
        import phonenumbers
        for match in phonenumbers.PhoneNumberMatcher(text, default_region):
            num = match.number
            if not phonenumbers.is_valid_number(num):
                continue
            e164 = phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
            digits = e164.lstrip("+")
            if not digits or len(digits) < _MIN_DIGITS or len(digits) > _MAX_DIGITS:
                continue
            if _is_hotline(digits):
                continue
            if digits in seen:
                continue
            seen.add(digits)
            results.append(digits)
    except Exception:
        # Fallback regex — Russian numbers only.
        for m in re.finditer(
            r"(?:\+?7|8)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}",
            text,
        ):
            raw = m.group(0)
            digits = _digits_only(raw)
            if len(digits) == 11 and digits.startswith("8"):
                digits = "7" + digits[1:]
            if len(digits) != 11 or not digits.startswith("7"):
                continue
            if _is_hotline(digits):
                continue
            if digits in seen:
                continue
            seen.add(digits)
            results.append(digits)
    return results
