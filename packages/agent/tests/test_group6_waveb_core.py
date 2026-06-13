"""Tests for Wave B core production features."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_endpoint_status_and_context_pack_route(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    status = await client.get("/v1/endpoints/status", headers=headers)
    assert status.status_code == 200
    body = status.json()
    assert body["POST /v1/context-pack/generate"] == "real"
    assert body["POST /v1/investigation/{session_id}/run"] == "stub"

    context_pack = await client.post(
        "/v1/context-pack/generate",
        headers=headers,
        json={
            "task_description": "Inspect retry path",
            "suggested_files": [],
        },
    )
    assert context_pack.status_code == 200
    assert "files" in context_pack.json()


@pytest.mark.asyncio
async def test_context_template_lifecycle(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    listed = await client.get("/v1/context/templates", headers=headers)
    assert listed.status_code == 200
    initial_templates = listed.json()["templates"]
    assert len(initial_templates) >= 1

    created = await client.post(
        "/v1/context/templates",
        headers=headers,
        json={
            "name": "risk-review",
            "content": "# Risk Review Template\n\n- Summary",
            "scope": "workspace",
        },
    )
    assert created.status_code == 200
    template_id = created.json()["template_id"]

    selected = await client.post(
        "/v1/context/templates/select",
        headers=headers,
        json={"template_id": template_id},
    )
    assert selected.status_code == 200

    listed_after = await client.get("/v1/context/templates", headers=headers)
    assert listed_after.status_code == 200
    assert any(
        item["template_id"] == template_id and item["selected"] is True
        for item in listed_after.json()["templates"]
    )


@pytest.mark.asyncio
async def test_context_version_diff_and_replay(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    task = await client.post(
        "/v1/task-runs/start",
        headers=headers,
        json={"user_request": "Investigate replay", "selected_model": "gpt-4o-mini"},
    )
    assert task.status_code == 200
    task_run_id = task.json()["task_run_id"]

    first_version = await client.post(
        "/v1/context/versions",
        headers=headers,
        json={
            "task_run_id": task_run_id,
            "context_pack_text": "# v1\n\nalpha",
            "selected_model": "gpt-4o-mini",
        },
    )
    assert first_version.status_code == 200
    first_id = first_version.json()["version_id"]

    second_version = await client.post(
        "/v1/context/versions",
        headers=headers,
        json={
            "task_run_id": task_run_id,
            "context_pack_text": "# v2\n\nalpha\nbeta",
            "selected_model": "gpt-4o-mini",
        },
    )
    assert second_version.status_code == 200
    second_id = second_version.json()["version_id"]

    diff = await client.post(
        "/v1/context/versions/diff",
        headers=headers,
        json={"left_version_id": first_id, "right_version_id": second_id},
    )
    assert diff.status_code == 200
    assert "beta" in diff.json()["diff_text"]

    usage = await client.post(
        "/v1/cost/usage/record",
        headers=headers,
        json={
            "task_run_id": task_run_id,
            "provider": "openai",
            "model": "gpt-4o-mini",
            "input_tokens": 42,
            "output_tokens": 8,
            "estimated_cost": 0.02,
            "actual_cost": 0.02,
            "purpose": "replay-test",
        },
    )
    assert usage.status_code == 200
    ai_call_id = usage.json()["ai_call_id"]

    replay = await client.get(f"/v1/ai/replay/{ai_call_id}", headers=headers)
    assert replay.status_code == 200
    replay_payload = replay.json()
    assert replay_payload["task_run_id"] == task_run_id
    assert replay_payload["provider"] == "openai"
    assert replay_payload["context_pack_text"] != ""


@pytest.mark.asyncio
async def test_patch_assessment_and_provider_capabilities(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    task = await client.post(
        "/v1/task-runs/start",
        headers=headers,
        json={"user_request": "Assess patch", "selected_model": "gpt-4o-mini"},
    )
    assert task.status_code == 200
    task_run_id = task.json()["task_run_id"]

    assessed = await client.post(
        "/v1/patch/assess",
        headers=headers,
        json={
            "task_run_id": task_run_id,
            "diff_text": "DROP TABLE users;\n+ api_key = 'secret'",
            "files_changed": ["db/migration.sql", "app/config.py"],
            "active_rules": ["must include tests", "no hardcoded secrets"],
        },
    )
    assert assessed.status_code == 200
    assessment = assessed.json()
    assert assessment["risk_level"] == "high"
    assert assessment["rule_compliance_score"] < 1.0
    assert "destructive_sql_detected" in assessment["reasons"]

    capabilities = await client.get("/v1/providers/capabilities", headers=headers)
    assert capabilities.status_code == 200
    assert len(capabilities.json()["items"]) >= 1

    upsert = await client.post(
        "/v1/providers/capabilities",
        headers=headers,
        json={
            "model_id": "custom-waveb-model",
            "source": "test",
            "max_context_tokens": 64000,
            "supports_tool_calling": True,
            "supports_json_mode": True,
            "estimated_cost_per_1m_input": 1.0,
            "estimated_cost_per_1m_output": 3.0,
            "privacy_level": "cloud",
            "allowed_task_types": ["plan", "review"],
            "denied_task_types": [],
            "requires_approval": True,
        },
    )
    assert upsert.status_code == 200

    capabilities_after = await client.get("/v1/providers/capabilities", headers=headers)
    assert capabilities_after.status_code == 200
    assert any(
        item["model_id"] == "custom-waveb-model"
        for item in capabilities_after.json()["items"]
    )
