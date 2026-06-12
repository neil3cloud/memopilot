"""Tests for Group 2: agentic loop, DB write blocking, and redaction."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_security_redaction_endpoint_masks_credentials(
    client: AsyncClient, test_token: str
):
    headers = {"X-Agent-Token": test_token}
    response = await client.post(
        "/v1/security/redact",
        headers=headers,
        json={"text": "api_key=abcd1234 password: super-secret bearer abc.token"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["redacted_count"] >= 2
    assert "[REDACTED" in data["redacted_text"]
    assert "super-secret" not in data["redacted_text"]


@pytest.mark.asyncio
async def test_db_write_check_blocks_writes(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}

    blocked = await client.post(
        "/v1/security/db-write/check",
        headers=headers,
        json={"statement": "UPDATE users SET role='admin'"},
    )
    assert blocked.status_code == 200
    assert blocked.json()["blocked"] is True

    allowed = await client.post(
        "/v1/security/db-write/check",
        headers=headers,
        json={"statement": "SELECT * FROM users"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["blocked"] is False


@pytest.mark.asyncio
async def test_agentic_loop_caps_iterations_and_records_blocked_calls(
    client: AsyncClient,
    test_token: str,
    test_db,
):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    task_run = await client.post(
        "/v1/task-runs/start",
        headers=headers,
        json={"user_request": "Run MCP workflow"},
    )
    assert task_run.status_code == 200
    task_run_id = task_run.json()["task_run_id"]

    run_response = await client.post(
        "/v1/mcp/agentic/run",
        headers=headers,
        json={
            "task_run_id": task_run_id,
            "server_name": "local-mcp",
            "max_iterations": 10,
            "tool_calls": [
                {"tool_name": "lookup", "input_data": {"query": "SELECT 1"}},
                {"tool_name": "db", "input_data": {"sql": "UPDATE users SET role='admin'"}},
                {"tool_name": "tool3", "input_data": {"token": "abc123"}},
                {"tool_name": "tool4", "input_data": {"text": "noop"}},
                {"tool_name": "tool5", "input_data": {"text": "noop"}},
                {"tool_name": "tool6", "input_data": {"text": "should-not-run"}},
            ],
        },
    )
    assert run_response.status_code == 200
    payload = run_response.json()
    assert payload["requested_iterations"] == 6
    assert payload["executed_iterations"] == 5
    assert payload["capped_at"] == 5
    assert payload["calls"][1]["status"] == "blocked"
    assert payload["calls"][1]["blocked_reason"] == "db_write_blocked_by_policy"

    conn = test_db.connection
    assert conn is not None
    cursor = await conn.execute(
        "SELECT COUNT(*) AS total FROM mcp_calls WHERE task_run_id = ?",
        (task_run_id,),
    )
    count_row = await cursor.fetchone()
    assert count_row["total"] == 5
