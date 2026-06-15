"""Memory manager filtering, human-in-the-loop actions, backup, and restore."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .backup import create_backup, restore_backup
from .config import Config
from .db import DatabaseManager
from .memory_governance import validate_status_transition
from .security_policy import CredentialRedactor

MAX_MEMORY_CONTENT_BYTES = 16 * 1024
_MAX_DIFF_CONTENT_LINES = 200
_FALLBACK_SECRET_PATTERNS = (
    re.compile(
        r"(?i)\b(api[_-]?key|token|password|secret)\b(\s*[:=]\s*)([^\s,;\"'}]+|\"[^\"]+\"|'[^']+')"
    ),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]+"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
)
_TRANSCRIPT_PATTERNS = (
    re.compile(r"(?im)^(?:user|assistant|human|ai|system)\s*:.*$"),
    re.compile(r'"role"\s*:\s*"(?:user|assistant|human|ai|system)"'),
)


def check_writeback_safety(content: str) -> tuple[bool, str]:
    """Check if content is safe for memory write-back.

    Returns (is_safe, blocked_reason).
    """
    redactor = CredentialRedactor()
    try:
        redaction = redactor.redact(content)
        if redaction.redacted_count > 0:
            return False, "memory content appears to contain secrets"
    except Exception:
        if any(pattern.search(content) for pattern in _FALLBACK_SECRET_PATTERNS):
            return False, "memory content appears to contain secrets"

    diff_line_count = sum(
        1
        for line in content.splitlines()
        if (line.startswith("+") and not line.startswith("+++"))
        or (line.startswith("-") and not line.startswith("---"))
    )
    if diff_line_count > _MAX_DIFF_CONTENT_LINES:
        return False, (
            f"memory content appears to contain a full diff "
            f"(>{_MAX_DIFF_CONTENT_LINES} changed lines)"
        )

    if any(pattern.search(content) for pattern in _TRANSCRIPT_PATTERNS):
        return False, "memory content appears to be a raw transcript"

    return True, ""


@dataclass(frozen=True)
class BackupResult:
    backup_id: str
    backup_path: str
    item_count: int
    created_at: str
    manifest: dict[str, Any]


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
    memory_class: str
    memory_status: str
    visibility_scope: str
    reusable: bool
    review_required: bool
    created_at: str
    updated_at: str
    usage_stats: dict[str, Any] | None = None


@dataclass(frozen=True)
class SuggestMemoryOutcome:
    memory_item_id: str | None
    pending_approval: bool
    artifact_id: str | None = None
    blocked_reason: str | None = None


class MemoryManagerService:
    """Memory Manager APIs for listing, item actions, backup, and restore."""

    def __init__(self, *, config: Config | None = None, db: DatabaseManager) -> None:
        self._config = config
        self._db = db

    def _normalize_workspace_root(self, workspace_root: str | None) -> str | None:
        if workspace_root is None or not workspace_root.strip():
            if self._config is None:
                return None
            return str(self._config.workspace_path.resolve())
        return str(Path(workspace_root).resolve())

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

    async def list_items(
        self,
        *,
        filter_name: str,
        limit: int,
        workspace_root: str | None = None,
    ) -> list[MemoryItem]:
        conn = await self._db.connect()

        where_clause = self._FILTER_CLAUSES.get(filter_name)
        if where_clause is None:
            raise ValueError(
                f"Invalid filter_name '{filter_name}'. "
                f"Allowed: {', '.join(self._FILTER_CLAUSES.keys())}"
            )

        normalized_root = self._normalize_workspace_root(workspace_root)
        query = f"""
            SELECT
                id, type, title, body, source, source_path, trust_level,
                stale, tags_json, COALESCE(memory_class, 'fact') AS memory_class,
                COALESCE(memory_status, 'discovered') AS memory_status,
                COALESCE(visibility_scope, 'workspace') AS visibility_scope,
                reusable, review_required, created_at, updated_at
            FROM memory_items
            WHERE {where_clause}
        """
        params: list[Any] = []
        if normalized_root is not None:
            query += " AND COALESCE(workspace_root, ?) = ?"
            params.extend([normalized_root, normalized_root])
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)

        cursor = await conn.execute(query, tuple(params))
        rows = await cursor.fetchall()
        return await self._rows_to_items(rows)

    async def suggest_memory_update(
        self,
        *,
        title: str,
        body: str,
        source: str,
        source_path: str | None = None,
        tags: dict[str, Any] | None = None,
        task_run_id: str | None = None,
        workspace_root: str | None = None,
    ) -> SuggestMemoryOutcome:
        conn = await self._db.connect()
        normalized_root = self._normalize_workspace_root(workspace_root)
        blocked_reason = self._get_blocked_reason(title=title, body=body)
        if blocked_reason is not None:
            artifact_id = await self._store_blocked_artifact(
                conn=conn,
                title=title,
                body=body,
                blocked_reason=blocked_reason,
                task_run_id=task_run_id,
                workspace_root=normalized_root,
            )
            await conn.commit()
            return SuggestMemoryOutcome(
                memory_item_id=None,
                pending_approval=False,
                artifact_id=artifact_id,
                blocked_reason=blocked_reason,
            )

        item_id = uuid.uuid4().hex
        merged_tags: dict[str, Any] = {"pending_approval": True}
        if tags:
            merged_tags.update(tags)

        await conn.execute(
            """
            INSERT INTO memory_items
            (
                id, type, title, body, source, source_path, source_hash,
                trust_level, tags_json, stale, memory_class, memory_status,
                visibility_scope, reusable, review_required, workspace_root
            )
            VALUES (
                ?, 'ai_summary', ?, ?, ?, ?, NULL, 4, ?, 0, 'fact',
                'pending_review', 'workspace', 0, 1, ?
            )
            """,
            (
                item_id,
                title,
                body,
                source,
                source_path,
                json.dumps(merged_tags),
                normalized_root,
            ),
        )
        await conn.commit()
        return SuggestMemoryOutcome(memory_item_id=item_id, pending_approval=True)

    async def suggest_memory_update_smart(
        self,
        *,
        title: str,
        body: str,
        source: str,
        source_path: str | None = None,
        tags: dict[str, Any] | None = None,
        task_run_id: str | None = None,
        workspace_root: str | None = None,
        memory_class: str = "fact",
        derivation_source: str | None = None,
    ) -> SuggestMemoryOutcome:
        conn = await self._db.connect()
        normalized_root = self._normalize_workspace_root(workspace_root)
        blocked_reason = self._get_blocked_reason(title=title, body=body)
        if blocked_reason is not None:
            artifact_id = await self._store_blocked_artifact(
                conn=conn,
                title=title,
                body=body,
                blocked_reason=blocked_reason,
                task_run_id=task_run_id,
                workspace_root=normalized_root,
            )
            await conn.commit()
            return SuggestMemoryOutcome(
                memory_item_id=None,
                pending_approval=False,
                artifact_id=artifact_id,
                blocked_reason=blocked_reason,
            )

        auto_confirm = (
            memory_class == "fact"
            and derivation_source in {"git_diff", "call_graph"}
            and task_run_id is not None
        )
        item_id = uuid.uuid4().hex
        merged_tags: dict[str, Any] = dict(tags or {})
        merged_tags["pending_approval"] = not auto_confirm
        if auto_confirm:
            merged_tags["approved"] = True
        if derivation_source is not None:
            merged_tags["derivation_source"] = derivation_source

        await conn.execute(
            """
            INSERT INTO memory_items
            (
                id, type, title, body, source, source_path, source_hash,
                trust_level, tags_json, stale, memory_class, memory_status,
                visibility_scope, reusable, review_required, workspace_root
            )
            VALUES (
                ?, 'ai_summary', ?, ?, ?, ?, NULL, 4, ?, 0, ?, ?,
                'workspace', ?, ?, ?
            )
            """,
            (
                item_id,
                title,
                body,
                source,
                source_path,
                json.dumps(merged_tags),
                memory_class,
                "confirmed" if auto_confirm else "pending_review",
                1 if auto_confirm else 0,
                0 if auto_confirm else 1,
                normalized_root,
            ),
        )
        await conn.commit()
        return SuggestMemoryOutcome(memory_item_id=item_id, pending_approval=not auto_confirm)

    async def approve_item(self, item_id: str, *, workspace_root: str | None = None) -> None:
        conn = await self._db.connect()
        item = await self._fetch_item_row(item_id, workspace_root=workspace_root)
        tags = self._parse_tags(item["tags_json"])
        if isinstance(tags, dict):
            tags["pending_approval"] = False
            tags["approved"] = True
        await self._update_memory_status(
            conn=conn,
            item_id=item_id,
            current_status=str(item["memory_status"] or "discovered"),
            new_status="confirmed",
            tags=tags,
            trust_level=3,
            reusable=1,
            review_required=0,
        )
        await conn.commit()

    async def reject_item(self, item_id: str, *, workspace_root: str | None = None) -> None:
        conn = await self._db.connect()
        item = await self._fetch_item_row(item_id, workspace_root=workspace_root)
        tags = self._parse_tags(item["tags_json"])
        if isinstance(tags, dict):
            tags["pending_approval"] = False
            tags["approved"] = False
            tags["rejected"] = True
        await self._update_memory_status(
            conn=conn,
            item_id=item_id,
            current_status=str(item["memory_status"] or "discovered"),
            new_status="rejected",
            tags=tags,
            reusable=0,
            review_required=0,
        )
        await conn.commit()

    async def bulk_approve(
        self,
        memory_ids: list[str],
        *,
        workspace_root: str | None = None,
    ) -> None:
        if not memory_ids:
            return
        conn = await self._db.connect()
        rows = await self._fetch_item_rows(memory_ids, workspace_root=workspace_root)
        for row in rows:
            current_status = str(row["memory_status"] or "discovered")
            if not validate_status_transition(current_status, "confirmed"):
                continue
            tags = self._parse_tags(row["tags_json"])
            if isinstance(tags, dict):
                tags["pending_approval"] = False
                tags["approved"] = True
            await self._update_memory_status(
                conn=conn,
                item_id=str(row["id"]),
                current_status=current_status,
                new_status="confirmed",
                tags=tags,
                trust_level=3,
                reusable=1,
                review_required=0,
            )
        await conn.commit()

    async def bulk_reject(
        self,
        memory_ids: list[str],
        *,
        workspace_root: str | None = None,
    ) -> None:
        if not memory_ids:
            return
        conn = await self._db.connect()
        rows = await self._fetch_item_rows(memory_ids, workspace_root=workspace_root)
        for row in rows:
            current_status = str(row["memory_status"] or "discovered")
            if not validate_status_transition(current_status, "rejected"):
                continue
            tags = self._parse_tags(row["tags_json"])
            if isinstance(tags, dict):
                tags["pending_approval"] = False
                tags["approved"] = False
                tags["rejected"] = True
            await self._update_memory_status(
                conn=conn,
                item_id=str(row["id"]),
                current_status=current_status,
                new_status="rejected",
                tags=tags,
                reusable=0,
                review_required=0,
            )
        await conn.commit()

    async def bulk_delete(
        self,
        memory_ids: list[str],
        *,
        workspace_root: str | None = None,
    ) -> None:
        if not memory_ids:
            return
        conn = await self._db.connect()
        normalized_root = self._normalize_workspace_root(workspace_root)
        placeholders = ", ".join("?" for _ in memory_ids)
        await conn.execute(
            (
                f"DELETE FROM memory_items WHERE id IN ({placeholders}) "
                "AND (? IS NULL OR COALESCE(workspace_root, ?) = ?)"
            ),
            (*memory_ids, normalized_root, normalized_root, normalized_root),
        )
        await conn.commit()

    async def edit_item(
        self,
        item_id: str,
        *,
        title: str,
        body: str,
        workspace_root: str | None = None,
    ) -> None:
        conn = await self._db.connect()
        await self._require_item(item_id, workspace_root=workspace_root)
        normalized_root = self._normalize_workspace_root(workspace_root)
        await conn.execute(
            """
            UPDATE memory_items
            SET title = ?, body = ?, updated_at = datetime('now')
            WHERE id = ? AND (? IS NULL OR COALESCE(workspace_root, ?) = ?)
            """,
            (title, body, item_id, normalized_root, normalized_root, normalized_root),
        )
        await conn.commit()

    async def delete_item(self, item_id: str, *, workspace_root: str | None = None) -> None:
        conn = await self._db.connect()
        await self._require_item(item_id, workspace_root=workspace_root)
        normalized_root = self._normalize_workspace_root(workspace_root)
        await conn.execute(
            (
                "DELETE FROM memory_items WHERE id = ? "
                "AND (? IS NULL OR COALESCE(workspace_root, ?) = ?)"
            ),
            (item_id, normalized_root, normalized_root, normalized_root),
        )
        await conn.commit()

    async def rebuild_item(self, item_id: str, *, workspace_root: str | None = None) -> None:
        conn = await self._db.connect()
        item = await self._fetch_item_row(item_id, workspace_root=workspace_root)
        if str(item["memory_status"] or "discovered") == "stale":
            await self._update_memory_status(
                conn=conn,
                item_id=item_id,
                current_status="stale",
                new_status="confirmed",
                stale=0,
            )
        else:
            await conn.execute(
                """
                UPDATE memory_items
                SET stale = 0, updated_at = datetime('now')
                WHERE id = ?
                """,
                (item_id,),
            )
        await conn.commit()

    async def list_review_items(
        self,
        *,
        limit: int = 100,
        workspace_root: str | None = None,
    ) -> list[MemoryItem]:
        conn = await self._db.connect()
        normalized_root = self._normalize_workspace_root(workspace_root)
        query = """
            SELECT
                id, type, title, body, source, source_path, trust_level,
                stale, tags_json, COALESCE(memory_class, 'fact') AS memory_class,
                COALESCE(memory_status, 'discovered') AS memory_status,
                COALESCE(visibility_scope, 'workspace') AS visibility_scope,
                reusable, review_required, created_at, updated_at
            FROM memory_items
            WHERE memory_status = 'pending_review' AND review_required = 1
        """
        params: list[Any] = []
        if normalized_root is not None:
            query += " AND COALESCE(workspace_root, ?) = ?"
            params.extend([normalized_root, normalized_root])
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        cursor = await conn.execute(query, tuple(params))
        rows = await cursor.fetchall()
        return await self._rows_to_items(rows)

    async def get_pending_proposals_for_module(
        self,
        *,
        module_path: str,
        workspace_root: str | None = None,
        limit: int = 10,
    ) -> list[MemoryItem]:
        normalized_module_path = module_path.strip().replace("\\", "/")
        if not normalized_module_path:
            return []

        conn = await self._db.connect()
        normalized_root = self._normalize_workspace_root(workspace_root)
        query = """
            SELECT
                id, type, title, body, source, source_path, trust_level,
                stale, tags_json, COALESCE(memory_class, 'fact') AS memory_class,
                COALESCE(memory_status, 'discovered') AS memory_status,
                COALESCE(visibility_scope, 'workspace') AS visibility_scope,
                reusable, review_required, created_at, updated_at
            FROM memory_items
            WHERE memory_status = 'pending_review'
              AND (
                    REPLACE(COALESCE(source_path, ''), '\\', '/') LIKE ?
                 OR REPLACE(body, '\\', '/') LIKE ?
              )
        """
        params: list[Any] = [f"%{normalized_module_path}%", f"%{normalized_module_path}%"]
        if normalized_root is not None:
            query += " AND COALESCE(workspace_root, ?) = ?"
            params.extend([normalized_root, normalized_root])
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)

        cursor = await conn.execute(query, tuple(params))
        rows = await cursor.fetchall()
        items = await self._rows_to_items(rows)

        grouped_items: dict[tuple[int, str], list[MemoryItem]] = {}
        for item in items:
            normalized_source_path = (item.source_path or "").replace("\\", "/")
            if normalized_module_path in normalized_source_path:
                matched_prefix = normalized_source_path.split(normalized_module_path, 1)[0]
                group_key = (0, matched_prefix.rstrip("/"))
            elif normalized_source_path:
                group_key = (
                    1,
                    normalized_source_path.rsplit("/", 1)[0]
                    if "/" in normalized_source_path
                    else normalized_source_path,
                )
            else:
                group_key = (2, "")
            grouped_items.setdefault(group_key, []).append(item)

        ordered_items: list[MemoryItem] = []
        for group_key in sorted(grouped_items):
            ordered_items.extend(grouped_items[group_key])
        return ordered_items

    async def list_unused_memories(
        self,
        days_threshold: int = 30,
        *,
        workspace_root: str | None = None,
        limit: int = 100,
    ) -> list[MemoryItem]:
        conn = await self._db.connect()
        normalized_root = self._normalize_workspace_root(workspace_root)
        query = """
            SELECT
                id, type, title, body, source, source_path, trust_level,
                stale, tags_json, COALESCE(memory_class, 'fact') AS memory_class,
                COALESCE(memory_status, 'discovered') AS memory_status,
                COALESCE(visibility_scope, 'workspace') AS visibility_scope,
                reusable, review_required, created_at, updated_at
            FROM memory_items
            WHERE (last_used_at IS NULL OR last_used_at <= datetime('now', '-' || ? || ' days'))
        """
        params: list[Any] = [days_threshold]
        if normalized_root is not None:
            query += " AND COALESCE(workspace_root, ?) = ?"
            params.extend([normalized_root, normalized_root])
        query += " ORDER BY COALESCE(last_used_at, created_at) ASC LIMIT ?"
        params.append(limit)
        cursor = await conn.execute(query, tuple(params))
        rows = await cursor.fetchall()
        return await self._rows_to_items(rows)

    async def get_usage_stats(self, memory_id: str) -> dict[str, Any]:
        conn = await self._db.connect()
        cursor = await conn.execute(
            "SELECT last_used_at, COALESCE(usage_count, 0) AS usage_count FROM memory_items WHERE id = ?",
            (memory_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError(f"Memory item not found: {memory_id}")

        last_used_at = str(row["last_used_at"]) if row["last_used_at"] is not None else None
        used_count = int(row["usage_count"] or 0)

        cursor = await conn.execute(
            """
            SELECT COUNT(*) AS recalled_count
            FROM recall_traces AS trace
            JOIN json_each(COALESCE(trace.included_memory_ids_json, '[]')) AS included
              ON 1 = 1
            WHERE included.value = ?
            """,
            (memory_id,),
        )
        recall_row = await cursor.fetchone()
        recalled_count = int(recall_row["recalled_count"] if recall_row is not None else 0)

        if await self._table_exists(conn, "memory_usage_events"):
            columns = await self._table_columns(conn, "memory_usage_events")
            if {"memory_id", "event_type"}.issubset(columns):
                timestamp_column = "created_at" if "created_at" in columns else None
                last_used_expression = (
                    f"MAX(CASE WHEN lower(event_type) IN ('used', 'applied', 'accepted') THEN {timestamp_column} END)"
                    if timestamp_column is not None
                    else "NULL"
                )
                cursor = await conn.execute(
                    f"""
                    SELECT
                        COALESCE(SUM(CASE WHEN lower(event_type) = 'recalled' THEN 1 ELSE 0 END), 0) AS recalled_count,
                        COALESCE(SUM(CASE WHEN lower(event_type) IN ('used', 'applied', 'accepted') THEN 1 ELSE 0 END), 0) AS used_count,
                        {last_used_expression} AS last_used_at
                    FROM memory_usage_events
                    WHERE memory_id = ?
                    """,
                    (memory_id,),
                )
                usage_row = await cursor.fetchone()
                if usage_row is not None:
                    recalled_count = max(recalled_count, int(usage_row["recalled_count"] or 0))
                    used_count = max(used_count, int(usage_row["used_count"] or 0))
                    if usage_row["last_used_at"] is not None:
                        event_last_used = str(usage_row["last_used_at"])
                        if last_used_at is None or event_last_used > last_used_at:
                            last_used_at = event_last_used

        return {
            "recalled_count": recalled_count,
            "used_count": used_count,
            "last_used_at": last_used_at,
            "days_since_last_use": self._days_since(last_used_at),
        }

    async def review_item(
        self,
        item_id: str,
        *,
        decision: str,
        workspace_root: str | None = None,
    ) -> None:
        if decision == "approve":
            await self.approve_item(item_id, workspace_root=workspace_root)
            return
        if decision == "reject":
            await self.reject_item(item_id, workspace_root=workspace_root)
            return
        raise ValueError(f"Unsupported review decision: {decision}")

    async def _fetch_item_rows(
        self,
        item_ids: list[str],
        *,
        workspace_root: str | None = None,
    ):
        if not item_ids:
            return []
        conn = await self._db.connect()
        normalized_root = self._normalize_workspace_root(workspace_root)
        placeholders = ", ".join("?" for _ in item_ids)
        cursor = await conn.execute(
            f"""
            SELECT id, tags_json, COALESCE(memory_status, 'discovered') AS memory_status
            FROM memory_items
            WHERE id IN ({placeholders})
              AND (? IS NULL OR COALESCE(workspace_root, ?) = ?)
            """,
            (*item_ids, normalized_root, normalized_root, normalized_root),
        )
        return await cursor.fetchall()

    async def _fetch_item_row(self, item_id: str, *, workspace_root: str | None = None):
        rows = await self._fetch_item_rows([item_id], workspace_root=workspace_root)
        if not rows:
            raise ValueError(f"Memory item not found: {item_id}")
        return rows[0]

    async def _require_item(self, item_id: str, *, workspace_root: str | None = None) -> None:
        await self._fetch_item_row(item_id, workspace_root=workspace_root)

    def _parse_tags(self, raw: str | None) -> dict[str, Any] | list[Any] | None:
        if raw is None:
            return {}
        parsed = json.loads(raw)
        if isinstance(parsed, (dict, list)):
            return parsed
        return {}

    async def _rows_to_items(self, rows, *, include_usage_stats: bool = True) -> list[MemoryItem]:
        items: list[MemoryItem] = []
        if include_usage_stats and rows:
            batch_stats = await self._batch_usage_stats([str(row["id"]) for row in rows])
        else:
            batch_stats = {}
        for row in rows:
            items.append(
                self._row_to_item(
                    row,
                    usage_stats=batch_stats.get(str(row["id"])),
                )
            )
        return items

    async def _batch_usage_stats(self, memory_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch usage stats for multiple memory items in batched queries."""
        if not memory_ids:
            return {}
        conn = await self._db.connect()
        placeholders = ", ".join("?" for _ in memory_ids)

        # Base stats from memory_items
        cursor = await conn.execute(
            f"SELECT id, last_used_at, COALESCE(usage_count, 0) AS usage_count "
            f"FROM memory_items WHERE id IN ({placeholders})",
            tuple(memory_ids),
        )
        rows = await cursor.fetchall()
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            mid = str(row["id"])
            last_used_at = str(row["last_used_at"]) if row["last_used_at"] is not None else None
            result[mid] = {
                "recalled_count": int(row["usage_count"] or 0),
                "used_count": int(row["usage_count"] or 0),
                "last_used_at": last_used_at,
                "days_since_last_use": self._days_since(last_used_at),
            }

        # Enrich from memory_usage_events if available
        if await self._table_exists(conn, "memory_usage_events"):
            columns = await self._table_columns(conn, "memory_usage_events")
            if {"memory_id", "event_type"}.issubset(columns):
                timestamp_col = "created_at" if "created_at" in columns else None
                last_used_expr = (
                    f"MAX(CASE WHEN lower(event_type) IN ('used', 'applied', 'accepted') THEN {timestamp_col} END)"
                    if timestamp_col
                    else "NULL"
                )
                cursor = await conn.execute(
                    f"""
                    SELECT
                        memory_id,
                        COALESCE(SUM(CASE WHEN lower(event_type) = 'recalled' THEN 1 ELSE 0 END), 0) AS recalled_count,
                        COALESCE(SUM(CASE WHEN lower(event_type) IN ('used', 'applied', 'accepted') THEN 1 ELSE 0 END), 0) AS used_count,
                        {last_used_expr} AS last_used_at
                    FROM memory_usage_events
                    WHERE memory_id IN ({placeholders})
                    GROUP BY memory_id
                    """,
                    tuple(memory_ids),
                )
                event_rows = await cursor.fetchall()
                for erow in event_rows:
                    mid = str(erow["memory_id"])
                    if mid not in result:
                        continue
                    result[mid]["recalled_count"] = max(
                        result[mid]["recalled_count"], int(erow["recalled_count"] or 0)
                    )
                    result[mid]["used_count"] = max(
                        result[mid]["used_count"], int(erow["used_count"] or 0)
                    )
                    if erow["last_used_at"] is not None:
                        event_last = str(erow["last_used_at"])
                        current_last = result[mid]["last_used_at"]
                        if current_last is None or event_last > current_last:
                            result[mid]["last_used_at"] = event_last
                            result[mid]["days_since_last_use"] = self._days_since(event_last)

        return result

    def _row_to_item(self, row, *, usage_stats: dict[str, Any] | None = None) -> MemoryItem:
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
            memory_class=str(row["memory_class"]),
            memory_status=str(row["memory_status"]),
            visibility_scope=str(row["visibility_scope"]),
            reusable=bool(row["reusable"]),
            review_required=bool(row["review_required"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            usage_stats=usage_stats,
        )

    def _get_blocked_reason(self, *, title: str, body: str) -> str | None:
        combined = f"{title}\n{body}"
        if len(combined.encode("utf-8")) > MAX_MEMORY_CONTENT_BYTES:
            return f"memory content exceeds {MAX_MEMORY_CONTENT_BYTES} byte limit"
        is_safe, blocked_reason = check_writeback_safety(combined)
        if not is_safe:
            return blocked_reason
        return None

    async def _store_blocked_artifact(
        self,
        *,
        conn,
        title: str,
        body: str,
        blocked_reason: str,
        task_run_id: str | None,
        workspace_root: str | None,
    ) -> str:
        if self._config is None:
            raise RuntimeError("config is required to persist blocked memory artifacts")

        artifact_id = uuid.uuid4().hex
        content = json.dumps(
            {
                "title": title,
                "body": body,
                "blocked_reason": blocked_reason,
                "created_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        )
        snapshots_dir = self._config.memopilot_dir / "memory" / "snapshots"
        snapshots_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = snapshots_dir / f"blocked-memory-{artifact_id}.json"
        artifact_path.write_text(content, encoding="utf-8")
        artifact_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        effective_task_run_id = task_run_id or await self._ensure_system_task_run(
            conn=conn,
            title=title,
            blocked_reason=blocked_reason,
            workspace_root=workspace_root,
        )
        artifact_type = self._artifact_type_for_blocked_reason(blocked_reason)
        await conn.execute(
            """
            INSERT INTO memory_artifacts
            (
                id, task_run_id, artifact_type, artifact_path, artifact_hash,
                size_bytes, blocked_reason, redacted
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                artifact_id,
                effective_task_run_id,
                artifact_type,
                str(artifact_path),
                artifact_hash,
                len(content.encode("utf-8")),
                blocked_reason,
            ),
        )
        return artifact_id

    def _artifact_type_for_blocked_reason(self, blocked_reason: str) -> str:
        if "diff" in blocked_reason:
            return "patch_diff"
        if "transcript" in blocked_reason:
            return "raw_transcript"
        return "other"

    async def _update_memory_status(
        self,
        *,
        conn,
        item_id: str,
        current_status: str,
        new_status: str,
        tags: dict[str, Any] | list[Any] | None = None,
        trust_level: int | None = None,
        reusable: int | None = None,
        review_required: int | None = None,
        stale: int | None = None,
    ) -> None:
        if not validate_status_transition(current_status, new_status):
            raise ValueError(f"Invalid memory status transition: {current_status} -> {new_status}")

        assignments = ["memory_status = ?", "updated_at = datetime('now')"]
        values: list[Any] = [new_status]

        if tags is not None:
            assignments.append("tags_json = ?")
            values.append(json.dumps(tags))
        if trust_level is not None:
            assignments.append("trust_level = ?")
            values.append(trust_level)
        if reusable is not None:
            assignments.append("reusable = ?")
            values.append(reusable)
        if review_required is not None:
            assignments.append("review_required = ?")
            values.append(review_required)
        if stale is not None:
            assignments.append("stale = ?")
            values.append(stale)

        values.append(item_id)
        await conn.execute(
            f"UPDATE memory_items SET {', '.join(assignments)} WHERE id = ?",
            tuple(values),
        )

    async def _ensure_system_task_run(
        self,
        *,
        conn,
        title: str,
        blocked_reason: str,
        workspace_root: str | None,
    ) -> str:
        task_run_id = uuid.uuid4().hex
        await conn.execute(
            """
            INSERT INTO task_runs
            (
                id, user_request, task_type, mode, risk_level,
                selected_model, estimated_cost, actual_cost, status,
                workspace_root
            )
            VALUES (
                ?, ?, 'memory_writeback', 'safety_filter', 'medium', NULL,
                NULL, NULL, 'success', ?
            )
            """,
            (
                task_run_id,
                f"Blocked memory write-back for '{title}': {blocked_reason}",
                workspace_root,
            ),
        )
        return task_run_id

    # ------------------------------------------------------------------
    # Backup / Restore (v1.5)
    # ------------------------------------------------------------------

    async def backup_memory(self) -> BackupResult:
        if self._config is None:
            raise RuntimeError("config is required for backup/restore operations")
        conn = await self._db.connect()
        snapshots_dir = self._config.memopilot_dir / "memory" / "snapshots" / "memory-backups"
        backup_dir = await create_backup(conn, self._config.memopilot_dir, snapshots_dir)
        manifest_path = backup_dir / "manifest.json"
        manifest = (
            json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        )
        return BackupResult(
            backup_id=backup_dir.name,
            backup_path=str(backup_dir),
            item_count=int(manifest.get("memory_items_count", 0)),
            created_at=str(manifest.get("created_at") or datetime.now(UTC).isoformat()),
            manifest=manifest,
        )

    async def restore_memory(self, *, backup_path: str) -> int:
        if self._config is None:
            raise RuntimeError("config is required for backup/restore operations")
        source = Path(backup_path)
        if not source.is_absolute():
            source = (self._config.workspace_path / source).resolve()
        if source.is_file() and source.name == "manifest.json":
            source = source.parent
        if not source.exists():
            raise ValueError(f"backup path not found: {source}")

        conn = await self._db.connect()
        if await self._connection_is_in_memory(conn):
            restored = await restore_backup(source, self._config.memopilot_dir, conn=conn)
        else:
            await self._db.close()
            restored = await restore_backup(source, self._config.memopilot_dir)
            await self._db.connect()
        if not restored:
            raise ValueError("backup restore failed")

        refreshed_conn = await self._db.connect()
        cursor = await refreshed_conn.execute("SELECT COUNT(*) FROM memory_items")
        row = await cursor.fetchone()
        return int(row[0] if row is not None else 0)

    async def _connection_is_in_memory(self, conn) -> bool:
        cursor = await conn.execute("PRAGMA database_list")
        rows = await cursor.fetchall()
        for row in rows:
            if row[1] == "main":
                location = str(row[2] or "").strip()
                return location in {"", ":memory:"}
        return False

    async def _table_exists(self, conn, table_name: str) -> bool:
        cursor = await conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        )
        return await cursor.fetchone() is not None

    async def _table_columns(self, conn, table_name: str) -> set[str]:
        cursor = await conn.execute(f"PRAGMA table_info({table_name})")
        rows = await cursor.fetchall()
        return {str(row[1]) for row in rows}

    def _days_since(self, timestamp: str | None) -> int | None:
        if not timestamp:
            return None
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max((datetime.now(UTC) - parsed.astimezone(UTC)).days, 0)
