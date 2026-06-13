"""Memory manager filtering, human-in-the-loop actions, backup, and restore."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Config
from .db import DatabaseManager


@dataclass(frozen=True)
class BackupResult:
    backup_id: str
    backup_path: str
    item_count: int
    created_at: str


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
    """Memory Manager APIs for listing, item actions, backup, and restore."""

    def __init__(self, *, config: Config | None = None, db: DatabaseManager) -> None:
        self._config = config
        self._db = db

    # Allowed filter names mapped to safe WHERE clauses (no user input interpolated)
    _FILTER_CLAUSES: dict[str, str] = {
        "all": "1=1",
        "rules": "type = 'rule'",
        "symbols": "type = 'symbol'",
        "file_summaries": "type = 'file_summary'",
        "stale": "stale = 1",
        "pending_approval": (
            "trust_level IN (4, 5) AND ("
            "json_extract(tags_json, '$.pending_approval') = 1"
            " OR json_extract(tags_json, '$.pending_approval') = true)"
        ),
    }

    async def list_items(self, *, filter_name: str, limit: int) -> list[MemoryItem]:
        conn = await self._db.connect()

        where_clause = self._FILTER_CLAUSES.get(filter_name)
        if where_clause is None:
            raise ValueError(
                f"Invalid filter_name '{filter_name}'. "
                f"Allowed: {', '.join(self._FILTER_CLAUSES.keys())}"
            )

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

    # ------------------------------------------------------------------
    # Backup / Restore (v1.5)
    # ------------------------------------------------------------------

    async def backup_memory(self) -> BackupResult:
        if self._config is None:
            raise RuntimeError("config is required for backup/restore operations")
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT
                id, type, title, body, source, source_path,
                trust_level, tags_json, stale, created_at, updated_at
            FROM memory_items
            ORDER BY updated_at DESC
            """
        )
        rows = await cursor.fetchall()
        payload = [
            {
                "id": row["id"],
                "type": row["type"],
                "title": row["title"],
                "body": row["body"],
                "source": row["source"],
                "source_path": row["source_path"],
                "trust_level": row["trust_level"],
                "tags_json": row["tags_json"],
                "stale": row["stale"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

        backup_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        backup_dir = self._config.memopilot_dir / "snapshots" / "memory-backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"{backup_id}.json"
        backup_path.write_text(
            json.dumps(
                {
                    "backup_id": backup_id,
                    "created_at": datetime.now(UTC).isoformat(),
                    "item_count": len(payload),
                    "items": payload,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return BackupResult(
            backup_id=backup_id,
            backup_path=str(backup_path),
            item_count=len(payload),
            created_at=datetime.now(UTC).isoformat(),
        )

    async def restore_memory(self, *, backup_path: str) -> int:
        if self._config is None:
            raise RuntimeError("config is required for backup/restore operations")
        source = Path(backup_path)
        if not source.is_absolute():
            source = (self._config.workspace_path / source).resolve()
        if not source.exists():
            raise ValueError(f"backup file not found: {source}")

        payload = json.loads(source.read_text(encoding="utf-8"))
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValueError("invalid backup payload")

        conn = await self._db.connect()
        await conn.execute("DELETE FROM memory_items")
        for item in items:
            await conn.execute(
                """
                INSERT INTO memory_items
                (
                    id, type, title, body, source, source_path, trust_level,
                    tags_json, stale, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("id") or uuid.uuid4().hex,
                    item.get("type", "note"),
                    item.get("title", "untitled"),
                    item.get("body", ""),
                    item.get("source", "backup"),
                    item.get("source_path"),
                    int(item.get("trust_level", 3)),
                    item.get("tags_json"),
                    int(item.get("stale", 0)),
                    item.get("created_at", datetime.now(UTC).isoformat()),
                    item.get("updated_at", datetime.now(UTC).isoformat()),
                ),
            )
        await conn.commit()
        return len(items)
