"""Tests for GET /v1/mcp/tools endpoint."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_mcp_tools_returns_builtin(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    resp = await client.get("/v1/mcp/tools", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "servers" in data
    assert isinstance(data["servers"], list)
    # Should at least have the builtin server
    builtins = [s for s in data["servers"] if s["name"] == "memopilot-builtin"]
    assert len(builtins) == 1
    assert builtins[0]["status"] == "connected"
    assert "memopilot-search" in builtins[0]["tools"]
    assert "memopilot-symbols" in builtins[0]["tools"]
    assert "memopilot-memory" in builtins[0]["tools"]
    assert "memopilot-profile" in builtins[0]["tools"]
    assert "memopilot-ingest-session" in builtins[0]["tools"]


@pytest.mark.asyncio
async def test_symbol_search_endpoint_returns_matches(client: AsyncClient, test_token: str, test_db):
    headers = {"X-Agent-Token": test_token}
    conn = await test_db.connect()
    await conn.execute(
        """
        INSERT INTO symbols (id, file_path, name, kind, start_line, end_line, signature, summary, content_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "sym-taskflow",
            "packages/extension/src/controllers/TaskFlowController.ts",
            "TaskFlowController",
            "class",
            1,
            120,
            "class TaskFlowController",
            "Coordinates the task flow pipeline.",
            "hash-1",
        ),
    )
    await conn.commit()

    resp = await client.post(
        "/v1/symbols/search",
        headers=headers,
        json={"query": "TaskFlow", "limit": 5},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["symbols"]) == 1
    assert data["symbols"][0]["name"] == "TaskFlowController"


@pytest.mark.asyncio
async def test_mcp_tools_server_shape(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    resp = await client.get("/v1/mcp/tools", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    for server in data["servers"]:
        assert "name" in server
        assert "status" in server
        assert "tools" in server
        assert isinstance(server["tools"], list)


@pytest.mark.asyncio
async def test_mcp_tools_detects_generated_vscode_mcp_config(client: AsyncClient, test_token: str, tmp_workspace):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    resp = await client.get("/v1/mcp/tools", headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    configured = [server for server in data["servers"] if server["name"] == "memopilot" and server["status"] == "configured"]
    assert len(configured) == 1
    assert configured[0]["tools"] == [
        "memopilot-search",
        "memopilot-symbols",
        "memopilot-memory",
        "memopilot-profile",
        "memopilot-ingest-session",
    ]
