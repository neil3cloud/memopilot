"""Tests for POST /v1/task/generate-patch and POST /v1/task/validate endpoints."""
from __future__ import annotations

import sys

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_generate_patch_basic(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    resp = await client.post(
        "/v1/task/generate-patch",
        headers=headers,
        json={
            "task_description": "Add input validation to user service",
            "context_files": ["src/user_service.py"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_files_changed"] == 1
    assert len(data["patches"]) == 1
    assert data["patches"][0]["path"] == "src/user_service.py"
    assert data["patches"][0]["action"] == "modify"
    assert "diff" in data["patches"][0]
    assert data["summary"]
    assert data["model_used"]
    assert data["tokens_used"] > 0
    assert data["cost_usd"] >= 0


@pytest.mark.asyncio
async def test_generate_patch_empty_description_fails(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    resp = await client.post(
        "/v1/task/generate-patch",
        headers=headers,
        json={"task_description": "   ", "context_files": []},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_generate_patch_no_context_creates_file(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    resp = await client.post(
        "/v1/task/generate-patch",
        headers=headers,
        json={"task_description": "Create a new utility module"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_files_changed"] == 1
    assert data["patches"][0]["action"] == "create"


@pytest.mark.asyncio
async def test_generate_patch_multiple_files(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    resp = await client.post(
        "/v1/task/generate-patch",
        headers=headers,
        json={
            "task_description": "Refactor auth across services",
            "context_files": ["auth.py", "service_a.py", "service_b.py"],
            "mode": "refactor",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_files_changed"] == 3
    # Refactor with 3 files → medium risk
    assert data["estimated_risk"] in ("medium", "high")


@pytest.mark.asyncio
async def test_generate_patch_deterministic(client: AsyncClient, test_token: str):
    """Same input produces same output (mock is deterministic)."""
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    payload = {"task_description": "Fix bug in parser", "context_files": ["parser.py"]}
    resp1 = await client.post("/v1/task/generate-patch", headers=headers, json=payload)
    resp2 = await client.post("/v1/task/generate-patch", headers=headers, json=payload)
    assert resp1.json() == resp2.json()


@pytest.mark.asyncio
async def test_validate_all_pass(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    resp = await client.post(
        "/v1/task/validate",
        headers=headers,
        json={
            "patches": [{"path": "src/app.py", "action": "modify", "diff": "+x=1"}],
            "checks": ["syntax", "lint"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["overall_status"] == "pass"
    assert data["can_apply"] is True
    assert len(data["checks"]) == 2


@pytest.mark.asyncio
async def test_validate_test_impact_warning(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    resp = await client.post(
        "/v1/task/validate",
        headers=headers,
        json={
            "patches": [{"path": "tests/test_auth.py", "action": "modify", "diff": "+assert True"}],
            "checks": ["test_impact"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["overall_status"] == "warn"
    assert data["can_apply"] is True


@pytest.mark.asyncio
async def test_validate_unknown_check_skipped(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    resp = await client.post(
        "/v1/task/validate",
        headers=headers,
        json={"patches": [], "checks": ["unknown_check"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["checks"][0]["status"] == "skipped"


@pytest.mark.asyncio
async def test_validate_command_timeout_fails(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    resp = await client.post(
        "/v1/task/validate",
        headers=headers,
        json={
            "checks": [],
            "commands": [
                {
                    "name": "Slow Check",
                    "command": [sys.executable, "-c", "import time; time.sleep(2)"],
                    "timeout": 1,
                }
            ],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["overall_status"] == "fail"
    assert data["can_apply"] is False
    assert data["checks"][0]["status"] == "timeout"
