from __future__ import annotations

import pytest
from httpx import AsyncClient

from agent.cost_guard import CostGuardService, calculate_savings
from agent.migration_runner import run_migrations


async def _run_db_migrations(test_db) -> None:
    conn = await test_db.connect()
    await run_migrations(conn)


async def _record_cloud_spend(client: AsyncClient, headers: dict[str, str], actual_cost: float) -> None:
    init = await client.post("/v1/workspace/init", headers=headers)
    assert init.status_code == 200

    started = await client.post(
        "/v1/task-runs/start",
        headers=headers,
        json={"user_request": "Track budget", "selected_model": "gpt-4o-mini", "estimated_cost": actual_cost},
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
            "output_tokens": 50,
            "estimated_cost": actual_cost,
            "actual_cost": actual_cost,
            "cache_hit": False,
            "purpose": "completion",
        },
    )
    assert usage.status_code == 200


@pytest.mark.asyncio
async def test_status_bar_warning_at_80_pct(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await _record_cloud_spend(client, headers, actual_cost=16.4)

    response = await client.get("/v1/cost/budget-status", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["pct_used"] == pytest.approx(0.82)
    assert payload["at_warning"] is True


@pytest.mark.asyncio
async def test_status_bar_error_at_100_pct(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await _record_cloud_spend(client, headers, actual_cost=20.0)

    response = await client.get("/v1/cost/budget-status", headers=headers)
    assert response.status_code == 200
    assert response.json()["at_limit"] is True


@pytest.mark.asyncio
async def test_savings_report_correct_dollar_amount(test_config, test_db):
    await _run_db_migrations(test_db)
    service = CostGuardService(config=test_config, db=test_db)
    task_run_id = await service.create_task_run(
        user_request="Compute savings",
        task_type="analysis",
        mode="auto",
        risk_level="low",
        selected_model="gpt-4o-mini",
        estimated_cost=0.002,
    )

    for _ in range(10):
        await service.record_ai_call(
            task_run_id=task_run_id,
            provider="openai",
            model="gpt-4o-mini",
            input_tokens=100,
            output_tokens=20,
            estimated_cost=0.002,
            actual_cost=0.002,
            cache_hit=False,
            context_pack_hash=None,
            purpose="completion",
        )

    report = await calculate_savings("2000-01-01", "2100-01-01", test_db)
    assert report.actual_cost == pytest.approx(0.02)
    assert report.hypothetical_frontier_cost == pytest.approx(0.18)
    assert report.savings == pytest.approx(0.16)
    assert report.cheap_cloud_tasks == 10


@pytest.mark.asyncio
async def test_per_task_cost_in_response(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    init = await client.post("/v1/workspace/init", headers=headers)
    assert init.status_code == 200

    response = await client.post(
        "/v1/task-runs/start",
        headers=headers,
        json={"user_request": "Estimate cost", "selected_model": "gpt-4o-mini", "estimated_cost": 0.25},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["estimated_cost"] == pytest.approx(0.25)
    assert payload["cost"]["estimated_cost_usd"] == pytest.approx(0.25)
    assert payload["cost"]["selected_tier"] == "cheap_cloud"
    assert "budget_gate" in payload


@pytest.mark.asyncio
async def test_hypothetical_frontier_cost_populated(test_config, test_db):
    await _run_db_migrations(test_db)
    service = CostGuardService(config=test_config, db=test_db)
    task_run_id = await service.create_task_run(
        user_request="Populate hypothetical frontier cost",
        task_type="analysis",
        mode="auto",
        risk_level="low",
        selected_model="gpt-4o-mini",
        estimated_cost=0.002,
    )
    await service.record_ai_call(
        task_run_id=task_run_id,
        provider="openai",
        model="gpt-4o-mini",
        input_tokens=10,
        output_tokens=5,
        estimated_cost=0.002,
        actual_cost=0.002,
        cache_hit=False,
        context_pack_hash=None,
        purpose="completion",
    )

    conn = await test_db.connect()
    cursor = await conn.execute("SELECT hypothetical_frontier_cost FROM ai_calls LIMIT 1")
    row = await cursor.fetchone()
    assert row is not None
    assert row["hypothetical_frontier_cost"] is not None
