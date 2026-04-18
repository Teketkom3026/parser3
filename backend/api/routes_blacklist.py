"""Blacklist endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.api.deps import get_db
from backend.storage.db import Database


router = APIRouter(prefix="/blacklist", tags=["blacklist"])


class BlacklistEntry(BaseModel):
    entry_type: str  # position | fio | email | domain
    value: str
    source: str = "user"


@router.get("")
async def list_blacklist(db: Database = Depends(get_db)):
    return {"items": await db.list_blacklist()}


@router.post("", status_code=201)
async def add_blacklist(entry: BlacklistEntry, db: Database = Depends(get_db)):
    if entry.entry_type not in ("position", "fio", "email", "domain"):
        raise HTTPException(status_code=400, detail="invalid entry_type")
    await db.add_blacklist(entry.entry_type, entry.value.strip(), entry.source)
    return {"status": "added"}


@router.delete("/{id}")
async def remove_blacklist(id: int, db: Database = Depends(get_db)):
    await db.remove_blacklist(id)
    return {"status": "removed"}
