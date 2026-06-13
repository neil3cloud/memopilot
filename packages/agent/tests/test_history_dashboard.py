"""Tests for GET /v1/task/history and GET /v1/cost/dashboard endpoints."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_task_history_returns_entries(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    resp = await client.get("/v1/task/history?limit=10", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "entries" in data
    assert "total_count" in data
    assert isinstance(data["entries"], list)


@pytest.mark.asyncio
async def test_task_history_entry_shape(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    # Record an AI call first to generate history entries
    await client.post("/v1/cost/usage/record", headers=headers, json={
        "task_run_id": "test-run-1",
        "provider": "ollama",
        "model": "codellama-13b-local",
        "input_tokens": 1000,
        "output_tokens": 500,
        "estimated_cost": 0.0,
        "actual_cost": 0.0,
    })

    resp = await client.get("/v1/task/history?limit=5", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    if data["entries"]:
        entry = data["entries"][0]
        assert "task_id" in entry
        assert "description" in entry
        assert "status" in entry
        assert "cost_usd" in entry
        assert "created_at" in entry


@pytest.mark.asyncio
async def test_cost_dashboard_returns_data(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    resp = await client.get("/v1/cost/dashboard?days=7", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["period_days"] == 7
    assert "total_cost_usd" in data
    assert "total_calls" in data
    assert "total_tokens" in data
    assert "saved_usd" in data
    assert isinstance(data["by_day"], list)
    assert isinstance(data["by_model"], list)


@pytest.mark.asyncio
async def test_cost_dashboard_by_model_shape(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    resp = await client.get("/v1/cost/dashboard?days=30", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    for entry in data["by_model"]:
        assert "provider" in entry
        assert "model" in entry
        assert "calls" in entry
        assert "tokens" in entry
        assert "cost_usd" in entry


@pytest.mark.asyncio
async def test_cost_dashboard_by_day_limited(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    resp = await client.get("/v1/cost/dashboard?days=7", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    # Should have at most 30 day entries (capped)
    assert len(data["by_day"]) <= 30
