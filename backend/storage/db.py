"""SQLite db wrapper with aiosqlite."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite


class Database:
    def __init__(self, path: str):
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self.migrate()

    async def migrate(self):
        migrations_dir = Path(__file__).parent / "migrations"
        for sql_file in sorted(migrations_dir.glob("*.sql")):
            sql = sql_file.read_text(encoding="utf-8")
            await self._conn.executescript(sql)
        await self._conn.commit()

    async def close(self):
        if self._conn:
            await self._conn.close()

    async def execute(self, sql: str, params: tuple = ()):
        async with self._conn.execute(sql, params) as cur:
            return cur

    async def commit(self):
        await self._conn.commit()

    async def fetchone(self, sql: str, params: tuple = ()) -> Optional[Dict]:
        async with self._conn.execute(sql, params) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def fetchall(self, sql: str, params: tuple = ()) -> List[Dict]:
        async with self._conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    # Tasks
    async def create_task(self, task_id: str, mode: str, total_urls: int,
                          input_file: str = "", target_positions: List[str] | None = None):
        await self._conn.execute(
            """INSERT INTO tasks (id, mode, status, input_file, target_positions, total_urls)
               VALUES (?, ?, 'pending', ?, ?, ?)""",
            (task_id, mode, input_file, json.dumps(target_positions or []), total_urls),
        )
        await self._conn.commit()

    async def update_task(self, task_id: str, **fields):
        if not fields:
            return
        keys = ", ".join(f"{k}=?" for k in fields)
        values = list(fields.values())
        values.append(task_id)
        await self._conn.execute(
            f"UPDATE tasks SET {keys}, updated_at=CURRENT_TIMESTAMP WHERE id=?", values
        )
        await self._conn.commit()

    async def get_task(self, task_id: str) -> Optional[Dict]:
        t = await self.fetchone("SELECT * FROM tasks WHERE id=?", (task_id,))
        if t and t.get("target_positions"):
            try:
                t["target_positions"] = json.loads(t["target_positions"])
            except Exception:
                pass
        return t

    async def list_tasks(self, limit: int = 100, offset: int = 0) -> List[Dict]:
        return await self.fetchall(
            "SELECT id, mode, status, total_urls, processed_urls, found_contacts, "
            "errors_count, output_file, created_at, updated_at, completed_at "
            "FROM tasks ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )

    async def delete_task(self, task_id: str):
        await self._conn.execute("DELETE FROM contacts WHERE task_id=?", (task_id,))
        await self._conn.execute("DELETE FROM sites WHERE task_id=?", (task_id,))
        await self._conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        await self._conn.commit()

    # Sites
    async def add_site(self, task_id: str, url: str) -> int:
        cur = await self._conn.execute(
            "INSERT OR IGNORE INTO sites (task_id, url) VALUES (?, ?)", (task_id, url)
        )
        await self._conn.commit()
        if cur.lastrowid:
            return cur.lastrowid
        row = await self.fetchone("SELECT id FROM sites WHERE task_id=? AND url=?", (task_id, url))
        return row["id"] if row else 0

    async def update_site(self, site_id: int, **fields):
        if not fields:
            return
        keys = ", ".join(f"{k}=?" for k in fields)
        values = list(fields.values()) + [site_id]
        await self._conn.execute(
            f"UPDATE sites SET {keys}, updated_at=CURRENT_TIMESTAMP WHERE id=?", values
        )
        await self._conn.commit()

    async def list_sites(self, task_id: str) -> List[Dict]:
        return await self.fetchall("SELECT * FROM sites WHERE task_id=?", (task_id,))

    # Contacts
    async def save_contacts(self, task_id: str, contacts: List[Dict]):
        if not contacts:
            return
        rows = []
        for c in contacts:
            social = c.get("social_links") or []
            if isinstance(social, list):
                social = json.dumps(social, ensure_ascii=False)
            rows.append((
                task_id,
                c.get("site_id"),
                c.get("domain") or "",
                c.get("page_url") or "",
                c.get("company_name"),
                c.get("company_email"),
                c.get("company_phone"),
                c.get("full_name"),
                c.get("last_name"),
                c.get("first_name"),
                c.get("patronymic"),
                c.get("gender"),
                c.get("position_raw"),
                c.get("position_canonical"),
                c.get("role_category"),
                c.get("matched_entry_id"),
                c.get("norm_method"),
                c.get("sheet_name"),
                c.get("person_email"),
                c.get("person_phone"),
                c.get("inn"),
                c.get("kpp"),
                social,
                c.get("language"),
                c.get("status") or "ok",
                c.get("comment"),
                c.get("dedup_key"),
            ))
        await self._conn.executemany(
            """INSERT INTO contacts (task_id, site_id, domain, page_url, company_name,
                company_email, company_phone, full_name, last_name, first_name, patronymic,
                gender, position_raw, position_canonical, role_category, matched_entry_id,
                norm_method, sheet_name, person_email, person_phone, inn, kpp,
                social_links, language, status, comment, dedup_key)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        await self._conn.commit()

    async def list_contacts(self, task_id: str) -> List[Dict]:
        rows = await self.fetchall("SELECT * FROM contacts WHERE task_id=?", (task_id,))
        for r in rows:
            if r.get("social_links"):
                try:
                    r["social_links"] = json.loads(r["social_links"])
                except Exception:
                    r["social_links"] = []
        return rows

    # Blacklist
    async def list_blacklist(self) -> List[Dict]:
        return await self.fetchall("SELECT * FROM blacklist ORDER BY added_at DESC")

    async def add_blacklist(self, entry_type: str, value: str, source: str = "user"):
        await self._conn.execute(
            "INSERT OR IGNORE INTO blacklist (entry_type, entry_value, source) VALUES (?, ?, ?)",
            (entry_type, value, source),
        )
        await self._conn.commit()

    async def remove_blacklist(self, id_: int):
        await self._conn.execute("DELETE FROM blacklist WHERE id=?", (id_,))
        await self._conn.commit()
