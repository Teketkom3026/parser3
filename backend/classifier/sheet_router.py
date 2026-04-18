"""Exclusive sheet routing."""
from __future__ import annotations

from typing import Optional

from backend.normalizer.position import NormalizedPosition


ROLE_SHEETS = {
    "Генеральные директора",
    "Финансовые директора",
    "Главные бухгалтеры",
    "Главные инженеры",
}

FALLBACK_SHEET = "Остальные"


def route(norm: NormalizedPosition, person_full_name: str) -> str:
    """Return exclusive sheet name for a contact.

    A contact goes to role sheet only if:
      - normalizer returned a non-null sheet (i.e. matched canonical role and no exclude_modifier)
      - person_full_name is non-empty
    Otherwise → FALLBACK_SHEET.
    """
    if not person_full_name or not person_full_name.strip():
        return FALLBACK_SHEET
    if norm.sheet and norm.sheet in ROLE_SHEETS:
        return norm.sheet
    return FALLBACK_SHEET
