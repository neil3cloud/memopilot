"""Tests for outcome-aware model routing."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from agent.migration_runner import run_migrations
from agent.model_router import ModelTier, get_outcome_routing_hint, route_with_outcome


async def _insert_failed_run(conn, task_run_id: str, model_id: str, file_path: str) -> None:
    await conn.execute(
        """
        INSERT INTO task_runs
        (
            id, user_request, task_type, mode, risk_level, active_rules_json,
            active_skills_json, context_pack_path, selected_model, estimated_cost,
            actual_cost, status, routing_escalation_source, routing_base_tier, model_override,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (
            task_run_id,
            "Fix failing file",
            "fix",
            "auto",
            None,
            None,
            None,
            "context-pack.json",
            model_id,
            0.01,
            None,
            "failed",
            None,
            None,
            0,
        ),
    )
    await conn.execute(
        """
        INSERT INTO patch_attempts
        (
            id, task_run_id, patch_path, files_changed_json, risk_level,
            rule_compliance_score, approved, applied, validation_status
        )
        VALUES (?, ?, ?, json_array(?), ?, ?, 0, 0, ?)
        """,
        (
            f"patch-{task_run_id}",
            task_run_id,
            "inline.diff",
            file_path,
            "medium",
            0.5,
            "failed",
        ),
    )
    await conn.commit()


@pytest.mark.asyncio
async def test_two_failures_triggers_frontier_escalation(test_db):
    conn = await test_db.connect()
    await run_migrations(conn)
    await _insert_failed_run(conn, "run-1", "codellama-13b-local", "src\\problem.py")
    await _insert_failed_run(conn, "run-2", "gpt-4o", "src\\problem.py")

    tier, reason = await get_outcome_routing_hint("fix", ["src\\problem.py"], conn)

    assert tier == ModelTier.FRONTIER
    assert reason is not None
    assert "src/problem.py" in reason
    assert "2 failed non-frontier attempts" in reason


@pytest.mark.asyncio
async def test_one_failure_does_not_trigger_escalation(test_db):
    conn = await test_db.connect()
    await run_migrations(conn)
    await _insert_failed_run(conn, "run-1", "gpt-4o", "src\\problem.py")

    tier, reason = await get_outcome_routing_hint("fix", ["src\\problem.py"], conn)

    assert tier is None
    assert reason is None


@pytest.mark.asyncio
async def test_per_model_options_in_routing_response(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    response = await client.post(
        "/v1/model/route",
        headers=headers,
        json={"context_tokens": 50000, "task_type": "refactor", "files_in_context": ["src\\problem.py"]},
    )

    assert response.status_code == 200
    data = response.json()
    assert "options" in data
    assert [item["tier"] for item in data["options"]] == ["local", "cheap_cloud", "frontier"]


@pytest.mark.asyncio
async def test_inline_override_recorded(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    response = await client.post(
        "/v1/model/route",
        headers=headers,
        json={
            "context_tokens": 5000,
            "task_type": "fix",
            "preferred_model": "gpt-4o",
            "model_override": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["model_override"] is True


@pytest.mark.asyncio
async def test_routing_reason_includes_escalation_conditions(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    response = await client.post(
        "/v1/model/route",
        headers=headers,
        json={"context_tokens": 50000, "task_type": "refactor", "files_in_context": ["src\\problem.py"]},
    )

    assert response.status_code == 200
    reasons = response.json()["recommended"]["reasons"]
    assert any("2 failed non-frontier attempts" in reason for reason in reasons)


@pytest.mark.asyncio
async def test_no_escalation_for_local_tier(test_db):
    conn = await test_db.connect()
    await run_migrations(conn)
    await _insert_failed_run(conn, "run-1", "gpt-4o", "src\\problem.py")
    await _insert_failed_run(conn, "run-2", "gpt-4o", "src\\problem.py")

    decision = await route_with_outcome("fix", ["src\\problem.py"], 5000, test_db)

    assert decision.tier == ModelTier.LOCAL
    assert decision.base_tier == ModelTier.LOCAL
    assert decision.escalation_source is None
