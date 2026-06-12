"""Tests for Group 1 cost guard, budget tracking, and response cache APIs."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_budget_tracking_and_usage_recording(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    check = await client.post(
        "/v1/cost/guard/check",
        headers=headers,
        json={"estimated_cost_usd": 5.0},
    )
    assert check.status_code == 200
    assert check.json()["allowed"] is True

    started = await client.post(
        "/v1/task-runs/start",
        headers=headers,
        json={"user_request": "Implement feature X", "estimated_cost": 5.0},
    )
    assert started.status_code == 200
    task_run_id = started.json()["task_run_id"]

    usage = await client.post(
        "/v1/cost/usage/record",
        headers=headers,
        json={
            "task_run_id": task_run_id,
            "provider": "openai",
            "model": "gpt-4o-mini",
            "input_tokens": 100,
            "output_tokens": 20,
            "estimated_cost": 2.0,
            "actual_cost": 3.0,
            "cache_hit": False,
            "purpose": "completion",
        },
    )
    assert usage.status_code == 200

    status = await client.get("/v1/cost/budget/status", headers=headers)
    assert status.status_code == 200
    data = status.json()
    assert data["monthly_budget_usd"] == pytest.approx(20.0)
    assert data["spent_usd"] == pytest.approx(3.0)
    assert data["remaining_usd"] == pytest.approx(17.0)


@pytest.mark.asyncio
async def test_response_cache_lookup_records_savings(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    stored = await client.post(
        "/v1/cache/store",
        headers=headers,
        json={
            "context_pack_hash": "hash-123",
            "response_text": "cached response",
            "provider": "openai",
            "model": "gpt-4o-mini",
            "estimated_cost": 1.25,
        },
    )
    assert stored.status_code == 200
    assert stored.json()["stored"] is True

    looked_up = await client.post(
        "/v1/cache/lookup",
        headers=headers,
        json={"context_pack_hash": "hash-123"},
    )
    assert looked_up.status_code == 200
    lookup_data = looked_up.json()
    assert lookup_data["hit"] is True
    assert lookup_data["response_text"] == "cached response"
    assert lookup_data["hit_count"] == 1

    report = await client.get("/v1/cost/report/savings", headers=headers)
    assert report.status_code == 200
    report_data = report.json()
    assert report_data["cache_savings_usd"] == pytest.approx(1.25)
    assert report_data["month_spend_usd"] == pytest.approx(0.0)
