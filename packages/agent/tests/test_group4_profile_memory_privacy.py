"""Tests for Group 4 workspace profile, memory manager, and privacy dashboard."""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient

from agent.db import DatabaseManager


@pytest.mark.asyncio
async def test_workspace_profile_generated_on_index(
    client: AsyncClient,
    test_token: str,
    tmp_workspace,
):
    headers = {"X-Agent-Token": test_token}
    (tmp_workspace / "service.py").write_text("def run() -> None:\n    pass\n", encoding="utf-8")

    await client.post("/v1/workspace/init", headers=headers)
    indexed = await client.post("/v1/workspace/index", headers=headers)
    assert indexed.status_code == 200

    profile = await client.get("/v1/workspace/profile", headers=headers)
    assert profile.status_code == 200
    assert "primary_language: python" in profile.json()["profile_yaml"]

    validation = await client.get("/v1/workspace/profile/validate", headers=headers)
    assert validation.status_code == 200
    assert validation.json()["valid"] is True


@pytest.mark.asyncio
async def test_memory_manager_filters_and_actions(
    client: AsyncClient,
    test_token: str,
    test_db: DatabaseManager,
):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    conn = test_db.connection
    assert conn is not None
    await conn.execute(
        """
        INSERT INTO memory_items
        (id, type, title, body, source, source_hash, trust_level, tags_json, stale)
        VALUES
        ('rule-1', 'rule', 'Rule One', 'Body', 'project', NULL, 3, '{}', 0),
        ('symbol-1', 'symbol', 'Symbol One', 'Body', 'indexer', NULL, 1, '{}', 0)
        """
    )
    await conn.commit()

    suggestion = await client.post(
        "/v1/memory/suggestions",
        headers=headers,
        json={"title": "AI suggestion", "body": "summary"},
    )
    assert suggestion.status_code == 200
    suggestion_id = suggestion.json()["memory_item_id"]

    symbols = await client.get("/v1/memory/items?filter_name=symbols", headers=headers)
    assert symbols.status_code == 200
    assert len(symbols.json()["items"]) == 1
    assert symbols.json()["items"][0]["id"] == "symbol-1"

    pending = await client.get("/v1/memory/items?filter_name=pending_approval", headers=headers)
    assert pending.status_code == 200
    assert any(item["id"] == suggestion_id for item in pending.json()["items"])

    approved = await client.post(f"/v1/memory/items/{suggestion_id}/approve", headers=headers)
    assert approved.status_code == 200

    edited = await client.put(
        "/v1/memory/items/rule-1",
        headers=headers,
        json={"title": "Rule One Updated", "body": "Updated Body"},
    )
    assert edited.status_code == 200

    deleted = await client.delete("/v1/memory/items/symbol-1", headers=headers)
    assert deleted.status_code == 200


@pytest.mark.asyncio
async def test_privacy_dashboard_shows_recent_cloud_calls(
    client: AsyncClient,
    test_token: str,
):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    task = await client.post(
        "/v1/task-runs/start",
        headers=headers,
        json={"user_request": "Check privacy dashboard", "selected_model": "gpt-4o-mini"},
    )
    assert task.status_code == 200
    task_run_id = task.json()["task_run_id"]

    usage = await client.post(
        "/v1/cost/usage/record",
        headers=headers,
        json={
            "task_run_id": task_run_id,
            "provider": "openai",
            "model": "gpt-4o-mini",
            "input_tokens": 120,
            "output_tokens": 40,
            "estimated_cost": 0.03,
            "actual_cost": 0.03,
            "cache_hit": False,
            "purpose": "chat",
        },
    )
    assert usage.status_code == 200

    dashboard = await client.get("/v1/privacy/dashboard", headers=headers)
    assert dashboard.status_code == 200
    payload = dashboard.json()
    assert "code index" in payload["local_only"]
    assert "context pack sent to cloud provider" in payload["may_leave_machine"]
    assert payload["recent_cloud_calls"][0]["provider"] == "openai"


@pytest.mark.asyncio
async def test_suggested_memory_is_pending_approval(
    client: AsyncClient,
    test_token: str,
    test_db: DatabaseManager,
):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    suggestion = await client.post(
        "/v1/memory/suggestions",
        headers=headers,
        json={"title": "Generated summary", "body": "pending item"},
    )
    assert suggestion.status_code == 200
    item_id = suggestion.json()["memory_item_id"]

    conn = test_db.connection
    assert conn is not None
    cursor = await conn.execute(
        "SELECT trust_level, tags_json FROM memory_items WHERE id = ?",
        (item_id,),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["trust_level"] == 4
    tags = json.loads(row["tags_json"])
    assert tags["pending_approval"] is True
