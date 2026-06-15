"""Tests for investigation-to-plan loop behavior."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from agent.api import app, configure
from agent.config import Config
from agent.db import DatabaseManager
from agent.investigation_service import InvestigationService
from agent.migration_runner import run_migrations


@pytest.fixture
def config(tmp_path: Path) -> Config:
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


@pytest.fixture
def test_token() -> str:
    return "test-token-" + "b" * 48


@pytest_asyncio.fixture
async def client(config: Config, db: DatabaseManager, test_token: str) -> AsyncClient:
    with patch.dict(os.environ, {"MEMOPILOT_TOKEN": test_token}):
        configure(config, db)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


async def _seed_investigation(conn, workspace_root: str) -> str:
    session_id = "investigation-plan-session"
    await conn.execute(
        """
        INSERT INTO investigation_sessions
        (id, title, description, mode, status, workspace_root, created_at, updated_at)
        VALUES (?, ?, ?, 'investigation', 'open', ?, datetime('now'), datetime('now'))
        """,
        (
            session_id,
            "Retry timeout regression",
            "Investigate retry failures before generating a patch",
            workspace_root,
        ),
    )
    await conn.execute(
        """
        INSERT INTO task_runs
        (id, user_request, task_type, status, mode, created_at, updated_at,
         investigation_session_id, workspace_root)
        VALUES (?, ?, 'bugfix', 'pending', 'investigation', datetime('now'), datetime('now'), ?, ?)
        """,
        (
            "task-run-investigation",
            "Investigate retry timeout regression",
            session_id,
            workspace_root,
        ),
    )
    await conn.execute(
        """
        INSERT INTO evidence_sources
        (id, task_run_id, investigation_session_id, source_type, source_path, trust_level,
         extraction_method, extracted_findings_json, approved, workspace_root)
        VALUES (?, ?, ?, 'existing_code', ?, 5, 'text', ?, 0, ?)
        """,
        (
            "evidence-root-cause",
            "task-run-investigation",
            session_id,
            "app/service.py",
            json.dumps(
                {
                    "findings": [
                        (
                            "Root cause: app/service.py retries without preserving "
                            "the timeout fallback."
                        ),
                        (
                            "caller app/api.py invokes retry_service() without "
                            "guarding timeout errors."
                        ),
                        (
                            "Acceptance criteria: add regression coverage in "
                            "tests/test_service.py for timeout fallback behavior."
                        ),
                    ],
                    "extraction_status": "ok",
                    "redacted_values": 0,
                }
            ),
            workspace_root,
        ),
    )
    await conn.execute(
        """
        INSERT INTO evidence_sources
        (id, task_run_id, investigation_session_id, source_type, source_path, trust_level,
         extraction_method, extracted_findings_json, approved, workspace_root)
        VALUES (?, ?, ?, 'text_log', ?, 4, 'text', ?, 0, ?)
        """,
        (
            "evidence-impact",
            "task-run-investigation",
            session_id,
            "logs/retry.log",
            json.dumps(
                {
                    "findings": [
                        (
                            "Timeout regression reproduces when retry_service() "
                            "is called from app/api.py."
                        ),
                        (
                            "Expected behavior: preserve timeout fallback and "
                            "keep callers stable."
                        ),
                    ],
                    "extraction_status": "ok",
                    "redacted_values": 0,
                }
            ),
            workspace_root,
        ),
    )
    await conn.commit()
    return session_id


@pytest.mark.asyncio
async def test_generate_plan_from_findings_creates_plan_from_evidence(db, config):
    conn = await db.connect()
    session_id = await _seed_investigation(conn, str(config.workspace_path))
    service = InvestigationService(config=config, db=db)

    result = await service.generate_plan_from_findings(investigation_session_id=session_id)

    assert result.title == "Plan from investigation: Retry timeout regression"
    assert result.memory_item_id
    assert any(step.target_file == "app/service.py" for step in result.steps)
    assert any("validation" in step.description.lower() for step in result.steps)

    cursor = await conn.execute(
        "SELECT memory_class, memory_status FROM memory_items WHERE id = ?",
        (result.memory_item_id,),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["memory_class"] == "decision"
    assert row["memory_status"] == "confirmed"

    cursor = await conn.execute(
        "SELECT plan_memory_id FROM task_runs WHERE id = 'task-run-investigation'"
    )
    linked = await cursor.fetchone()
    assert linked is not None
    assert linked["plan_memory_id"] == result.memory_item_id


@pytest.mark.asyncio
async def test_store_investigation_memory_persists_findings(db, config):
    conn = await db.connect()
    session_id = await _seed_investigation(conn, str(config.workspace_path))
    service = InvestigationService(config=config, db=db)

    memory_ids = await service.store_investigation_memory(investigation_session_id=session_id)

    assert len(memory_ids) == 3
    cursor = await conn.execute(
        """
        SELECT title, memory_class, trust_level, memory_status
        FROM memory_items
        WHERE source = 'investigation'
        ORDER BY title
        """
    )
    rows = await cursor.fetchall()
    assert len(rows) == 3

    row_map = {row["title"]: row for row in rows}
    root_cause_title = "Root cause for investigation: Retry timeout regression"
    impacted_scope_title = "Impacted scope for investigation: Retry timeout regression"
    acceptance_title = "Acceptance criteria for investigation: Retry timeout regression"

    assert row_map[root_cause_title]["memory_class"] == "fact"
    assert row_map[root_cause_title]["trust_level"] == 4
    assert row_map[impacted_scope_title]["memory_class"] == "fact"
    assert row_map[impacted_scope_title]["trust_level"] == 4
    assert row_map[acceptance_title]["memory_class"] == "instruction"
    assert row_map[acceptance_title]["trust_level"] == 3
    assert {row["memory_status"] for row in rows} == {"confirmed"}


@pytest.mark.asyncio
async def test_plan_from_findings_endpoint_returns_plan(
    client: AsyncClient,
    config: Config,
    db: DatabaseManager,
    test_token: str,
):
    conn = await db.connect()
    session_id = await _seed_investigation(conn, str(config.workspace_path))

    response = await client.post(
        "/v1/investigation/plan-from-findings",
        headers={"X-Agent-Token": test_token},
        json={"investigation_session_id": session_id},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "Plan from investigation: Retry timeout regression"
    assert any(step.get("target_file") == "app/service.py" for step in payload["steps"])
    assert "Acceptance criteria:" in payload["raw_text"]
