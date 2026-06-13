"""Tests for Group 4 workspace profile, memory manager, and privacy dashboard."""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient

from agent.db import DatabaseManager
from agent.memory_governance import validate_status_transition


@pytest.mark.asyncio
async def test_workspace_profile_generated_on_index(
    client: AsyncClient,
    test_token: str,
    tmp_workspace,
):
    headers = {"X-Agent-Token": test_token}
    (tmp_workspace / "service.py").write_text("def run() -> None:\n    pass\n", encoding="utf-8")

    await client.post("/v1/workspace/init", headers=headers)
    indexed = await client.post("/v1/workspace/index", headers=headers)
    assert indexed.status_code == 200

    profile = await client.get("/v1/workspace/profile", headers=headers)
    assert profile.status_code == 200
    assert "primary_language: python" in profile.json()["profile_yaml"]

    validation = await client.get("/v1/workspace/profile/validate", headers=headers)
    assert validation.status_code == 200
    assert validation.json()["valid"] is True


@pytest.mark.asyncio
async def test_memory_manager_filters_and_actions(
    client: AsyncClient,
    test_token: str,
    test_db: DatabaseManager,
):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    conn = test_db.connection
    assert conn is not None
    await conn.execute(
        """
        INSERT INTO memory_items
        (
            id, type, title, body, source, source_hash, trust_level, tags_json, stale,
            memory_class, memory_status, visibility_scope, reusable, review_required
        )
        VALUES
        ('rule-1', 'rule', 'Rule One', 'Body', 'project', NULL, 3, '{}', 0, 'fact', 'discovered', 'workspace', 0, 0),
        ('symbol-1', 'symbol', 'Symbol One', 'Body', 'indexer', NULL, 1, '{}', 0, 'fact', 'discovered', 'workspace', 0, 0)
        """
    )
    await conn.commit()

    suggestion = await client.post(
        "/v1/memory/suggestions",
        headers=headers,
        json={"title": "AI suggestion", "body": "summary"},
    )
    assert suggestion.status_code == 200
    suggestion_id = suggestion.json()["memory_item_id"]

    symbols = await client.get("/v1/memory/items?filter_name=symbols", headers=headers)
    assert symbols.status_code == 200
    assert len(symbols.json()["items"]) == 1
    assert symbols.json()["items"][0]["id"] == "symbol-1"

    pending = await client.get("/v1/memory/items?filter_name=pending_approval", headers=headers)
    assert pending.status_code == 200
    assert any(item["id"] == suggestion_id for item in pending.json()["items"])

    approved = await client.post(f"/v1/memory/items/{suggestion_id}/approve", headers=headers)
    assert approved.status_code == 200

    cursor = await conn.execute(
        "SELECT memory_status, review_required, tags_json FROM memory_items WHERE id = ?",
        (suggestion_id,),
    )
    approved_row = await cursor.fetchone()
    assert approved_row is not None
    assert approved_row["memory_status"] == "confirmed"
    assert approved_row["review_required"] == 0
    assert json.loads(approved_row["tags_json"])["approved"] is True

    edited = await client.put(
        "/v1/memory/items/rule-1",
        headers=headers,
        json={"title": "Rule One Updated", "body": "Updated Body"},
    )
    assert edited.status_code == 200

    deleted = await client.delete("/v1/memory/items/symbol-1", headers=headers)
    assert deleted.status_code == 200


@pytest.mark.asyncio
async def test_privacy_dashboard_shows_recent_cloud_calls(
    client: AsyncClient,
    test_token: str,
):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    task = await client.post(
        "/v1/task-runs/start",
        headers=headers,
        json={"user_request": "Check privacy dashboard", "selected_model": "gpt-4o-mini"},
    )
    assert task.status_code == 200
    task_run_id = task.json()["task_run_id"]

    usage = await client.post(
        "/v1/cost/usage/record",
        headers=headers,
        json={
            "task_run_id": task_run_id,
            "provider": "openai",
            "model": "gpt-4o-mini",
            "input_tokens": 120,
            "output_tokens": 40,
            "estimated_cost": 0.03,
            "actual_cost": 0.03,
            "cache_hit": False,
            "purpose": "chat",
        },
    )
    assert usage.status_code == 200

    dashboard = await client.get("/v1/privacy/dashboard", headers=headers)
    assert dashboard.status_code == 200
    payload = dashboard.json()
    assert "code index" in payload["local_only"]
    assert "context pack sent to cloud provider" in payload["may_leave_machine"]
    assert payload["recent_cloud_calls"][0]["provider"] == "openai"


