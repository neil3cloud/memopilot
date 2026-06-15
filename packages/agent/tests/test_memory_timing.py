"""Tests for smarter memory proposal timing behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient

from agent.config import Config
from agent.db import DatabaseManager
from agent.memory_manager_service import MemoryManagerService
from agent.migration_runner import run_migrations


@pytest.fixture
def config(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return Config(
        workspace_path=workspace,
        memopilot_dir=workspace / ".memopilot",
        global_dir=tmp_path / ".memopilot-global",
    )


@pytest_asyncio.fixture
async def db():
    manager = DatabaseManager(Path(":memory:"))
    conn = await manager.connect()
    await conn.execute("PRAGMA foreign_keys = ON")
    await run_migrations(conn)
    yield manager
    await manager.close()


async def _fetch_memory_row(db: DatabaseManager, item_id: str):
    conn = await db.connect()
    cursor = await conn.execute(
        """
        SELECT id, memory_class, memory_status, trust_level, review_required,
               reusable, tags_json, source_path, workspace_root
        FROM memory_items
        WHERE id = ?
        """,
        (item_id,),
    )
    row = await cursor.fetchone()
    assert row is not None
    return row


async def _insert_memory_item(
    db: DatabaseManager,
    *,
    item_id: str,
    title: str,
    body: str,
    source_path: str | None,
    memory_status: str,
    workspace_root: str,
) -> None:
    conn = await db.connect()
    await conn.execute(
        """
        INSERT INTO memory_items (
            id, type, title, body, source, source_path, source_hash,
            trust_level, tags_json, stale, memory_class, memory_status,
            visibility_scope, reusable, review_required, workspace_root,
            created_at, updated_at
        )
        VALUES (
            ?, 'ai_summary', ?, ?, 'test', ?, NULL,
            4, '{"pending_approval": true}', 0, 'fact', ?,
            'workspace', 0, 1, ?,
            datetime('now'), datetime('now')
        )
        """,
        (item_id, title, body, source_path, memory_status, workspace_root),
    )
    await conn.commit()


@pytest.mark.asyncio
async def test_factual_git_diff_proposals_auto_confirm(config: Config, db: DatabaseManager):
    service = MemoryManagerService(config=config, db=db)
    outcome = await service.suggest_memory_update_smart(
        title="Updated parser behavior",
        body="Parser now handles empty input without raising.",
        source="code_analysis",
        source_path="src/parser.py",
        workspace_root=str(config.workspace_path),
        memory_class="fact",
        derivation_source="git_diff",
        task_run_id="task-auto-1",
    )

    assert outcome.memory_item_id is not None
    assert outcome.pending_approval is False

    row = await _fetch_memory_row(db, outcome.memory_item_id)
    assert row["memory_class"] == "fact"
    assert row["memory_status"] == "confirmed"
    assert row["trust_level"] == 4
    assert row["review_required"] == 0
    assert row["reusable"] == 1
    assert json.loads(row["tags_json"])["pending_approval"] is False
    assert json.loads(row["tags_json"])["derivation_source"] == "git_diff"


@pytest.mark.asyncio
async def test_factual_call_graph_proposals_auto_confirm(config: Config, db: DatabaseManager):
    service = MemoryManagerService(config=config, db=db)
    outcome = await service.suggest_memory_update_smart(
        title="Resolver calls storage layer",
        body="User resolver delegates persistence to storage.save_user().",
        source="call_graph",
        source_path="src/graphql/resolver.py",
        workspace_root=str(config.workspace_path),
        memory_class="fact",
        derivation_source="call_graph",
        task_run_id="task-auto-2",
    )

    assert outcome.memory_item_id is not None
    assert outcome.pending_approval is False

    row = await _fetch_memory_row(db, outcome.memory_item_id)
    assert row["memory_status"] == "confirmed"
    assert row["review_required"] == 0
    assert json.loads(row["tags_json"])["pending_approval"] is False
    assert json.loads(row["tags_json"])["derivation_source"] == "call_graph"


@pytest.mark.asyncio
@pytest.mark.parametrize("memory_class", ["instruction", "lesson"])
async def test_instruction_and_lesson_proposals_still_require_review(
    config: Config,
    db: DatabaseManager,
    memory_class: str,
):
    service = MemoryManagerService(config=config, db=db)
    outcome = await service.suggest_memory_update_smart(
        title=f"{memory_class.title()} proposal",
        body="Always run the focused regression suite before merging.",
        source="code_analysis",
        source_path="docs/process.md",
        workspace_root=str(config.workspace_path),
        memory_class=memory_class,
        derivation_source="git_diff",
    )

    assert outcome.memory_item_id is not None
    assert outcome.pending_approval is True

    row = await _fetch_memory_row(db, outcome.memory_item_id)
    assert row["memory_class"] == memory_class
    assert row["memory_status"] == "pending_review"
    assert row["trust_level"] == 4
    assert row["review_required"] == 1
    assert row["reusable"] == 0
    assert json.loads(row["tags_json"])["pending_approval"] is True
    assert json.loads(row["tags_json"])["derivation_source"] == "git_diff"


@pytest.mark.asyncio
async def test_get_pending_proposals_for_module_returns_matching_items(
    config: Config,
    db: DatabaseManager,
):
    service = MemoryManagerService(config=config, db=db)
    workspace_root = str(config.workspace_path)
    other_workspace_root = str((config.workspace_path.parent / "other-workspace").resolve())

    await _insert_memory_item(
        db,
        item_id="pending-source",
        title="Module note",
        body="Directly about the module.",
        source_path="src/app/module.py",
        memory_status="pending_review",
        workspace_root=workspace_root,
    )
    await _insert_memory_item(
        db,
        item_id="pending-helper",
        title="Module helper note",
        body="Also tied to the module.",
        source_path="src/app/module.py::helper",
        memory_status="pending_review",
        workspace_root=workspace_root,
    )
    await _insert_memory_item(
        db,
        item_id="pending-body",
        title="Body match",
        body="See src/app/module.py for the follow-up change.",
        source_path="docs/notes.md",
        memory_status="pending_review",
        workspace_root=workspace_root,
    )
    await _insert_memory_item(
        db,
        item_id="confirmed-match",
        title="Confirmed item",
        body="Should not be returned.",
        source_path="src/app/module.py",
        memory_status="confirmed",
        workspace_root=workspace_root,
    )
    await _insert_memory_item(
        db,
        item_id="pending-other-module",
        title="Other module",
        body="Different file.",
        source_path="src/app/other.py",
        memory_status="pending_review",
        workspace_root=workspace_root,
    )
    await _insert_memory_item(
        db,
        item_id="pending-other-workspace",
        title="Other workspace",
        body="See src/app/module.py in another workspace.",
        source_path="src/app/module.py",
        memory_status="pending_review",
        workspace_root=other_workspace_root,
    )

    items = await service.get_pending_proposals_for_module(
        module_path="src/app/module.py",
        workspace_root=workspace_root,
        limit=10,
    )

    returned_ids = [item.id for item in items]
    assert set(returned_ids) == {"pending-source", "pending-helper", "pending-body"}
    assert set(returned_ids[:2]) == {"pending-source", "pending-helper"}
    assert returned_ids[-1] == "pending-body"


@pytest.mark.asyncio
async def test_smart_suggest_endpoint_auto_confirms_fact_from_git_diff(
    client: AsyncClient,
    test_token: str,
    test_db: DatabaseManager,
):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    response = await client.post(
        "/v1/memory/smart-suggest",
        headers=headers,
        json={
            "title": "Smart suggestion",
            "body": "This behavior is derived from the diff.",
            "source": "code_analysis",
            "source_path": "src/app/module.py",
            "memory_class": "fact",
            "derivation_source": "git_diff",
            "task_run_id": "task-endpoint-1",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["memory_item_id"] is not None
    assert payload["pending_approval"] is False

    conn = test_db.connection
    assert conn is not None
    cursor = await conn.execute(
        "SELECT memory_status, review_required, tags_json FROM memory_items WHERE id = ?",
        (payload["memory_item_id"],),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["memory_status"] == "confirmed"
    assert row["review_required"] == 0
    assert json.loads(row["tags_json"])["pending_approval"] is False


@pytest.mark.asyncio
async def test_proposals_for_module_endpoint_returns_pending_items(
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
            id, type, title, body, source, source_path, source_hash,
            trust_level, tags_json, stale, memory_class, memory_status,
            visibility_scope, reusable, review_required
        )
        VALUES
        ('endpoint-pending-1', 'ai_summary', 'Module proposal', 'Body', 'test', 'src/app/module.py', NULL, 4, '{"pending_approval": true}', 0, 'fact', 'pending_review', 'workspace', 0, 1),
        ('endpoint-pending-2', 'ai_summary', 'Body proposal', 'See src/app/module.py for follow-up', 'test', 'docs/notes.md', NULL, 4, '{"pending_approval": true}', 0, 'fact', 'pending_review', 'workspace', 0, 1),
        ('endpoint-confirmed', 'ai_summary', 'Confirmed proposal', 'Body', 'test', 'src/app/module.py', NULL, 4, '{"pending_approval": false}', 0, 'fact', 'confirmed', 'workspace', 1, 0)
        """
    )
    await conn.commit()

    response = await client.post(
        "/v1/memory/proposals-for-module",
        headers=headers,
        json={"module_path": "src/app/module.py"},
    )

    assert response.status_code == 200
    returned_ids = [item["id"] for item in response.json()["items"]]
    assert set(returned_ids) == {"endpoint-pending-1", "endpoint-pending-2"}
