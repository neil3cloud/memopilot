"""Retention policy enforcement for MemoPilot observability tables.

Runs on startup and every 6 hours to enforce row count and age limits
on recall_traces, audit_events, and memory_usage_events.
"""

from __future__ import annotations

import json
import logging
import re

import aiosqlite

logger = logging.getLogger(__name__)

DETAILS_JSON_MAX_BYTES = 4096
_VALID_TABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


async def enforce_retention(conn: aiosqlite.Connection) -> dict[str, int]:
    """Enforce retention policies for all configured tables.

    Returns dict of table_name -> rows_deleted.
    """
    results: dict[str, int] = {}

    if not await _table_exists(conn, "retention_config"):
        return results

    cursor = await conn.execute("SELECT table_name, max_rows, max_days FROM retention_config")
    rows = await cursor.fetchall()

    for row in rows:
        table_name = str(row[0])
        max_rows = int(row[1])
        max_days = int(row[2])
        deleted = 0

        if not _VALID_TABLE_NAME.match(table_name):
            logger.warning("Skipping retention for invalid table name: %s", table_name)
            results[table_name] = 0
            continue

        if not await _table_exists(conn, table_name):
            logger.warning("Retention target table does not exist: %s", table_name)
            results[table_name] = 0
            continue

        try:
            cursor = await conn.execute(
                f"DELETE FROM {table_name} WHERE created_at < datetime('now', '-' || ? || ' days')",
                (max_days,),
            )
            deleted += max(cursor.rowcount, 0)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("Retention age cleanup failed for %s: %s", table_name, exc)

        try:
            cursor = await conn.execute(
                f"DELETE FROM {table_name} WHERE id NOT IN "
                f"(SELECT id FROM {table_name} ORDER BY created_at DESC LIMIT ?)",
                (max_rows,),
            )
            deleted += max(cursor.rowcount, 0)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("Retention row-count cleanup failed for %s: %s", table_name, exc)

        results[table_name] = deleted

    if any(value > 0 for value in results.values()):
        await conn.commit()
        logger.info("Retention enforcement: %s", results)

    return results


async def _table_exists(conn: aiosqlite.Connection, table_name: str) -> bool:
    cursor = await conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    )
    return await cursor.fetchone() is not None


def truncate_details_json(details_json: str | None) -> str | None:
    """Truncate audit event details_json to 4KB limit.

    If content exceeds limit, replace with a summary object.
    """
    if details_json is None:
        return None

    size_bytes = len(details_json.encode("utf-8"))
    if size_bytes <= DETAILS_JSON_MAX_BYTES:
        return details_json

    return json.dumps(
        {
            "truncated": True,
            "original_size_bytes": size_bytes,
            "summary": "Details truncated. Content exceeded 4KB limit.",
        }
    )
