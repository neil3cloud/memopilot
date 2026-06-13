"""Tests for POST /v1/model/route endpoint."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_model_route_basic(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    resp = await client.post(
        "/v1/model/route",
        headers=headers,
        json={"context_tokens": 5000, "task_type": "refactor"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "recommended" in data
    assert "alternatives" in data
    assert "budget_check" in data
    assert data["recommended"]["model_id"]
    assert data["recommended"]["provider"]
    assert isinstance(data["recommended"]["reasons"], list)
    assert data["budget_check"]["allowed"] is True


@pytest.mark.asyncio
async def test_model_route_prefers_local_for_small_context(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    resp = await client.post(
        "/v1/model/route",
        headers=headers,
        json={"context_tokens": 3000, "task_type": "fix", "privacy_level": "local_preferred"},
    )
    assert resp.status_code == 200
    data = resp.json()
    # Local should be recommended for small context + local preference
    assert data["recommended"]["provider"] == "ollama"
    assert data["recommended"]["cost_estimate_usd"] == 0.0


@pytest.mark.asyncio
async def test_model_route_cloud_when_context_exceeds_local(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    resp = await client.post(
        "/v1/model/route",
        headers=headers,
        json={"context_tokens": 50000, "task_type": "refactor"},
    )
    assert resp.status_code == 200
    data = resp.json()
    # Local doesn't fit (>32K), should recommend cloud
    assert data["recommended"]["provider"] in ("openai", "anthropic")
    assert data["recommended"]["cost_estimate_usd"] > 0


@pytest.mark.asyncio
async def test_model_route_honors_preferred_model(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    resp = await client.post(
        "/v1/model/route",
        headers=headers,
        json={"context_tokens": 5000, "task_type": "fix", "preferred_model": "gpt-4o"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["recommended"]["model_id"] == "gpt-4o"


@pytest.mark.asyncio
async def test_model_route_alternatives_exclude_recommended(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    resp = await client.post(
        "/v1/model/route",
        headers=headers,
        json={"context_tokens": 5000},
    )
    assert resp.status_code == 200
    data = resp.json()
    rec_id = data["recommended"]["model_id"]
    alt_ids = [a["model_id"] for a in data["alternatives"]]
    assert rec_id not in alt_ids


@pytest.mark.asyncio
async def test_model_route_invalid_tokens(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    resp = await client.post(
        "/v1/model/route",
        headers=headers,
        json={"context_tokens": -1},
    )
    assert resp.status_code == 422
