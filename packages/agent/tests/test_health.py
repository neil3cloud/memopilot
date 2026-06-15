"""Tests for the /v1/health endpoint."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_returns_ok(client: AsyncClient, test_token: str):
    """Health endpoint returns status ok with version info."""
    response = await client.get(
        "/v1/health",
        headers={"X-Agent-Token": test_token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["api_version"] == 1
    assert data["schema_version"] == 21


@pytest.mark.asyncio
async def test_health_accessible_without_token(client: AsyncClient):
    """Health endpoint is accessible without authentication for probes."""
    response = await client.get("/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
