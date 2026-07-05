"""Tests for the SQLite migration runner."""

from __future__ import annotations

import aiosqlite
import pytest

from agent.migration_runner import run_migrations
from agent.migration_runner import MIGRATIONS_DIR


def _latest_migration_version() -> int:
    versions: list[int] = []
    for path in MIGRATIONS_DIR.glob("*.sql"):
        try:
            versions.append(int(path.stem.split("_", 1)[0]))
        except ValueError:
            continue
    return max(versions) if versions else 0


@pytest.mark.asyncio
async def test_migrations_apply_on_fresh_db():
    """Migrations apply cleanly on a fresh in-memory database."""
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("PRAGMA foreign_keys = ON")

    version = await run_migrations(conn)

    assert version == _latest_migration_version()

    # Verify core tables exist
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row[0] for row in await cursor.fetchall()]

    # Core tables that must always exist after a full migration run.
    expected_tables = [
        # Schema 001 — foundation
        "memory_items",
        "file_index",
        "symbols",
        "rules",
        "skills",
        "task_runs",
        "ai_calls",
        "schema_version",
        "response_cache",
        "cost_ledger",
        # Feature tables
        "workspace_roots",
        "workspace_profile",
        "audit_events",
        "tool_mode_sessions",
        "tool_call_events",
        "tool_mode_writebacks",
        "symbol_relationships",
        "commit_history",
        "commit_file_changes",
        "vectors",
        "vector_index_status",
        "vector_config",
        "ingested_sessions",
        "memory_relations",
        "recall_traces",
        "retention_config",
        "context_pack_versions",
        "provider_capabilities",
    ]

    for table in expected_tables:
        assert table in tables, f"Table '{table}' not found in database"

    await conn.close()


@pytest.mark.asyncio
async def test_migrations_are_idempotent():
    """Running migrations twice does not fail or duplicate data."""
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("PRAGMA foreign_keys = ON")

    version1 = await run_migrations(conn)
    version2 = await run_migrations(conn)

    assert version1 == version2
    await conn.close()


@pytest.mark.asyncio
async def test_fts_table_created():
    """FTS5 virtual table for memory search is created."""
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("PRAGMA foreign_keys = ON")

    await run_migrations(conn)

    # FTS table should be queryable
    cursor = await conn.execute("SELECT * FROM memory_fts WHERE memory_fts MATCH 'test'")
    results = await cursor.fetchall()
    assert results == []  # No data yet, but query succeeds

    await conn.close()


@pytest.mark.asyncio
async def test_schema_version_table_populated():
    """schema_version table has a row after migration."""
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("PRAGMA foreign_keys = ON")

    await run_migrations(conn)

    cursor = await conn.execute("SELECT version FROM schema_version")
    row = await cursor.fetchone()
    assert row is not None
    assert isinstance(row[0], int)
    assert row[0] >= 1

    await conn.close()
