"""Tests for Wave C v1.5 capabilities."""

from __future__ import annotations

from pathlib import Path

import openpyxl
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
        (
            id, type, title, body, source, source_hash, trust_level, tags_json, stale,
            memory_class, memory_status, visibility_scope, reusable, review_required
        )
        VALUES ('m1', 'note', 'Original', 'Body', 'seed', NULL, 3, '{}', 0, 'fact', 'confirmed', 'workspace', 1, 0)
        """
    )
    await conn.commit()

    backup = await client.post("/v1/memory/backup", headers=headers)
    assert backup.status_code == 200
    backup_payload = backup.json()
    backup_path = backup_payload["backup_path"]
    assert backup_payload["manifest"]["memory_items_count"] == 1

    await conn.execute("DELETE FROM memory_items")
    await conn.commit()

    restored = await client.post(
        "/v1/memory/restore",
        headers=headers,
        json={"backup_path": backup_path},
    )
    assert restored.status_code == 200
    assert restored.json()["restored_count"] == 1

    cursor = await conn.execute(
        "SELECT COUNT(*) AS total, MIN(memory_status) AS memory_status, MIN(memory_class) AS memory_class, MIN(reusable) AS reusable FROM memory_items"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert int(row["total"]) == 1
    assert row["memory_status"] == "confirmed"
    assert row["memory_class"] == "fact"
    assert row["reusable"] == 1


@pytest.mark.asyncio
async def test_optimizer_tool_classifier(
    client: AsyncClient,
    test_token: str,
):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    optimize = await client.post(
        "/v1/optimizer/tools-skills",
        headers=headers,
        json={
            "task_text": "Refactor parser tests for AB#1234 and update docs",
            "task_type": "bounded_refactor",
            "available_tools": ["fts_search", "rule_resolver", "pytest", "ruff", "ado_mcp"],
        },
    )
    assert optimize.status_code == 200
    payload = optimize.json()
    assert "fts_search" in payload["suggested_tools"]
    assert "rule_resolver" in payload["suggested_tools"]
    assert "pytest" in payload["suggested_tools"]
    assert "ruff" in payload["suggested_tools"]
    assert "ado_mcp" in payload["suggested_tools"]
    assert payload["reasons_map"]["pytest"].startswith("Required for task_type=")
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


@pytest.mark.asyncio
async def test_context_pack_diff_endpoint_returns_section_changes(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    first = await client.post(
        "/v1/context/versions",
        headers=headers,
        json={
            "context_pack_text": "# Active Rules\n- keep tests\n# Relevant Files\n- a.py\n",
            "token_estimate": 10,
        },
    )
    assert first.status_code == 200

    second = await client.post(
        "/v1/context/versions",
        headers=headers,
        json={
            "context_pack_text": "# Active Rules\n- keep tests\n- add coverage\n# Relevant Files\n- b.py\n",
            "token_estimate": 16,
        },
    )
    assert second.status_code == 200

    diff = await client.get(
        "/v1/context-pack/diff",
        headers=headers,
        params={
            "from_version_id": first.json()["version_id"],
            "to_version_id": second.json()["version_id"],
        },
    )
    assert diff.status_code == 200
    payload = diff.json()
    assert payload["from_version_id"] == first.json()["version_id"]
    assert payload["to_version_id"] == second.json()["version_id"]
    assert payload["added_items"]["Active Rules"] == ["add coverage"]
    assert payload["added_items"]["Relevant Files"] == ["b.py"]
    assert payload["removed_items"]["Relevant Files"] == ["a.py"]
    assert payload["token_delta_estimate"] > 0


@pytest.mark.asyncio
async def test_skill_import_listing_and_conflicts(
    client: AsyncClient,
    test_token: str,
):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    safe_skill = await client.post(
        "/v1/skills/import",
        headers=headers,
        json={
            "yaml_content": (
                "name: Python Type Safety\n"
                "applies_when: \"language == 'python'\"\n"
                "rules:\n"
                "  - Always use type hints on function parameters\n"
                "tools:\n"
                "  - mypy\n"
            )
        },
    )
    assert safe_skill.status_code == 200
    assert safe_skill.json()["source"] == "skill_store"

    conflict_skill = await client.post(
        "/v1/skills/import",
        headers=headers,
        json={
            "yaml_content": (
                "name: Python Rapid Prototyping\n"
                "applies_when: \"language == 'python'\"\n"
                "rules:\n"
                "  - Never use type hints on function parameters\n"
                "tools:\n"
                "  - python\n"
            )
        },
    )
    assert conflict_skill.status_code == 200
    assert conflict_skill.json()["conflict"] is True

    listed = await client.get("/v1/skills", headers=headers)
    assert listed.status_code == 200
    assert {item["name"] for item in listed.json()["items"]} >= {
        "Python Type Safety",
        "Python Rapid Prototyping",
    }

    conflicts = await client.get("/v1/skills/conflicts", headers=headers)
    assert conflicts.status_code == 200
    assert len(conflicts.json()["items"]) == 1
    pair = conflicts.json()["items"][0]
    assert pair["language"] == "python"
    assert any("type hints" in item.lower() for item in pair["contradictory_rules"])


@pytest.mark.asyncio
async def test_document_ingestion_endpoints(
    client: AsyncClient,
    test_token: str,
    tmp_workspace: Path,
):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    csv_path = tmp_workspace / "evidence.csv"
    csv_path.write_text("name;score\nalpha;7\n", encoding="utf-8")

    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "Sheet1"
    worksheet.append(["name", "score"])
    worksheet.append(["alpha", 7])
    excel_path = tmp_workspace / "evidence.xlsx"
    workbook.save(excel_path)
    workbook.close()

    csv_response = await client.post(
        "/v1/evidence/extract-csv",
        headers=headers,
        json={"file_path": str(csv_path)},
    )
    assert csv_response.status_code == 200
    csv_payload = csv_response.json()
    assert csv_payload["source_type"] == "csv_data"
    assert csv_payload["metadata"]["delimiter"] == ";"
    assert "alpha" in csv_payload["chunks"][0]["chunk_text"]

    excel_sheet_list = await client.post(
        "/v1/evidence/extract-excel",
        headers=headers,
        json={"file_path": str(excel_path)},
    )
    assert excel_sheet_list.status_code == 200
    assert excel_sheet_list.json()["metadata"]["available_sheets"] == ["Sheet1"]

    excel_extract = await client.post(
        "/v1/evidence/extract-excel",
        headers=headers,
        json={
            "file_path": str(excel_path),
            "sheet_names": ["Sheet1"],
            "column_mapping": {"score": "points"},
        },
    )
    assert excel_extract.status_code == 200
    assert "points: 7" in excel_extract.json()["chunks"][0]["chunk_text"]
