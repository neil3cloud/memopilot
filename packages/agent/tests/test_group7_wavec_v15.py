"""Tests for Wave C v1.5 capabilities."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_skill_store_versioning_and_conflict(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    created = await client.post(
        "/v1/skills/store",
        headers=headers,
        json={
            "name": "python-testing",
            "applies_when": "pytest tests",
            "rules": ["must include tests"],
            "tools": ["Test", "Review"],
        },
    )
    assert created.status_code == 200
    first = created.json()
    assert first["version"] == 1
    assert first["conflict"] is False

    updated = await client.post(
        "/v1/skills/store",
        headers=headers,
        json={
            "name": "python-testing",
            "applies_when": "pytest tests",
            "rules": ["must include tests", "no hardcoded secrets"],
            "tools": ["Patch"],
        },
    )
    assert updated.status_code == 200
    second = updated.json()
    assert second["version"] == 2
    assert second["conflict"] is True

    listed = await client.get("/v1/skills/store", headers=headers)
    assert listed.status_code == 200
    assert any(item["name"] == "python-testing" for item in listed.json()["items"])


@pytest.mark.asyncio
async def test_memory_backup_and_restore(client: AsyncClient, test_token: str, test_db):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    conn = test_db.connection
    assert conn is not None
    await conn.execute(
        """
        INSERT INTO memory_items
        (id, type, title, body, source, source_hash, trust_level, tags_json, stale)
        VALUES ('m1', 'note', 'Original', 'Body', 'seed', NULL, 3, '{}', 0)
        """
    )
    await conn.commit()

    backup = await client.post("/v1/memory/backup", headers=headers)
    assert backup.status_code == 200
    backup_path = backup.json()["backup_path"]

    await conn.execute("DELETE FROM memory_items")
    await conn.commit()

    restored = await client.post(
        "/v1/memory/restore",
        headers=headers,
        json={"backup_path": backup_path},
    )
    assert restored.status_code == 200
    assert restored.json()["restored_count"] == 1

    cursor = await conn.execute("SELECT COUNT(*) AS total FROM memory_items")
    row = await cursor.fetchone()
    assert row is not None
    assert int(row["total"]) == 1


@pytest.mark.asyncio
async def test_optimizer_budget_profiles_and_classifier(
    client: AsyncClient,
    test_token: str,
):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    optimize = await client.post(
        "/v1/optimizer/tools-skills",
        headers=headers,
        json={
            "task_text": "Investigate failing tests and prepare patch review",
            "available_tools": ["Investigate", "Patch", "Test", "Review"],
        },
    )
    assert optimize.status_code == 200
    payload = optimize.json()
    assert "Investigate" in payload["suggested_tools"]
    assert "Test" in payload["suggested_tools"]
    assert len(payload["reasons"]) >= 1

    profiles = await client.get("/v1/budget/profiles", headers=headers)
    assert profiles.status_code == 200
    assert profiles.json()["active_profile"] in {"balanced", "cost_saver", "frontier"}

    set_profile = await client.post(
        "/v1/budget/profiles",
        headers=headers,
        json={"profile": "cost_saver"},
    )
    assert set_profile.status_code == 200
    assert set_profile.json()["active_profile"] == "cost_saver"
    assert set_profile.json()["multiplier"] == 0.7

    classify = await client.post(
        "/v1/investigation/evidence/classify",
        headers=headers,
        json={"source_url": "https://dev.azure.com/org/project/_workitems/edit/1234"},
    )
    assert classify.status_code == 200
    classified = classify.json()
    assert classified["source_type"] == "external_work_item"
    assert classified["trust_level"] == 3
    assert classified["extraction_method"] == "work_item_summary"
