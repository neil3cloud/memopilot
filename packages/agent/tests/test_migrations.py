"""Tests for the SQLite migration runner."""

from __future__ import annotations

import aiosqlite
import pytest

from agent.migration_runner import run_migrations


@pytest.mark.asyncio
async def test_migrations_apply_on_fresh_db():
    """Migrations apply cleanly on a fresh in-memory database."""
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("PRAGMA foreign_keys = ON")

    version = await run_migrations(conn)

    assert version == 17

    # Verify core tables exist
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row[0] for row in await cursor.fetchall()]

    expected_tables = [
        "memory_items",
        "file_index",
        "symbols",
        "rules",
        "skills",
        "task_runs",
        "ai_calls",
        "patch_attempts",
        "rule_conflicts",
        "mcp_calls",
        "evidence_sources",
        "document_chunks",
        "context_pack_versions",
        "workspace_profile",
        "provider_capabilities",
        "schema_version",
        "response_cache",
        "cost_ledger",
        "skill_store_versions",
        "optimizer_runs",
        "policy_packs",
        "policy_pack_versions",
        "local_flows",
        "local_flow_runs",
        "workspace_roots",
        "memory_relations",
        "retention_config",
        "recall_traces",
        "audit_events",
        "memory_artifacts",
        "investigation_sessions",
        "tool_mode_sessions",
        "tool_call_events",
        "tool_mode_writebacks",
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
    assert row[0] == 17

    await conn.close()
