"""Tests for model_router.py — outcome-aware routing and provider resolution."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from agent.local_model_discovery import LocalModel
from agent.migration_runner import run_migrations
from agent.model_router import (
    ModelTier,
    ProviderDecision,
    _classify_tier,
    get_outcome_routing_hint,
    route_model,
    route_with_outcome,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _local(model_id: str, ctx: int = 32_768, source: str = "ollama") -> LocalModel:
    return LocalModel(
        model_id=model_id,
        name=model_id,
        source=source,
        max_context_tokens=ctx,
        supports_tools=True,
        cost_per_1m_input=0.0,
        cost_per_1m_output=0.0,
    )


# ---------------------------------------------------------------------------
# Outcome-aware routing (route_with_outcome / get_outcome_routing_hint)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# _classify_tier
# ---------------------------------------------------------------------------

def test_classify_tier_high_risk_returns_advanced():
    assert _classify_tier("fix", "high") == "advanced"
    assert _classify_tier("auto", "critical") == "advanced"


def test_classify_tier_security_change_returns_advanced():
    assert _classify_tier("security_change", "low") == "advanced"
    assert _classify_tier("billing_change", "low") == "advanced"


def test_classify_tier_formatting_returns_no_ai():
    assert _classify_tier("code_formatting", "low") == "no_ai"
    assert _classify_tier("import_sorting", "low") == "no_ai"
    assert _classify_tier("exact_search", "low") == "no_ai"


def test_classify_tier_summarization_returns_local():
    assert _classify_tier("summarization", "low") == "local"
    assert _classify_tier("memory_generation", "low") == "local"


def test_classify_tier_default_returns_standard():
    assert _classify_tier("fix", "low") == "standard"
    assert _classify_tier("auto", "low") == "standard"


# ---------------------------------------------------------------------------
# route_model — no_ai
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_route_model_no_ai_task():
    decision = await route_model("code_formatting", "low", 500, {})
    assert decision.tier == "no_ai"
    assert decision.model_id is None
    assert decision.provider is None


# ---------------------------------------------------------------------------
# route_model — local preferred for cost_saver
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_route_model_prefers_local_for_cost_saver():
    local = _local("qwen2.5-coder:7b", ctx=32_768)
    with patch("agent.model_router._get_local_models", AsyncMock(return_value=[local])):
        decision = await route_model(
            "fix", "low", 5_000, {"budget_profile": "cost_saver"}
        )
    assert decision.tier == "local"
    assert decision.model_id == "qwen2.5-coder:7b"
    assert decision.provider == "ollama"
    assert decision.cost_estimate_usd == 0.0


# ---------------------------------------------------------------------------
# route_model — strict_local blocks cloud
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_route_model_strict_local_blocks_cloud_when_no_local():
    with patch("agent.model_router._get_local_models", AsyncMock(return_value=[])):
        decision = await route_model(
            "fix", "low", 5_000,
            {"budget_profile": "strict_local", "anthropic_api_key": "sk-test"},
        )
    assert decision.tier == "context_pack_only"
    assert decision.model_id is None


# ---------------------------------------------------------------------------
# route_model — context window enforcement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_route_model_skips_local_with_too_small_context():
    small_local = _local("phi3:mini", ctx=4_096)
    with patch("agent.model_router._get_local_models", AsyncMock(return_value=[small_local])):
        decision = await route_model(
            "fix", "low", 10_000,
            {"budget_profile": "cost_saver", "anthropic_api_key": "sk-test"},
        )
    assert decision.provider != "ollama"


# ---------------------------------------------------------------------------
# route_model — cloud fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_route_model_falls_back_to_cloud():
    with patch("agent.model_router._get_local_models", AsyncMock(return_value=[])):
        decision = await route_model(
            "fix", "low", 5_000,
            {"budget_profile": "balanced", "anthropic_api_key": "sk-test"},
        )
    assert decision.provider == "anthropic"
    assert decision.model_id is not None


@pytest.mark.asyncio
async def test_route_model_no_providers_returns_context_pack_only():
    with patch("agent.model_router._get_local_models", AsyncMock(return_value=[])):
        decision = await route_model("fix", "low", 5_000, {"budget_profile": "balanced"})
    assert decision.tier == "context_pack_only"
    assert decision.model_id is None


# ---------------------------------------------------------------------------
# route_model — host model
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_route_model_uses_host_when_available():
    host_model = {"model_id": "gpt-4o", "max_context_tokens": 128_000}
    with patch("agent.model_router._get_local_models", AsyncMock(return_value=[])):
        decision = await route_model(
            "fix", "low", 5_000,
            {"budget_profile": "balanced", "host_models_available": True,
             "host_model_list": [host_model]},
        )
    assert decision.provider == "host"
    assert decision.model_id == "gpt-4o"


# ---------------------------------------------------------------------------
# route_model — advanced tier goes to cloud even with local available
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_route_model_advanced_tier_skips_local():
    local = _local("qwen2.5-coder:7b", ctx=32_768)
    with patch("agent.model_router._get_local_models", AsyncMock(return_value=[local])):
        decision = await route_model(
            "security_change", "low", 5_000,
            {"budget_profile": "cost_saver", "anthropic_api_key": "sk-test"},
        )
    assert decision.provider != "ollama"
