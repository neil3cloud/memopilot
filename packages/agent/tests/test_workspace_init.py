"""Tests for workspace initialization endpoint."""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient

from agent.config import Config


@pytest.mark.asyncio
async def test_workspace_init_creates_directories(
    client: AsyncClient, test_token: str, test_config: Config
):
    """Workspace init creates the .memopilot directory structure."""
    response = await client.post(
        "/v1/workspace/init",
        headers={"X-Agent-Token": test_token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["initialized"] is True

    # Verify directories were created
    memopilot_dir = test_config.memopilot_dir
    assert (memopilot_dir / "rules").exists()
    assert (memopilot_dir / "memory").exists()
    assert (memopilot_dir / "logs").exists()
    assert (memopilot_dir / "context-packs").exists()
    assert (memopilot_dir / "context-templates").exists()
    assert (memopilot_dir / "memory" / "snapshots").exists()
    assert (test_config.workspace_path / ".vscode" / "mcp.json").exists()
    assert (test_config.workspace_path / ".github" / "copilot-instructions.md").exists()
    assert (test_config.workspace_path / ".cursor" / "rules" / "memopilot.mdc").exists()


@pytest.mark.asyncio
async def test_workspace_init_generates_retrieval_bootstrap_files(
    client: AsyncClient, test_token: str, test_config: Config
):
    response = await client.post(
        "/v1/workspace/init",
        headers={"X-Agent-Token": test_token},
    )
    assert response.status_code == 200

    mcp_path = test_config.workspace_path / ".vscode" / "mcp.json"
    mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    assert "servers" in mcp
    assert "memopilot" in mcp["servers"]
    assert mcp["servers"]["memopilot"]["args"] == ["-m", "agent.mcp_server"]
    assert mcp["servers"]["memopilot"]["cwd"] == "${workspaceFolder}"

    copilot_instructions = (test_config.workspace_path / ".github" / "copilot-instructions.md").read_text(encoding="utf-8")
    assert "memopilot-search" in copilot_instructions
    assert "memopilot-symbols" in copilot_instructions
    assert "memopilot-memory" in copilot_instructions
    assert "memopilot-profile" in copilot_instructions

    cursor_rule = (test_config.workspace_path / ".cursor" / "rules" / "memopilot.mdc").read_text(encoding="utf-8")
    assert "memopilot-search" in cursor_rule
    assert "memopilot-symbols" in cursor_rule


@pytest.mark.asyncio
async def test_workspace_init_preserves_unmanaged_instruction_files(
    client: AsyncClient, test_token: str, test_config: Config
):
    instructions_path = test_config.workspace_path / ".github" / "copilot-instructions.md"
    instructions_path.parent.mkdir(parents=True, exist_ok=True)
    instructions_path.write_text("# Existing project instructions\n", encoding="utf-8")

    response = await client.post(
        "/v1/workspace/init",
        headers={"X-Agent-Token": test_token},
    )
    assert response.status_code == 200

    content = instructions_path.read_text(encoding="utf-8")
    assert "# Existing project instructions" in content
    assert "MemoPilot managed block" in content


@pytest.mark.asyncio
async def test_workspace_init_is_idempotent(
    client: AsyncClient, test_token: str
):
    """Calling workspace init multiple times does not fail."""
    headers = {"X-Agent-Token": test_token}

    response1 = await client.post("/v1/workspace/init", headers=headers)
    assert response1.status_code == 200

    response2 = await client.post("/v1/workspace/init", headers=headers)
    assert response2.status_code == 200
    assert response2.json()["initialized"] is True
