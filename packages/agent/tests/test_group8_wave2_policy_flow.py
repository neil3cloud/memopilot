"""Tests for Wave 2 policy packs and local flow builder."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_policy_pack_blocking_and_advisory_modes(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    strict = await client.post(
        "/v1/policies/packs",
        headers=headers,
        json={
            "name": "strict-model-policy",
            "description": "Block expensive models and enforce tests in patches",
            "enforcement_mode": "enforce",
            "rules": ["deny_model: gpt-4o, opus", "require_test_file"],
        },
    )
    assert strict.status_code == 200
    strict_body = strict.json()

    activate_strict = await client.post(
        "/v1/policies/packs/activate",
        headers=headers,
        json={"pack_id": strict_body["pack_id"]},
    )
    assert activate_strict.status_code == 200

    eval_model = await client.post(
        "/v1/policies/evaluate",
        headers=headers,
        json={
            "stage": "model_call",
            "task_text": "investigate auth failure",
            "selected_model": "gpt-4o",
        },
    )
    assert eval_model.status_code == 200
    model_body = eval_model.json()
    assert model_body["allowed"] is False
    assert model_body["decision"] == "block"
    assert len(model_body["violations"]) >= 1

    eval_patch = await client.post(
        "/v1/policies/evaluate",
        headers=headers,
        json={
            "stage": "patch_execution",
            "task_text": "fix auth failure",
            "files_changed": ["src/auth/service.py"],
        },
    )
    assert eval_patch.status_code == 200
    patch_body = eval_patch.json()
    assert patch_body["allowed"] is False
    assert patch_body["decision"] == "block"

    advisory = await client.post(
        "/v1/policies/packs",
        headers=headers,
        json={
            "name": "advisory-model-policy",
            "description": "Warn only for frontier model usage",
            "enforcement_mode": "advisory",
            "rules": ["deny_model: gpt-4o"],
        },
    )
    assert advisory.status_code == 200
    advisory_body = advisory.json()

    activate_advisory = await client.post(
        "/v1/policies/packs/activate",
        headers=headers,
        json={"pack_id": advisory_body["pack_id"]},
    )
    assert activate_advisory.status_code == 200

    eval_warn = await client.post(
        "/v1/policies/evaluate",
        headers=headers,
        json={
            "stage": "model_call",
            "task_text": "investigate auth failure",
            "selected_model": "gpt-4o",
        },
    )
    assert eval_warn.status_code == 200
    warn_body = eval_warn.json()
    assert warn_body["allowed"] is True
    assert warn_body["decision"] == "warn"


@pytest.mark.asyncio
async def test_model_call_blocked_by_active_policy(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    created = await client.post(
        "/v1/policies/packs",
        headers=headers,
        json={
            "name": "start-task-policy",
            "enforcement_mode": "enforce",
            "rules": ["deny_model: gpt-4o"],
        },
    )
    assert created.status_code == 200
    pack_id = created.json()["pack_id"]

    activated = await client.post(
        "/v1/policies/packs/activate",
        headers=headers,
        json={"pack_id": pack_id},
    )
    assert activated.status_code == 200

    blocked = await client.post(
        "/v1/task-runs/start",
        headers=headers,
        json={
            "user_request": "Need a fix plan",
            "selected_model": "gpt-4o",
        },
    )
    assert blocked.status_code == 403


@pytest.mark.asyncio
async def test_local_flow_builder_runs_policy_and_optimizer(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    policy = await client.post(
        "/v1/policies/packs",
        headers=headers,
        json={
            "name": "flow-enforce-policy",
            "enforcement_mode": "enforce",
            "rules": ["deny_model: gpt-4o"],
        },
    )
    assert policy.status_code == 200
    pack_id = policy.json()["pack_id"]
    activate = await client.post(
        "/v1/policies/packs/activate",
        headers=headers,
        json={"pack_id": pack_id},
    )
    assert activate.status_code == 200

    created_flow = await client.post(
        "/v1/flows/local",
        headers=headers,
        json={
            "name": "default-guarded-flow",
            "description": "Policy check and tool recommendation",
            "steps": [
                {
                    "id": "policy-1",
                    "title": "Policy gate",
                    "action": "policy_check",
                    "stage": "model_call",
                },
                {
                    "id": "tools-1",
                    "title": "Tool optimizer",
                    "action": "tool_recommend",
                    "available_tools": ["Investigate", "Patch", "Test"],
                },
                {"id": "approve-1", "title": "Approval gate", "action": "approval_gate"},
            ],
        },
    )
    assert created_flow.status_code == 200
    flow_id = created_flow.json()["flow_id"]

    listed = await client.get("/v1/flows/local", headers=headers)
    assert listed.status_code == 200
    assert any(item["flow_id"] == flow_id for item in listed.json()["items"])

    run = await client.post(
        "/v1/flows/local/run",
        headers=headers,
        json={
            "flow_id": flow_id,
            "task_text": "Investigate failing login tests",
            "selected_model": "gpt-4o",
        },
    )
    assert run.status_code == 200
    payload = run.json()
    assert payload["status"] == "blocked"
    assert payload["blocked_reason"] is not None
    assert any(step["action"] == "policy_check" for step in payload["steps"])
