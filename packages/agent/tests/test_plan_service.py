"""Tests for plan mode service."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from agent.config import Config
from agent.db import DatabaseManager
from agent.migration_runner import run_migrations
from agent.plan_service import PlanModeService, PlanStep


@pytest.fixture
def config(tmp_path):
    """Minimal config for testing."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return Config(
        workspace_path=workspace,
        memopilot_dir=workspace / ".memopilot",
        global_dir=tmp_path / ".memopilot-global",
    )


@pytest_asyncio.fixture
async def db():
    """Create an in-memory database with all migrations applied."""
    manager = DatabaseManager(Path(":memory:"))
    conn = await manager.connect()
    await conn.execute("PRAGMA foreign_keys = ON")
    await run_migrations(conn)
    yield manager
    await manager.close()


@pytest.mark.asyncio
async def test_store_plan_creates_memory_item(db, config):
    """Storing a plan creates a confirmed decision memory item."""
    service = PlanModeService(config=config, db=db)

    result = await service.store_plan(
        title="Plan: Add expiry warning to inventory",
        steps=[
            PlanStep(step_number=1, description="Add get_near_expiry_items()", target_file="inventory.py"),
            PlanStep(step_number=2, description="Add expiry threshold to settings"),
            PlanStep(step_number=3, description="Surface warning in sell_item()", target_file="inventory.py"),
        ],
        task_description="Add an expiry warning when inventory is near expiry date",
    )

    assert result.plan_id
    assert result.memory_item_id
    assert result.title == "Plan: Add expiry warning to inventory"
    assert len(result.steps) == 3
    assert "expiry warning" in result.raw_text

    # Verify it's stored in the database as a confirmed decision
    conn = await db.connect()
    cursor = await conn.execute(
        "SELECT memory_class, memory_status, trust_level, reusable, review_required "
        "FROM memory_items WHERE id = ?",
        (result.memory_item_id,),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == "decision"      # memory_class
    assert row[1] == "confirmed"     # memory_status
    assert row[2] == 3              # trust_level
    assert row[3] == 1              # reusable
    assert row[4] == 0              # review_required (not needed)


@pytest.mark.asyncio
async def test_store_plan_links_to_task_run(db, config):
    """Storing a plan with a task_run_id updates the task_run record."""
    service = PlanModeService(config=config, db=db)
    conn = await db.connect()

    # Create a task run first
    await conn.execute(
        """INSERT INTO task_runs (id, user_request, task_type, status, mode, created_at, updated_at)
           VALUES ('task-1', 'test request', 'general', 'pending', 'plan', datetime('now'), datetime('now'))""",
    )
    await conn.commit()

    result = await service.store_plan(
        title="Test Plan",
        steps=[PlanStep(step_number=1, description="Do thing")],
        task_description="Test task",
        task_run_id="task-1",
    )

    cursor = await conn.execute(
        "SELECT plan_memory_id FROM task_runs WHERE id = 'task-1'"
    )
    row = await cursor.fetchone()
    assert row[0] == result.memory_item_id


@pytest.mark.asyncio
async def test_recall_plans_returns_confirmed_decisions(db, config):
    """recall_plans_for_context returns confirmed decision plans."""
    service = PlanModeService(config=config, db=db)

    # Store two plans
    await service.store_plan(
        title="Plan A",
        steps=[PlanStep(step_number=1, description="Step A1")],
        task_description="Task A",
    )
    await service.store_plan(
        title="Plan B",
        steps=[PlanStep(step_number=1, description="Step B1")],
        task_description="Task B",
    )

    plans = await service.recall_plans_for_context(limit=5)
    assert len(plans) == 2
    # Most recent first
    assert plans[0].title == "Plan B"
    assert plans[1].title == "Plan A"


@pytest.mark.asyncio
async def test_recall_plans_filters_by_module(db, config):
    """recall_plans_for_context can filter by module path."""
    service = PlanModeService(config=config, db=db)

    await service.store_plan(
        title="Plan: Fix inventory",
        steps=[PlanStep(step_number=1, description="Fix sell_item", target_file="inventory.py")],
        task_description="Fix inventory module",
    )
    await service.store_plan(
        title="Plan: Fix billing",
        steps=[PlanStep(step_number=1, description="Fix charge", target_file="billing.py")],
        task_description="Fix billing module",
    )

    plans = await service.recall_plans_for_context(module_path="inventory")
    assert len(plans) == 1
    assert "inventory" in plans[0].title.lower()


@pytest.mark.asyncio
async def test_plan_compliance_no_warnings_when_compliant(db, config):
    """No warnings when patch files match plan target files."""
    service = PlanModeService(config=config, db=db)

    result = await service.store_plan(
        title="Plan: Update inventory",
        steps=[
            PlanStep(step_number=1, description="Modify service", target_file="inventory.py"),
            PlanStep(step_number=2, description="Add test", target_file="test_inventory.py"),
        ],
        task_description="Update inventory",
    )

    warnings = await service.check_plan_compliance(
        plan_memory_id=result.memory_item_id,
        files_changed=["inventory.py", "test_inventory.py"],
    )
    assert warnings == []


@pytest.mark.asyncio
async def test_plan_compliance_warns_on_unexpected_files(db, config):
    """Warning raised when patch modifies files not in plan."""
    service = PlanModeService(config=config, db=db)

    result = await service.store_plan(
        title="Plan: Update inventory",
        steps=[
            PlanStep(step_number=1, description="Modify service", target_file="inventory.py"),
        ],
        task_description="Update inventory",
    )

    warnings = await service.check_plan_compliance(
        plan_memory_id=result.memory_item_id,
        files_changed=["inventory.py", "billing.py", "auth.py"],
    )
    assert len(warnings) == 1
    assert "Plan contradicted" in warnings[0]
    assert "billing.py" in warnings[0]


@pytest.mark.asyncio
async def test_link_plan_to_patch(db, config):
    """link_plan_to_patch updates patch_attempts record."""
    service = PlanModeService(config=config, db=db)
    conn = await db.connect()

    # Create task run and patch attempt
    await conn.execute(
        """INSERT INTO task_runs (id, user_request, task_type, status, mode, created_at, updated_at)
           VALUES ('task-2', 'test', 'general', 'pending', 'patch', datetime('now'), datetime('now'))""",
    )
    await conn.execute(
        """INSERT INTO patch_attempts (id, task_run_id, patch_path, files_changed_json,
           risk_level, rule_compliance_score, approved, applied, validation_status)
           VALUES ('patch-1', 'task-2', 'inline', '[]', 'low', 1.0, 0, 0, 'pending')""",
    )
    await conn.commit()

    result = await service.store_plan(
        title="Test Plan",
        steps=[PlanStep(step_number=1, description="Do thing")],
        task_description="Test",
    )

    await service.link_plan_to_patch(
        patch_attempt_id="patch-1",
        plan_memory_id=result.memory_item_id,
    )

    cursor = await conn.execute(
        "SELECT plan_memory_id FROM patch_attempts WHERE id = 'patch-1'"
    )
    row = await cursor.fetchone()
    assert row[0] == result.memory_item_id
