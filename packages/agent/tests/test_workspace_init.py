"""Tests for workspace initialization endpoint."""

from __future__ import annotations

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
    assert (memopilot_dir / "snapshots").exists()


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
