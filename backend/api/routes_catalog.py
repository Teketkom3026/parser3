"""Catalog introspection endpoints."""
from __future__ import annotations

from fastapi import APIRouter

from backend.catalog.loader import get_catalog


router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/positions")
async def list_positions():
    cat = get_catalog()
    return {
        "positions": [
            {
                "id": p.id,
                "canonical": p.canonical,
                "category": p.category,
                "sheet": p.sheet,
                "priority": p.priority,
                "aliases_count": len(p.aliases),
            }
            for p in cat.positions
        ]
    }
