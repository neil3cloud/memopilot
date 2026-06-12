"""Memory manager filtering and human-in-the-loop actions."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from .db import DatabaseManager


@dataclass(frozen=True)
class MemoryItem:
    id: str
    type: str
    title: str
    body: str
    source: str
    source_path: str | None
    trust_level: int
    stale: bool
    tags: dict[str, Any] | list[Any] | None
    created_at: str
    updated_at: str


class MemoryManagerService:
    """Memory Manager APIs for listing and item actions."""

    def __init__(self, *, db: DatabaseManager) -> None:
        self._db = db

    _FILTER_CLAUSES: dict[str, str] = {
        "all": "1=1",
        "rules": "type = 'rule'",
        "symbols": "type = 'symbol'",
        "file_summaries": "type = 'file_summary'",
        "stale": "stale = 1",
        "pending_approval": (
            "trust_level IN (4, 5) AND ("
            " json_extract(tags_json, '$.pending_approval') = 1"
            " OR json_extract(tags_json, '$.pending_approval') = true"
            ")"
        ),
    }

    async def list_items(self, *, filter_name: str, limit: int) -> list[MemoryItem]:
        conn = await self._db.connect()

        where_clause = self._FILTER_CLAUSES.get(filter_name)
        if where_clause is None:
            raise ValueError(f"Unknown filter: {filter_name}")

        cursor = await conn.execute(
            f"""
            SELECT
                id, type, title, body, source, source_path, trust_level,
                stale, tags_json, created_at, updated_at
            FROM memory_items
            WHERE {where_clause}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_item(row) for row in rows]

    async def suggest_memory_update(
        self,
        *,
        title: str,
        body: str,
        source: str,
        source_path: str | None = None,
        tags: dict[str, Any] | None = None,
    ) -> str:
        conn = await self._db.connect()
        item_id = uuid.uuid4().hex
        merged_tags: dict[str, Any] = {"pending_approval": True}
        if tags:
            merged_tags.update(tags)

        await conn.execute(
            """
            INSERT INTO memory_items
            (id, type, title, body, source, source_path, source_hash, trust_level, tags_json, stale)
            VALUES (?, 'ai_summary', ?, ?, ?, ?, NULL, 4, ?, 0)
            """,
            (
                item_id,
                title,
                body,
                source,
                source_path,
                json.dumps(merged_tags),
            ),
        )
        await conn.commit()
        return item_id

    async def approve_item(self, item_id: str) -> None:
        conn = await self._db.connect()
        item = await self._fetch_item_row(item_id)
        tags = self._parse_tags(item["tags_json"])
        if isinstance(tags, dict):
            tags["pending_approval"] = False
            tags["approved"] = True
        await conn.execute(
            """
            UPDATE memory_items
            SET trust_level = 3, tags_json = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (json.dumps(tags), item_id),
        )
        await conn.commit()

    async def reject_item(self, item_id: str) -> None:
        conn = await self._db.connect()
        await self._require_item(item_id)
        await conn.execute("DELETE FROM memory_items WHERE id = ?", (item_id,))
        await conn.commit()

    async def edit_item(self, item_id: str, *, title: str, body: str) -> None:
        conn = await self._db.connect()
        await self._require_item(item_id)
        await conn.execute(
            """
            UPDATE memory_items
            SET title = ?, body = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (title, body, item_id),
        )
        await conn.commit()

    async def delete_item(self, item_id: str) -> None:
        await self.reject_item(item_id)

    async def rebuild_item(self, item_id: str) -> None:
        conn = await self._db.connect()
        await self._require_item(item_id)
        await conn.execute(
            """
            UPDATE memory_items
            SET stale = 0, updated_at = datetime('now')
            WHERE id = ?
            """,
            (item_id,),
        )
        await conn.commit()

    async def _fetch_item_row(self, item_id: str):
        conn = await self._db.connect()
        cursor = await conn.execute(
            "SELECT id, tags_json FROM memory_items WHERE id = ?",
            (item_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError(f"Memory item not found: {item_id}")
        return row

    async def _require_item(self, item_id: str) -> None:
        await self._fetch_item_row(item_id)

    def _parse_tags(self, raw: str | None) -> dict[str, Any] | list[Any] | None:
        if raw is None:
            return {}
        parsed = json.loads(raw)
        if isinstance(parsed, (dict, list)):
            return parsed
        return {}

    def _row_to_item(self, row) -> MemoryItem:
        return MemoryItem(
            id=str(row["id"]),
            type=str(row["type"]),
            title=str(row["title"]),
            body=str(row["body"]),
            source=str(row["source"]),
            source_path=row["source_path"],
            trust_level=int(row["trust_level"]),
            stale=bool(row["stale"]),
            tags=self._parse_tags(row["tags_json"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
