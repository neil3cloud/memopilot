"""Tests for retention enforcement helpers."""

from __future__ import annotations

import json

import aiosqlite
import pytest

from agent.migration_runner import run_migrations
from agent.retention import truncate_details_json, enforce_retention


@pytest.mark.asyncio
async def test_enforce_retention_trims_old_and_extra_rows():
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = aiosqlite.Row
    await run_migrations(conn)

    await conn.execute("UPDATE retention_config SET max_rows = 2, max_days = 30 WHERE table_name = 'recall_traces'")
    await conn.execute(
        "INSERT INTO recall_traces (id, context_pack_hash, created_at) VALUES (?, ?, ?)",
        ("old", "hash-old", "2000-01-01 00:00:00"),
    )
    await conn.execute(
        "INSERT INTO recall_traces (id, context_pack_hash, created_at) VALUES (?, ?, datetime('now', '-2 days'))",
        ("recent-1", "hash-1"),
    )
    await conn.execute(
        "INSERT INTO recall_traces (id, context_pack_hash, created_at) VALUES (?, ?, datetime('now', '-1 days'))",
        ("recent-2", "hash-2"),
    )
    await conn.execute(
        "INSERT INTO recall_traces (id, context_pack_hash, created_at) VALUES (?, ?, datetime('now'))",
        ("recent-3", "hash-3"),
    )
    await conn.commit()

    results = await enforce_retention(conn)

    assert results["recall_traces"] >= 2
    cursor = await conn.execute("SELECT id FROM recall_traces ORDER BY created_at")
    remaining = [row[0] for row in await cursor.fetchall()]
    assert remaining == ["recent-2", "recent-3"]
    assert results["memory_usage_events"] == 0

    await conn.close()


def test_truncate_details_json_limits_payload_size():
    original = "x" * 5000
    truncated = truncate_details_json(original)

    assert truncated is not None
    payload = json.loads(truncated)
    assert payload["truncated"] is True
    assert payload["original_size_bytes"] > 4096
    assert truncate_details_json('{"ok":true}') == '{"ok":true}'
