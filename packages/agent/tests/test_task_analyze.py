"""Tests for POST /v1/task/analyze endpoint."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_task_analyze_returns_intent(client: AsyncClient, test_token: str):
    """Basic task analysis returns intent summary and mode."""
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    response = await client.post(
        "/v1/task/analyze",
        headers=headers,
        json={
            "description": "Add validation so expired items cannot be sold",
            "constraints": ["follow_all_rules", "run_tests"],
            "mode": None,
            "notes": "Expiration date is in inventory/item.expiry_date",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "intent_summary" in data
    assert "suggested_files" in data
    assert "applicable_rules" in data
    assert "estimated_complexity" in data
    assert "suggested_mode" in data
    assert len(data["intent_summary"]) > 0


@pytest.mark.asyncio
async def test_task_analyze_detects_fix_mode(client: AsyncClient, test_token: str):
    """Mode auto-detection identifies 'fix' from keywords."""
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    response = await client.post(
        "/v1/task/analyze",
        headers=headers,
        json={"description": "Fix the bug where users get a 500 error on login"},
    )
    assert response.status_code == 200
    assert response.json()["suggested_mode"] == "fix"


@pytest.mark.asyncio
async def test_task_analyze_detects_test_mode(client: AsyncClient, test_token: str):
    """Mode auto-detection identifies 'test' from keywords."""
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    response = await client.post(
        "/v1/task/analyze",
        headers=headers,
        json={"description": "Write unit tests for the payment service"},
    )
    assert response.status_code == 200
    assert response.json()["suggested_mode"] == "test"


@pytest.mark.asyncio
async def test_task_analyze_respects_explicit_mode(client: AsyncClient, test_token: str):
    """When mode is explicitly provided, it is used as-is."""
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    response = await client.post(
        "/v1/task/analyze",
        headers=headers,
        json={"description": "Update the README", "mode": "document"},
    )
    assert response.status_code == 200
    assert response.json()["suggested_mode"] == "document"


@pytest.mark.asyncio
async def test_task_analyze_empty_description_rejected(client: AsyncClient, test_token: str):
    """Empty description returns 400."""
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    response = await client.post(
        "/v1/task/analyze",
        headers=headers,
        json={"description": "   "},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_task_analyze_includes_constraints_as_rules(client: AsyncClient, test_token: str):
    """run_tests constraint adds a rule to applicable_rules."""
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    response = await client.post(
        "/v1/task/analyze",
        headers=headers,
        json={
            "description": "Refactor the inventory module",
            "constraints": ["run_tests"],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "Run tests after applying changes" in data["applicable_rules"]


@pytest.mark.asyncio
async def test_task_analyze_complexity_estimation(client: AsyncClient, test_token: str):
    """Long descriptions with broad keywords get higher complexity."""
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    # Simple task
    simple = await client.post(
        "/v1/task/analyze",
        headers=headers,
        json={"description": "Rename the function"},
    )
    assert simple.json()["estimated_complexity"] == "low"

    # Complex task
    complex_desc = (
        "Refactor the database schema across multiple services to support "
        "multi-tenancy. Every model needs a tenant_id field and all queries "
        "must be scoped. This involves migration scripts and updating all tests."
    )
    complex_resp = await client.post(
        "/v1/task/analyze",
        headers=headers,
        json={"description": complex_desc},
    )
    assert complex_resp.json()["estimated_complexity"] in ("medium", "high")
