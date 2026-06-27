"""Tests for POST /v1/context/build endpoint."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_context_build_empty_files(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    resp = await client.post(
        "/v1/context/build",
        headers=headers,
        json={"task_description": "Add validation", "suggested_files": []},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "files" in data
    assert "total_tokens" in data
    assert "estimated_cost_usd" in data
    assert data["total_tokens"] >= 0


@pytest.mark.asyncio
async def test_context_build_with_nonexistent_files(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    resp = await client.post(
        "/v1/context/build",
        headers=headers,
        json={
            "task_description": "Fix bug",
            "suggested_files": ["nonexistent_file_xyz.py"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["files"]) == 1
    assert data["files"][0]["path"] == "nonexistent_file_xyz.py"
    assert data["files"][0]["tokens"] >= 1


@pytest.mark.asyncio
async def test_context_build_file_overrides_take_priority(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    resp = await client.post(
        "/v1/context/build",
        headers=headers,
        json={
            "task_description": "Refactor service",
            "suggested_files": ["file_a.py", "file_b.py"],
            "file_overrides": ["override_x.py"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    paths = [f["path"] for f in data["files"]]
    assert "override_x.py" in paths
    assert "file_a.py" not in paths


@pytest.mark.asyncio
async def test_context_build_response_shape(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    resp = await client.post(
        "/v1/context/build",
        headers=headers,
        json={"task_description": "Add feature", "suggested_files": ["a.py"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["files"], list)
    assert isinstance(data["rules"], list)
    assert isinstance(data["skills"], list)
    assert isinstance(data["total_tokens"], int)
    assert isinstance(data["estimated_cost_usd"], float)


@pytest.mark.asyncio
async def test_context_build_caps_at_20_files(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    many_files = [f"file_{i}.py" for i in range(30)]
    resp = await client.post(
        "/v1/context/build",
        headers=headers,
        json={"task_description": "Large change", "suggested_files": many_files},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["files"]) <= 20


@pytest.mark.asyncio
async def test_context_build_falls_back_to_index_when_suggested_files_empty(
    client: AsyncClient,
    test_token: str,
    tmp_workspace,
):
    (tmp_workspace / "memory_indexing_service.py").write_text(
        "def build_memory_index() -> str:\n    return 'ok'\n",
        encoding="utf-8",
    )

    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)
    index_response = await client.post("/v1/workspace/index", headers=headers)
    assert index_response.status_code == 200

    resp = await client.post(
        "/v1/context/build",
        headers=headers,
        json={
            "task_description": "Improve memory indexing service",
            "suggested_files": [],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    paths = [entry["path"] for entry in data["files"]]
    assert "memory_indexing_service.py" in paths


@pytest.mark.asyncio
async def test_context_assemble_returns_rendered_markdown(
    client: AsyncClient,
    test_token: str,
    tmp_workspace,
):
    (tmp_workspace / "billing_service.py").write_text(
        "def validate_billing() -> bool:\n    return True\n",
        encoding="utf-8",
    )

    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)
    index_response = await client.post("/v1/workspace/index", headers=headers)
    assert index_response.status_code == 200

    resp = await client.post(
        "/v1/context/assemble",
        headers=headers,
        json={
            "task_description": "Explain billing validation flow",
            "files_in_focus": ["billing_service.py"],
            "caller": "copilot_lm_tool",
            "max_output_tokens": 2000,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "rendered_markdown" in data
    assert "MemoPilot Context" in data["rendered_markdown"]
    assert "billing_service.py" in data["rendered_markdown"]
    assert data["context_pack_hash"]
    assert data["total_tokens"] >= 0