@pytest.mark.asyncio
async def test_suggested_memory_is_pending_approval(
    client: AsyncClient,
    test_token: str,
    test_db: DatabaseManager,
):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    suggestion = await client.post(
        "/v1/memory/suggestions",
        headers=headers,
        json={"title": "Generated summary", "body": "pending item"},
    )
    assert suggestion.status_code == 200
    item_id = suggestion.json()["memory_item_id"]

    conn = test_db.connection
    assert conn is not None
    cursor = await conn.execute(
        "SELECT trust_level, tags_json, memory_class, memory_status, review_required FROM memory_items WHERE id = ?",
        (item_id,),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["trust_level"] == 4
    assert row["memory_class"] == "fact"
    assert row["memory_status"] == "pending_review"
    assert row["review_required"] == 1
    tags = json.loads(row["tags_json"])
    assert tags["pending_approval"] is True


@pytest.mark.asyncio
async def test_blocked_memory_suggestion_creates_artifact(
    client: AsyncClient,
    test_token: str,
    test_db: DatabaseManager,
):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    response = await client.post(
        "/v1/memory/suggestions",
        headers=headers,
        json={
            "title": "Transcript dump",
            "body": "user: here is the trace\nassistant: here is the reply\npassword=super-secret",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["memory_item_id"] is None
    assert payload["artifact_id"]
    assert "blocked_reason" in payload

    conn = test_db.connection
    assert conn is not None
    cursor = await conn.execute("SELECT COUNT(*) AS total FROM memory_items")
    count_row = await cursor.fetchone()
    assert count_row is not None
    assert int(count_row["total"]) == 0

    cursor = await conn.execute(
        "SELECT artifact_path, blocked_reason FROM memory_artifacts WHERE id = ?",
        (payload["artifact_id"],),
    )
    artifact_row = await cursor.fetchone()
    assert artifact_row is not None
    assert artifact_row["blocked_reason"]
    assert "blocked-memory-" in artifact_row["artifact_path"]


@pytest.mark.asyncio
async def test_writeback_endpoint_rejects_large_diffs_and_transcripts(
    client: AsyncClient,
    test_token: str,
    test_db: DatabaseManager,
):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    diff_body = "\n".join(f"+ added line {index}" for index in range(205))
    diff_response = await client.post(
        "/v1/memory/writeback",
        headers=headers,
        json={"title": "Patch dump", "body": diff_body},
    )
    assert diff_response.status_code == 200
    diff_payload = diff_response.json()
    assert diff_payload["memory_item_id"] is None
    assert "full diff" in diff_payload["blocked_reason"]

    transcript_response = await client.post(
        "/v1/memory/writeback",
        headers=headers,
        json={"title": "Conversation dump", "body": "Human: hello\nAI: hi there"},
    )
    assert transcript_response.status_code == 200
    transcript_payload = transcript_response.json()
    assert transcript_payload["memory_item_id"] is None
    assert "raw transcript" in transcript_payload["blocked_reason"]

    conn = test_db.connection
    assert conn is not None
    cursor = await conn.execute(
        "SELECT artifact_type, blocked_reason FROM memory_artifacts"
    )
    rows = await cursor.fetchall()
    artifact_types = {row["artifact_type"] for row in rows}
    blocked_reasons = [row["blocked_reason"] for row in rows]
    assert artifact_types == {"patch_diff", "raw_transcript"}
    assert any("full diff" in reason for reason in blocked_reasons)
    assert any("raw transcript" in reason for reason in blocked_reasons)


@pytest.mark.asyncio
async def test_memory_review_queue_and_lifecycle_actions(
    client: AsyncClient,
    test_token: str,
    test_db: DatabaseManager,
):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    conn = test_db.connection
    assert conn is not None
    await conn.execute(
        """
        INSERT INTO memory_items (
            id, type, title, body, source, source_hash, trust_level, tags_json, stale,
            memory_class, memory_status, visibility_scope, reusable, review_required
        )
        VALUES
        ('review-approve', 'note', 'Approve me', 'Body', 'project', NULL, 4, '{"pending_approval": true}', 0, 'fact', 'pending_review', 'workspace', 0, 1),
        ('review-reject', 'note', 'Reject me', 'Body', 'project', NULL, 4, '{"pending_approval": true}', 0, 'fact', 'pending_review', 'workspace', 0, 1),
        ('confirmed-item', 'note', 'Already confirmed', 'Body', 'project', NULL, 3, '{}', 0, 'fact', 'confirmed', 'workspace', 1, 0),
        ('discovered-item', 'note', 'Discovered item', 'Body', 'project', NULL, 3, '{}', 0, 'fact', 'discovered', 'workspace', 0, 0)
        """
    )
    await conn.commit()

    queue = await client.get("/v1/memory/review", headers=headers)
    assert queue.status_code == 200
    queue_ids = {item["id"] for item in queue.json()["items"]}
    assert queue_ids == {"review-approve", "review-reject"}

    approved = await client.patch(
        "/v1/memory/items/review-approve/review",
        headers=headers,
        json={"decision": "approve"},
    )
    assert approved.status_code == 200

    rejected = await client.patch(
        "/v1/memory/items/review-reject/review",
        headers=headers,
        json={"decision": "reject"},
    )
    assert rejected.status_code == 200

    cursor = await conn.execute(
        "SELECT id, memory_status, review_required, reusable FROM memory_items WHERE id IN ('review-approve', 'review-reject') ORDER BY id"
    )
    rows = {row["id"]: row for row in await cursor.fetchall()}
    assert rows["review-approve"]["memory_status"] == "confirmed"
    assert rows["review-approve"]["review_required"] == 0
    assert rows["review-approve"]["reusable"] == 1
    assert rows["review-reject"]["memory_status"] == "rejected"
    assert rows["review-reject"]["review_required"] == 0

    invalid = await client.patch(
        "/v1/memory/items/confirmed-item/review",
        headers=headers,
        json={"decision": "approve"},
    )
    assert invalid.status_code == 400
    assert "Invalid memory status transition" in invalid.text

    queue_after = await client.get("/v1/memory/review", headers=headers)
    assert queue_after.status_code == 200
    assert queue_after.json()["items"] == []

    assert validate_status_transition("discovered", "confirmed") is True
    assert validate_status_transition("confirmed", "pending_review") is False
    assert validate_status_transition("evidence_only", "confirmed") is False


@pytest.mark.asyncio
async def test_context_build_records_recall_trace(
    client: AsyncClient,
    test_token: str,
    test_db: DatabaseManager,
):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    response = await client.post(
        "/v1/context/build",
        headers=headers,
        json={"task_description": "Add validation", "suggested_files": []},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["context_pack_hash"]

    conn = test_db.connection
    assert conn is not None
    cursor = await conn.execute(
        "SELECT context_pack_hash, included_memory_ids_json, excluded_memory_ids_json FROM recall_traces"
    )
    trace_row = await cursor.fetchone()
    assert trace_row is not None
    assert trace_row["context_pack_hash"] == payload["context_pack_hash"]
    assert json.loads(trace_row["included_memory_ids_json"]) == []
    assert json.loads(trace_row["excluded_memory_ids_json"]) == []


@pytest.mark.asyncio
async def test_workspace_profile_yaml_preserves_user_edited_fields_and_syncs_cache(
    client: AsyncClient,
    test_token: str,
    test_db: DatabaseManager,
    tmp_workspace,
):
    headers = {"X-Agent-Token": test_token}
    (tmp_workspace / "service.py").write_text("def run() -> None:\n    pass\n", encoding="utf-8")

    initialized = await client.post("/v1/workspace/init", headers=headers)
    assert initialized.status_code == 200

    profile_path = tmp_workspace / ".memopilot" / "workspace.profile.yaml"
    initial_yaml = profile_path.read_text(encoding="utf-8")
    assert "model_policy:  # user-edited" in initial_yaml

    customized_yaml = initial_yaml.replace("budget_profile: cost_saver", "budget_profile: frontier")
    profile_path.write_text(customized_yaml, encoding="utf-8")
    (tmp_workspace / "pyproject.toml").write_text(
        "[project]\ndependencies = [\"fastapi\"]\n",
        encoding="utf-8",
    )

    rebuilt = await client.post("/v1/workspace/profile/rebuild", headers=headers)
    assert rebuilt.status_code == 200
    rebuilt_yaml = rebuilt.json()["profile_yaml"]
    assert "budget_profile: frontier" in rebuilt_yaml
    assert "frameworks:\n  - fastapi" in rebuilt_yaml

    conn = test_db.connection
    assert conn is not None
    cursor = await conn.execute(
        "SELECT profile_yaml, is_cache, synced_from_yaml_at FROM workspace_profile WHERE id = ?",
        ("default",),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["is_cache"] == 1
    assert row["synced_from_yaml_at"] is not None
    assert "budget_profile: frontier" in row["profile_yaml"]


@pytest.mark.asyncio
async def test_memory_recall_applies_visibility_policy_and_trace_logging(
    client: AsyncClient,
    test_token: str,
    test_db: DatabaseManager,
):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    conn = test_db.connection
    assert conn is not None
    await conn.execute(
        """
        INSERT INTO memory_items (
            id, type, title, body, source, source_hash, trust_level, tags_json, stale,
            memory_class, memory_status, visibility_scope, use_policy_json, provenance_json,
            review_required
        )
        VALUES
        (
            'allowed-1', 'note', 'Allowed shared memory', 'shared knowledge body', 'project', NULL,
            3, '{}', 0, 'fact', 'confirmed', 'workspace',
            '{"allowed_in_cloud_context": true}',
            '[{"source_type": "file", "source_ref": "service.py", "source_path": "service.py"}]',
            0
        ),
        (
            'local-1', 'note', 'Local shared memory', 'shared knowledge body', 'project', NULL,
            3, '{}', 0, 'fact', 'confirmed', 'local_only',
            '{"allowed_in_cloud_context": true}', NULL, 0
        ),
        (
            'policy-blocked-1', 'note', 'Policy shared memory', 'shared knowledge body', 'project', NULL,
            3, '{}', 0, 'fact', 'confirmed', 'workspace',
            '{"allowed_in_cloud_context": false}', NULL, 0
        ),
        (
            'superseded-1', 'note', 'Superseded shared memory', 'shared knowledge body', 'project', NULL,
            3, '{}', 0, 'fact', 'superseded', 'workspace',
            '{"allowed_in_cloud_context": true}', NULL, 0
        ),
        (
            'stale-1', 'note', 'Stale shared memory', 'shared knowledge body', 'project', NULL,
            3, '{}', 1, 'fact', 'stale', 'workspace',
            '{"allowed_in_cloud_context": true}', NULL, 0
        )
        """
    )
    await conn.commit()

    recall = await client.post(
        "/v1/memory/recall",
        headers=headers,
        json={"query": "shared", "visibility_target": "cloud_context", "limit": 10},
    )
    assert recall.status_code == 200
    payload = recall.json()
    assert [item["memory_id"] for item in payload["items"]] == ["allowed-1"]
    assert payload["items"][0]["provenance"][0]["source_ref"] == "service.py"
    assert payload["trace_id"]
    assert payload["context_pack_hash"]

    cursor = await conn.execute(
        "SELECT request_json, included_memory_ids_json, excluded_memory_ids_json FROM recall_traces"
    )
    trace_row = await cursor.fetchone()
    assert trace_row is not None
    assert "allowed-1" in json.loads(trace_row["included_memory_ids_json"])
    excluded = json.loads(trace_row["excluded_memory_ids_json"])
    assert "local-1" in excluded
    assert "policy-blocked-1" in excluded
    assert "superseded-1" in excluded
    assert "stale-1" in excluded
