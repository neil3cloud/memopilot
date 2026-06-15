"""Tests for task history pattern detection."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from agent.config import Config
from agent.db import DatabaseManager
from agent.migration_runner import run_migrations
from agent.task_pattern_detector import TaskPatternDetector


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


async def _insert_task_run(
    db: DatabaseManager,
    *,
    task_run_id: str,
    patch_attempt_id: str,
    user_request: str,
    file_path: str,
    status: str,
    validation_status: str,
    workspace_root: str,
    days_ago: int,
    selected_model: str | None = None,
    actual_cost: float | None = None,
    estimated_cost: float | None = None,
    rejection_reason: str | None = None,
    routing_escalation_source: str | None = None,
    routing_base_tier: str | None = None,
) -> None:
    conn = await db.connect()
    await conn.execute(
        """
        INSERT INTO task_runs (
            id,
            user_request,
            task_type,
            mode,
            risk_level,
            context_pack_path,
            selected_model,
            estimated_cost,
            actual_cost,
            status,
            workspace_root,
            routing_escalation_source,
            routing_base_tier,
            created_at,
            updated_at
        )
        VALUES (?, ?, 'general', 'patch', 'medium', ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', ?), datetime('now', ?))
        """,
        (
            task_run_id,
            user_request,
            file_path,
            selected_model,
            estimated_cost,
            actual_cost,
            status,
            workspace_root,
            routing_escalation_source,
            routing_base_tier,
            f'-{days_ago} days',
            f'-{days_ago} days',
        ),
    )
    await conn.execute(
        """
        INSERT INTO patch_attempts (
            id,
            task_run_id,
            patch_path,
            files_changed_json,
            risk_level,
            rule_compliance_score,
            approved,
            applied,
            validation_status,
            rejection_reason
        )
        VALUES (?, ?, 'inline.diff', json_array(?), 'medium', 0.75, 0, 0, ?, ?)
        """,
        (patch_attempt_id, task_run_id, file_path, validation_status, rejection_reason),
    )
    await conn.commit()


@pytest.mark.asyncio
async def test_detect_patterns_returns_patterns_for_frequently_patched_modules(db, config):
    service = TaskPatternDetector(config=config, db=db)
    workspace_root = str(config.workspace_path)

    await _insert_task_run(
        db,
        task_run_id='billing-1',
        patch_attempt_id='patch-billing-1',
        user_request='Fix billing retries',
        file_path='src\\billing.py',
        status='failed',
        validation_status='failed',
        workspace_root=workspace_root,
        days_ago=2,
        selected_model='gpt-4o-mini',
        actual_cost=0.01,
    )
    await _insert_task_run(
        db,
        task_run_id='billing-2',
        patch_attempt_id='patch-billing-2',
        user_request='Adjust billing totals',
        file_path='src\\billing.py',
        status='failed',
        validation_status='rejected',
        workspace_root=workspace_root,
        days_ago=4,
        selected_model='gpt-4o-mini',
        actual_cost=0.02,
        rejection_reason='Regression in invoice totals',
    )
    await _insert_task_run(
        db,
        task_run_id='billing-3',
        patch_attempt_id='patch-billing-3',
        user_request='Refine billing messages',
        file_path='src\\billing.py',
        status='completed',
        validation_status='passed',
        workspace_root=workspace_root,
        days_ago=6,
        selected_model='gpt-4o-mini',
        actual_cost=0.01,
    )
    await _insert_task_run(
        db,
        task_run_id='auth-1',
        patch_attempt_id='patch-auth-1',
        user_request='Untangle auth middleware',
        file_path='src\\auth.py',
        status='completed',
        validation_status='passed',
        workspace_root=workspace_root,
        days_ago=1,
        selected_model='claude-3.5-sonnet',
        actual_cost=0.12,
        routing_escalation_source='recent_file_failures',
        routing_base_tier='cheap_cloud',
    )
    await _insert_task_run(
        db,
        task_run_id='auth-2',
        patch_attempt_id='patch-auth-2',
        user_request='Fix auth edge cases',
        file_path='src\\auth.py',
        status='completed',
        validation_status='passed',
        workspace_root=workspace_root,
        days_ago=3,
        selected_model='claude-3.5-sonnet',
        actual_cost=0.15,
        routing_escalation_source='recent_file_failures',
        routing_base_tier='cheap_cloud',
    )

    patterns = await service.detect_patterns(workspace_root)
    pattern_map = {(pattern.pattern_type, pattern.context_path): pattern for pattern in patterns}

    frequent = pattern_map[('frequent_failures', 'src/billing.py')]
    assert frequent.details['patch_count'] == 3
    assert frequent.details['failure_count'] == 2
    assert frequent.details['failure_rate'] == pytest.approx(0.67, rel=0, abs=0.01)
    assert 'billing.py' in frequent.suggestion

    escalation = pattern_map[('model_escalation', 'src/auth.py')]
    assert escalation.details['task_count'] == 2
    assert escalation.details['escalated_count'] == 2
    assert escalation.details['models'] == ['claude-3.5-sonnet']

    conn = await db.connect()
    cursor = await conn.execute('SELECT COUNT(*) FROM task_patterns WHERE workspace_root = ?', (workspace_root,))
    row = await cursor.fetchone()
    assert row[0] == 2


@pytest.mark.asyncio
async def test_find_similar_tasks_returns_matching_recent_tasks(db, config):
    service = TaskPatternDetector(config=config, db=db)
    workspace_root = str(config.workspace_path)

    await _insert_task_run(
        db,
        task_run_id='recent-1',
        patch_attempt_id='patch-recent-1',
        user_request='Fix billing retry loop',
        file_path='src\\billing.py',
        status='completed',
        validation_status='passed',
        workspace_root=workspace_root,
        days_ago=1,
        selected_model='gpt-4o-mini',
        actual_cost=0.02,
    )
    await _insert_task_run(
        db,
        task_run_id='recent-2',
        patch_attempt_id='patch-recent-2',
        user_request='Patch billing discount bug',
        file_path='src\\billing.py',
        status='failed',
        validation_status='failed',
        workspace_root=workspace_root,
        days_ago=2,
        selected_model='gpt-4o',
        actual_cost=0.08,
        rejection_reason='Missed discount edge case',
    )
    await _insert_task_run(
        db,
        task_run_id='recent-3',
        patch_attempt_id='patch-recent-3',
        user_request='Clean up billing logging',
        file_path='src\\billing.py',
        status='completed',
        validation_status='passed',
        workspace_root=workspace_root,
        days_ago=3,
        selected_model='gpt-4o-mini',
        estimated_cost=0.03,
    )
    await _insert_task_run(
        db,
        task_run_id='other-1',
        patch_attempt_id='patch-other-1',
        user_request='Update inventory safety stock',
        file_path='src\\inventory.py',
        status='completed',
        validation_status='passed',
        workspace_root=workspace_root,
        days_ago=1,
        selected_model='gpt-4o-mini',
        actual_cost=0.01,
    )

    similar = await service.find_similar_tasks('src/billing.py', workspace_root, limit=3)

    assert [item.task_id for item in similar] == ['recent-1', 'recent-2', 'recent-3']
    assert similar[0].user_request == 'Fix billing retry loop'
    assert similar[1].rejection_reason == 'Missed discount edge case'
    assert similar[2].cost_usd == pytest.approx(0.03)
    assert all(item.model_used is not None for item in similar)


@pytest.mark.asyncio
async def test_empty_history_returns_no_patterns(db, config):
    service = TaskPatternDetector(config=config, db=db)
    workspace_root = str(config.workspace_path)

    patterns = await service.detect_patterns(workspace_root)
    similar = await service.find_similar_tasks('src/unknown.py', workspace_root)

    assert patterns == []
    assert similar == []
