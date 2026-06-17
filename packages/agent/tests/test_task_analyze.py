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


@pytest.mark.asyncio
async def test_task_analyze_prioritizes_test_files_over_sensitive_directories(
    client: AsyncClient,
    test_token: str,
):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    response = await client.post(
        "/v1/task/analyze",
        headers=headers,
        json={
            "description": "Update auth regression coverage",
            "changed_files": [r"src\auth\login_test.py"],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["task_type"] == "test_generation"
    assert data["risk"] == "low"
    assert data["suggested_mode"] == "test"


@pytest.mark.asyncio
async def test_task_analyze_detects_schema_change_from_migration_paths(
    client: AsyncClient,
    test_token: str,
):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    response = await client.post(
        "/v1/task/analyze",
        headers=headers,
        json={
            "description": "Adjust migration for investigation sessions",
            "changed_files": [r"src\billing\migrations\20250101_add_session.py"],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["task_type"] == "schema_change"
    assert data["risk"] == "critical"


@pytest.mark.asyncio
async def test_task_analyze_uses_directory_signals_when_no_filename_match(
    client: AsyncClient,
    test_token: str,
):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    response = await client.post(
        "/v1/task/analyze",
        headers=headers,
        json={
            "description": "Update invoice settlement flow",
            "changed_files": [r"src\billing\invoice\processor.py"],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["task_type"] == "billing_change"
    assert data["risk"] == "high"


@pytest.mark.asyncio
async def test_task_analyze_falls_back_to_indexed_files_when_memory_has_no_match(
    client: AsyncClient,
    test_token: str,
    tmp_workspace,
):
    (tmp_workspace / "memory_indexing_service.py").write_text(
        "def memory_indexing_service() -> None:\n    pass\n",
        encoding="utf-8",
    )

    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)
    index_response = await client.post("/v1/workspace/index", headers=headers)
    assert index_response.status_code == 200

    response = await client.post(
        "/v1/task/analyze",
        headers=headers,
        json={"description": "Improve memory indexing service"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "memory_indexing_service.py" in data["suggested_files"]


@pytest.mark.asyncio
async def test_task_analyze_keeps_domain_keywords_for_index_fallback(
    client: AsyncClient,
    test_token: str,
    tmp_workspace,
):
    (tmp_workspace / "workspace_memory_indexing.py").write_text(
        "def improve_local_memory_indexing() -> None:\n    pass\n",
        encoding="utf-8",
    )

    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)
    index_response = await client.post("/v1/workspace/index", headers=headers)
    assert index_response.status_code == 200

    response = await client.post(
        "/v1/task/analyze",
        headers=headers,
        json={"description": "i want to improve the local memory indexing"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "workspace_memory_indexing.py" in data["suggested_files"]
