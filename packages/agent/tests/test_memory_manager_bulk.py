"""Tests for memory manager bulk actions, usage stats, and ranking helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from agent.db import DatabaseManager
from agent.memory_suggestions import MemorySuggestion, get_decay_status, rank_memory_suggestions


async def _insert_memory_item(
    test_db: DatabaseManager,
    *,
    item_id: str,
    title: str,
    memory_status: str = "pending_review",
    review_required: int = 1,
    reusable: int = 0,
    trust_level: int = 4,
    last_used_at: str | None = None,
    usage_count: int = 0,
) -> None:
    conn = test_db.connection
    assert conn is not None
    await conn.execute(
        """
        INSERT INTO memory_items (
            id, type, title, body, source, source_hash, trust_level, tags_json, stale,
            memory_class, memory_status, visibility_scope, reusable, review_required,
            last_used_at, usage_count
        )
        VALUES (?, 'note', ?, 'Body', 'project', NULL, ?, '{"pending_approval": true}', 0,
                'fact', ?, 'workspace', ?, ?, ?, ?)
        """,
        (
            item_id,
            title,
            trust_level,
            memory_status,
            reusable,
            review_required,
            last_used_at,
            usage_count,
        ),
    )
    await conn.commit()


@pytest.mark.asyncio
async def test_bulk_approve_transitions_to_confirmed(
    client: AsyncClient,
    test_token: str,
    test_db: DatabaseManager,
):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    for index in range(10):
        await _insert_memory_item(test_db, item_id=f"bulk-approve-{index}", title=f"Item {index}")

    response = await client.post(
        "/v1/memory/bulk-approve",
        headers=headers,
        json={"memory_ids": [f"bulk-approve-{index}" for index in range(10)]},
    )
    assert response.status_code == 200

    conn = test_db.connection
    assert conn is not None
    cursor = await conn.execute(
        "SELECT COUNT(*) AS total FROM memory_items WHERE id LIKE 'bulk-approve-%' AND memory_status = 'confirmed'"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["total"] == 10


@pytest.mark.asyncio
async def test_bulk_approve_requires_existing_items(
    client: AsyncClient,
    test_token: str,
):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    response = await client.post(
        "/v1/memory/bulk-approve",
        headers=headers,
        json={"memory_ids": ["missing-1", "missing-2"]},
    )
    assert response.status_code == 200
    assert response.json() == {"success": True}


@pytest.mark.asyncio
async def test_usage_stats_returned(
    client: AsyncClient,
    test_token: str,
    test_db: DatabaseManager,
):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    last_used_at = (datetime.now(UTC) - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    await _insert_memory_item(
        test_db,
        item_id="usage-item",
        title="Usage Item",
        memory_status="confirmed",
        review_required=0,
        reusable=1,
        trust_level=3,
        last_used_at=last_used_at,
        usage_count=1,
    )

    conn = test_db.connection
    assert conn is not None
    await conn.execute(
        """
        CREATE TABLE memory_usage_events (
            id TEXT PRIMARY KEY,
            memory_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    await conn.execute(
        "INSERT INTO memory_usage_events (id, memory_id, event_type, created_at) VALUES (?, ?, ?, datetime('now', '-3 days'))",
        ("evt-1", "usage-item", "recalled"),
    )
    await conn.execute(
        "INSERT INTO memory_usage_events (id, memory_id, event_type, created_at) VALUES (?, ?, ?, datetime('now', '-2 days'))",
        ("evt-2", "usage-item", "recalled"),
    )
    await conn.execute(
        "INSERT INTO memory_usage_events (id, memory_id, event_type, created_at) VALUES (?, ?, ?, datetime('now', '-2 days'))",
        ("evt-3", "usage-item", "used"),
    )
    await conn.commit()

    response = await client.get("/v1/memory/items", headers=headers)
    assert response.status_code == 200
    item = next(entry for entry in response.json()["items"] if entry["id"] == "usage-item")
    assert item["usage_stats"]["recalled_count"] == 2
    assert item["usage_stats"]["used_count"] == 1
    assert item["usage_stats"]["last_used_at"] is not None
    assert item["usage_stats"]["days_since_last_use"] >= 1


@pytest.mark.asyncio
async def test_unused_filter_returns_correct_items(
    client: AsyncClient,
    test_token: str,
    test_db: DatabaseManager,
):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    await _insert_memory_item(
        test_db,
        item_id="unused-null",
        title="Never used",
        memory_status="confirmed",
        review_required=0,
        reusable=1,
        trust_level=3,
    )
    await _insert_memory_item(
        test_db,
        item_id="unused-old",
        title="Used long ago",
        memory_status="confirmed",
        review_required=0,
        reusable=1,
        trust_level=3,
        last_used_at=(datetime.now(UTC) - timedelta(days=35)).strftime("%Y-%m-%d %H:%M:%S"),
    )
    await _insert_memory_item(
        test_db,
        item_id="unused-recent",
        title="Used recently",
        memory_status="confirmed",
        review_required=0,
        reusable=1,
        trust_level=3,
        last_used_at=(datetime.now(UTC) - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S"),
    )

    response = await client.get("/v1/memory/unused", headers=headers)
    assert response.status_code == 200
    returned_ids = {item["id"] for item in response.json()["items"]}
    assert {"unused-null", "unused-old"}.issubset(returned_ids)
    assert "unused-recent" not in returned_ids


def test_ranking_contradicting_suggestion_first():
    suggestions = [
        MemorySuggestion(
            id="contradiction",
            title="Contradiction",
            body="New evidence conflicts with an older memory.",
            source_path="agent\\memory.py",
            memory_class="decision",
            derived_from="validation_result",
            contradicts_memory_id="old-memory",
        ),
        MemorySuggestion(
            id="normal",
            title="Normal",
            body="Helpful note.",
            source_path="agent\\other.py",
            memory_class="fact",
            derived_from="manual",
            contradicts_memory_id=None,
        ),
    ]

    ranked = rank_memory_suggestions(
        suggestions,
        file_changed_checker=lambda path: path == "agent\\memory.py",
        module_task_frequency_getter=lambda path: 0.1 if path == "agent\\memory.py" else 0.0,
    )

    assert ranked[0].id == "contradiction"
    assert ranked[0].rank_score > ranked[1].rank_score


def test_decayed_items_identified():
    created_at = (datetime.now(UTC) - timedelta(days=15)).isoformat()
    assert get_decay_status("pending_review", created_at, True) is True


@pytest.mark.asyncio
async def test_bulk_delete_removes_items(
    client: AsyncClient,
    test_token: str,
    test_db: DatabaseManager,
):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    await _insert_memory_item(test_db, item_id="delete-1", title="Delete 1")
    await _insert_memory_item(test_db, item_id="delete-2", title="Delete 2")

    response = await client.post(
        "/v1/memory/bulk-delete",
        headers=headers,
        json={"memory_ids": ["delete-1", "delete-2"]},
    )
    assert response.status_code == 200

    listed = await client.get("/v1/memory/items", headers=headers)
    assert listed.status_code == 200
    returned_ids = {item["id"] for item in listed.json()["items"]}
    assert "delete-1" not in returned_ids
    assert "delete-2" not in returned_ids
