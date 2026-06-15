"""Tests for structured rejection handler."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from agent.config import Config
from agent.db import DatabaseManager
from agent.migration_runner import run_migrations
from agent.rejection_handler import RejectionHandlerService


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


@pytest_asyncio.fixture
async def setup_patch(db):
    """Create a task_run and patch_attempt for rejection testing."""
    conn = await db.connect()
    await conn.execute(
        """INSERT INTO task_runs (id, user_request, task_type, status, mode, created_at, updated_at)
           VALUES ('task-rej', 'Fix inventory service', 'general', 'success', 'patch',
                   datetime('now'), datetime('now'))""",
    )
    await conn.execute(
        """INSERT INTO patch_attempts
           (id, task_run_id, patch_path, files_changed_json,
            risk_level, rule_compliance_score, approved, applied, validation_status)
           VALUES ('patch-rej', 'task-rej', 'inline', '["inventory.py", "billing.py"]',
                   'low', 0.8, 0, 0, 'pending')""",
    )
    await conn.commit()
    return "patch-rej"


@pytest.mark.asyncio
async def test_wrong_approach_creates_lesson(db, config, setup_patch):
    """Wrong approach rejection creates a lesson memory item."""
    service = RejectionHandlerService(config=config, db=db)
    result = await service.handle_rejection(
        patch_attempt_id=setup_patch,
        category="wrong_approach",
        reason="Used inheritance instead of composition",
    )

    assert result.category == "wrong_approach"
    assert result.memory_item_id is not None
    assert "differently" in result.action_taken

    # Verify memory item
    conn = await db.connect()
    cursor = await conn.execute(
        "SELECT memory_class, memory_status, body FROM memory_items WHERE id = ?",
        (result.memory_item_id,),
    )
    row = await cursor.fetchone()
    assert row[0] == "lesson"
    assert row[1] == "confirmed"
    assert "inheritance" in row[2]


@pytest.mark.asyncio
async def test_missed_business_rule_creates_pending_instruction(db, config, setup_patch):
    """Missed business rule creates a pending instruction for review."""
    service = RejectionHandlerService(config=config, db=db)
    result = await service.handle_rejection(
        patch_attempt_id=setup_patch,
        category="missed_business_rule",
        reason="Expired items cannot be sold",
    )

    assert result.category == "missed_business_rule"
    assert result.memory_item_id is not None
    assert "pending" in result.action_taken.lower() or "instruction" in result.action_taken.lower()

    conn = await db.connect()
    cursor = await conn.execute(
        "SELECT memory_class, memory_status, review_required FROM memory_items WHERE id = ?",
        (result.memory_item_id,),
    )
    row = await cursor.fetchone()
    assert row[0] == "instruction"
    assert row[1] == "pending_review"
    assert row[2] == 1


@pytest.mark.asyncio
async def test_wrong_scope_stores_restriction(db, config, setup_patch):
    """Wrong scope rejection stores file restriction."""
    service = RejectionHandlerService(config=config, db=db)
    result = await service.handle_rejection(
        patch_attempt_id=setup_patch,
        category="wrong_file",
        reason="Should not have touched billing.py",
    )

    assert result.category == "wrong_file"
    assert result.memory_item_id is not None

    conn = await db.connect()
    cursor = await conn.execute(
        "SELECT body, tags_json FROM memory_items WHERE id = ?",
        (result.memory_item_id,),
    )
    row = await cursor.fetchone()
    assert "billing.py" in row[0]
    tags = json.loads(row[1])
    assert "inventory.py" in tags["restricted_files"] or "billing.py" in tags["restricted_files"]


@pytest.mark.asyncio
async def test_broke_behavior_stores_evidence(db, config, setup_patch):
    """Broke behavior rejection stores regression evidence with suggestion."""
    service = RejectionHandlerService(config=config, db=db)
    result = await service.handle_rejection(
        patch_attempt_id=setup_patch,
        category="broke_existing_behavior",
        reason="Checkout flow no longer calculates tax correctly",
    )

    assert result.category == "broke_existing_behavior"
    assert result.suggestion is not None
    assert "test" in result.suggestion.lower()

    conn = await db.connect()
    cursor = await conn.execute(
        "SELECT memory_class, body FROM memory_items WHERE id = ?",
        (result.memory_item_id,),
    )
    row = await cursor.fetchone()
    assert row[0] == "fact"
    assert "tax" in row[1]


@pytest.mark.asyncio
async def test_incomplete_stores_feedback(db, config, setup_patch):
    """Incomplete rejection stores feedback for Plan mode."""
    service = RejectionHandlerService(config=config, db=db)
    result = await service.handle_rejection(
        patch_attempt_id=setup_patch,
        category="incomplete",
        reason="Missing error handling for edge cases",
    )

    assert result.category == "incomplete"
    assert result.suggestion is not None
    assert "plan" in result.suggestion.lower()


@pytest.mark.asyncio
async def test_get_rejection_constraints(db, config, setup_patch):
    """Rejection constraints can be recalled for context injection."""
    service = RejectionHandlerService(config=config, db=db)

    # Create some rejection memories
    await service.handle_rejection(
        patch_attempt_id=setup_patch,
        category="wrong_approach",
        reason="Do not use global state",
    )
    await service.handle_rejection(
        patch_attempt_id=setup_patch,
        category="wrong_file",
        reason="Keep changes scoped to inventory",
    )

    constraints = await service.get_rejection_constraints()
    assert len(constraints) >= 2
    assert all("[PRIOR REJECTION]" in c for c in constraints)
