"""Tests for GET /v1/rules/active endpoint."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from pathlib import Path


@pytest.mark.asyncio
async def test_rules_active_returns_empty_when_no_rules(client: AsyncClient, test_token: str):
    """With no rules or skills configured, returns empty lists."""
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    response = await client.get("/v1/rules/active", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert "global_rules" in data
    assert "project_rules" in data
    assert "detected_skills" in data
    assert isinstance(data["global_rules"], list)
    assert isinstance(data["project_rules"], list)
    assert isinstance(data["detected_skills"], list)


@pytest.mark.asyncio
async def test_rules_active_includes_policy_pack_rules(
    client: AsyncClient, test_token: str
):
    """Active policy pack rules appear in the response."""
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    # Create a policy pack with rules
    create_response = await client.post(
        "/v1/policies/packs",
        headers=headers,
        json={
            "name": "Project Standards",
            "description": "Project-level coding standards",
            "enforcement_mode": "enforce",
            "rules": [
                "All changes must go through InventoryService.",
                "Run pytest before marking task complete.",
            ],
        },
    )
    assert create_response.status_code == 200
    pack_id = create_response.json()["pack_id"]

    # Activate the pack
    activate_response = await client.post(
        "/v1/policies/packs/activate",
        headers=headers,
        json={"pack_id": pack_id},
    )
    assert activate_response.status_code == 200

    # Now fetch active rules
    response = await client.get("/v1/rules/active", headers=headers)
    assert response.status_code == 200
    data = response.json()

    # Rules should appear in project_rules (since "global" is not in pack name)
    all_rules = data["project_rules"]
    rule_texts = [r["text"] for r in all_rules]
    assert "All changes must go through InventoryService." in rule_texts
    assert "Run pytest before marking task complete." in rule_texts

    # Each rule should have required fields
    for rule in all_rules:
        assert "rule_id" in rule
        assert "text" in rule
        assert "source_file" in rule
        assert "enabled" in rule
        assert rule["enabled"] is True


@pytest.mark.asyncio
async def test_rules_active_includes_skills(client: AsyncClient, test_token: str):
    """Skills from the skill store appear in detected_skills."""
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    # Create a skill
    create_response = await client.post(
        "/v1/skills/store",
        headers=headers,
        json={
            "name": "fastapi",
            "applies_when": "Python FastAPI project detected",
            "rules": ["Use dependency injection"],
            "tools": ["uvicorn"],
        },
    )
    assert create_response.status_code == 200

    # Fetch active rules
    response = await client.get("/v1/rules/active", headers=headers)
    assert response.status_code == 200
    data = response.json()

    skill_names = [s["name"] for s in data["detected_skills"]]
    assert "fastapi" in skill_names

    # Verify skill structure
    fastapi_skill = next(s for s in data["detected_skills"] if s["name"] == "fastapi")
    assert "skill_id" in fastapi_skill
    assert "enabled" in fastapi_skill
    assert fastapi_skill["enabled"] is True


@pytest.mark.asyncio
async def test_rules_active_reads_yaml_rule_files(
    client: AsyncClient, test_token: str, test_config
):
    """Rules from .memopilot/rules/*.yaml files are included."""
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    # Create a rules YAML file
    rules_dir = test_config.memopilot_dir / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)

    rules_file = rules_dir / "project.rules.yaml"
    rules_file.write_text(
        "rules:\n"
        "  - Use service layer for all operations.\n"
        "  - Add tests for every new function.\n",
        encoding="utf-8",
    )

    response = await client.get("/v1/rules/active", headers=headers)
    assert response.status_code == 200
    data = response.json()

    project_rule_texts = [r["text"] for r in data["project_rules"]]
    assert "Use service layer for all operations." in project_rule_texts
    assert "Add tests for every new function." in project_rule_texts


@pytest.mark.asyncio
async def test_rules_active_global_rules_from_file(
    client: AsyncClient, test_token: str, test_config
):
    """Rules in files with 'global' in the name appear in global_rules."""
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    rules_dir = test_config.memopilot_dir / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)

    global_file = rules_dir / "global.rules.yaml"
    global_file.write_text(
        "rules:\n  - Never expose internal database IDs in API responses.\n",
        encoding="utf-8",
    )

    response = await client.get("/v1/rules/active", headers=headers)
    assert response.status_code == 200
    data = response.json()

    global_rule_texts = [r["text"] for r in data["global_rules"]]
    assert "Never expose internal database IDs in API responses." in global_rule_texts
