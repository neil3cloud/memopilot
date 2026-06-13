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
    assert "memory_search" in builtins[0]["tools"]
    assert "patch_generate" in builtins[0]["tools"]


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
