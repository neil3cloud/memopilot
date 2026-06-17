"""Shared test fixtures for MemoPilot agent tests."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from agent.api import app, configure
from agent.config import Config
from agent.db import DatabaseManager


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory."""
    workspace = tmp_path / "test-workspace"
    workspace.mkdir()
    return workspace


@pytest.fixture
def test_config(tmp_workspace: Path) -> Config:
    """Create a test configuration with temporary paths."""
    return Config(
        workspace_path=tmp_workspace,
        memopilot_dir=tmp_workspace / ".memopilot",
        global_dir=tmp_workspace / ".memopilot-global",
    )


@pytest_asyncio.fixture
async def test_db(test_config: Config) -> DatabaseManager:
    """Create an in-memory database manager for tests."""
    from agent.migration_runner import run_migrations
    db = DatabaseManager(Path(":memory:"))
    conn = await db.connect()
    await run_migrations(conn)
    yield db
    await db.close()


@pytest.fixture
def test_token() -> str:
    """Fixed test token for authentication."""
    return "test-token-" + "a" * 48


@pytest_asyncio.fixture
async def client(test_config: Config, test_db: DatabaseManager, test_token: str) -> AsyncClient:
    """Create an authenticated test HTTP client."""
    with patch.dict(os.environ, {"MEMOPILOT_TOKEN": test_token}):
        configure(test_config, test_db)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


@pytest_asyncio.fixture
async def db_with_task_history(test_db: DatabaseManager) -> DatabaseManager:
    """Pre-populate with 5 task runs including 2 failures for billing module."""
    conn = await test_db.connect()
    for i in range(5):
        status = "failed" if i < 2 else "completed"
        model = "gpt-4o-mini" if i < 3 else "claude-3.5-sonnet"
        await conn.execute(
            """INSERT INTO task_runs (id, status, selected_model, context_pack_path, created_at, workspace_root)
               VALUES (?, ?, ?, ?, datetime('now', ? || ' days'), ?)""",
            (f"tr-hist-{i}", status, model, f"/packs/billing-{i}.md", f"-{i}", "/workspace"),
        )
    await conn.commit()
    return test_db


@pytest_asyncio.fixture
async def context_pack_with_stale_items(test_db: DatabaseManager) -> DatabaseManager:
    """5 memory items, 3 stale, for testing stale exclusion surfacing."""
    conn = await test_db.connect()
    for i in range(5):
        stale = i < 3
        await conn.execute(
            """INSERT INTO memory_items (id, title, body, memory_class, memory_status, trust_level, source_path, source_hash, created_at, updated_at)
               VALUES (?, ?, ?, 'fact', 'confirmed', 3, ?, ?, datetime('now', '-10 days'), datetime('now', '-10 days'))""",
            (
                f"mem-stale-{i}",
                f"Memory item {i}",
                f"Body {i}",
                f"app/services/mod{i}.py",
                f"hash-{'old' if stale else 'current'}-{i}",
            ),
        )
    await conn.commit()
    return test_db


@pytest_asyncio.fixture
async def patch_attempt_high_risk(test_db: DatabaseManager) -> DatabaseManager:
    """patch_attempt touching billing module; classified as HIGH risk."""
    conn = await test_db.connect()
    await conn.execute(
        """INSERT INTO task_runs (id, status, selected_model, context_pack_path, workspace_root)
           VALUES ('tr-high-risk', 'completed', 'gpt-4o', '/packs/billing.md', '/workspace')"""
    )
    await conn.execute(
        """INSERT INTO patch_attempts (id, task_run_id, patch_path, validation_status, approval_tier)
           VALUES ('pa-high-risk', 'tr-high-risk', '/patches/billing.patch', 'passed', 'high')"""
    )
    await conn.commit()
    return test_db


@pytest.fixture
def validation_with_pre_existing(tmp_path: Path) -> Path:
    """Workspace with 2 pre-existing test failures before any patch."""
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    # Create a conftest that defines two always-failing tests as "pre-existing"
    (test_dir / "test_legacy.py").write_text(
        "import pytest\n\n"
        "def test_legacy_import_path():\n"
        "    assert False, 'pre-existing failure'\n\n"
        "def test_deprecated_endpoint():\n"
        "    assert False, 'pre-existing failure'\n",
        encoding="utf-8",
    )
    (test_dir / "test_good.py").write_text(
        "def test_passing():\n    assert True\n",
        encoding="utf-8",
    )
    return tmp_path
