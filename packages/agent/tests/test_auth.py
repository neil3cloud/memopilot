"""Tests for authentication middleware."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_missing_token_returns_401(client: AsyncClient):
    """Requests without X-Agent-Token header are rejected."""
    response = await client.get("/v1/cost/budget/status")
    assert response.status_code == 401
    assert "Unauthorized" in response.text


@pytest.mark.asyncio
async def test_invalid_token_returns_401(client: AsyncClient):
    """Requests with incorrect token are rejected."""
    response = await client.get(
        "/v1/cost/budget/status",
        headers={"X-Agent-Token": "invalid-token-value"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_valid_token_allows_request(client: AsyncClient, test_token: str):
    """Requests with correct token are allowed."""
    response = await client.get(
        "/v1/health",
        headers={"X-Agent-Token": test_token},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_empty_token_returns_401(client: AsyncClient):
    """Empty token string is rejected."""
    response = await client.get(
        "/v1/cost/budget/status",
        headers={"X-Agent-Token": ""},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_health_endpoint_does_not_require_auth(client: AsyncClient):
    """Health endpoint is accessible without authentication for deployment probes."""
    response = await client.get("/v1/health")
    assert response.status_code == 200
