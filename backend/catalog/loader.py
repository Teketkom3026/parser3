"""Catalog loader."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml


@dataclass
class PositionEntry:
    id: str
    canonical: str
    aliases: List[str] = field(default_factory=list)
    pymorphy_keywords: List[str] = field(default_factory=list)
    category: str = "Другое"
    sheet: Optional[str] = None
    priority: int = 0
    exclude_modifiers: List[str] = field(default_factory=list)
    requires_any: List[str] = field(default_factory=list)


@dataclass
class Catalog:
    positions: List[PositionEntry]
    companies_stopwords: Dict[str, List[str]]
    fio_stopwords: Dict[str, List[str]]


_CATALOG: Optional[Catalog] = None


def load_catalog(base: Optional[Path] = None) -> Catalog:
    global _CATALOG
    if _CATALOG is not None:
        return _CATALOG
    base = base or Path(__file__).parent
    positions_data = yaml.safe_load((base / "positions.yaml").read_text(encoding="utf-8")) or []
    positions = [PositionEntry(**p) for p in positions_data]
    comp = yaml.safe_load((base / "companies_stopwords.yaml").read_text(encoding="utf-8")) or {}
    fio = yaml.safe_load((base / "fio_stopwords.yaml").read_text(encoding="utf-8")) or {}
    _CATALOG = Catalog(positions=positions, companies_stopwords=comp, fio_stopwords=fio)
    return _CATALOG


def get_catalog() -> Catalog:
    return load_catalog()
