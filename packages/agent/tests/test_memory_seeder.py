"""Tests for MemorySeederService."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
import yaml

from agent.config import Config
from agent.db import DatabaseManager
from agent.memory_seeder import MemorySeederService, _extract_conventions, _relative


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def test_config(tmp_workspace: Path) -> Config:
    return Config(
        workspace_path=tmp_workspace,
        memopilot_dir=tmp_workspace / ".memopilot",
        global_dir=tmp_workspace / ".memopilot-global",
    )


@pytest_asyncio.fixture
async def test_db(test_config: Config) -> DatabaseManager:
    from agent.migration_runner import run_migrations
    db = DatabaseManager(Path(":memory:"))
    conn = await db.connect()
    await run_migrations(conn)
    yield db
    await db.close()


import pytest_asyncio


async def _seed_symbol(conn, file_path: str, name: str, kind: str, summary: str, content_hash: str) -> None:
    """Insert a row into file_index and symbols for testing."""
    await conn.execute(
        "INSERT OR IGNORE INTO file_index (file_path, content_hash) VALUES (?, ?)",
        (file_path, content_hash),
    )
    await conn.execute(
        "INSERT INTO symbols (id, file_path, name, kind, summary, signature, content_hash)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name + "_id", file_path, name, kind, summary, f"def {name}()", content_hash),
    )
    await conn.commit()


async def _seed_profile(conn, profile_dict: dict) -> None:
    """Insert a workspace_profile row."""
    await conn.execute(
        "INSERT OR IGNORE INTO workspace_profile (id, profile_yaml) VALUES (?, ?)",
        ("profile_1", yaml.dump(profile_dict)),
    )
    await conn.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_seed_returns_zero_on_empty_workspace(test_config: Config, test_db: DatabaseManager, tmp_workspace: Path):
    seeder = MemorySeederService(config=test_config, db=test_db)
    count = await seeder.seed(str(tmp_workspace))
    assert count == 0


@pytest.mark.asyncio
async def test_seed_inserts_symbol_with_long_summary(test_config: Config, test_db: DatabaseManager, tmp_workspace: Path):
    conn = await test_db.connect()
    await _seed_symbol(
        conn, "app/service.py", "InventoryService", "class",
        "Manages inventory operations including add, remove, and restock of items.",
        "abc123",
    )

    seeder = MemorySeederService(config=test_config, db=test_db)
    count = await seeder.seed(str(tmp_workspace))
    assert count >= 1

    cursor = await conn.execute(
        "SELECT title, body, trust_level, memory_status, memory_class, type, reusable"
        " FROM memory_items WHERE type = 'seeded'"
    )
    rows = await cursor.fetchall()
    assert len(rows) >= 1
    item = rows[0]
    assert item["trust_level"] == 5
    assert item["memory_status"] == "confirmed"
    assert item["memory_class"] == "fact"
    assert item["reusable"] == 1
    assert "InventoryService" in item["title"]


@pytest.mark.asyncio
async def test_seed_skips_short_summaries(test_config: Config, test_db: DatabaseManager, tmp_workspace: Path):
    conn = await test_db.connect()
    await _seed_symbol(
        conn, "utils.py", "helper", "function", "Helps.", "short_hash",
    )

    seeder = MemorySeederService(config=test_config, db=test_db)
    count = await seeder.seed(str(tmp_workspace))
    assert count == 0


@pytest.mark.asyncio
async def test_seed_conventions_from_profile(test_config: Config, test_db: DatabaseManager, tmp_workspace: Path):
    conn = await test_db.connect()
    await _seed_profile(conn, {
        "primary_language": "Python",
        "frameworks": ["FastAPI", "SQLite"],
        "test_commands": ["pytest"],
        "lint_commands": ["ruff check ."],
    })

    seeder = MemorySeederService(config=test_config, db=test_db)
    count = await seeder.seed(str(tmp_workspace))
    assert count >= 3  # language + test + lint

    cursor = await conn.execute(
        "SELECT title FROM memory_items WHERE type = 'seeded'"
    )
    titles = {row["title"] for row in await cursor.fetchall()}
    assert any("Primary language" in t for t in titles)
    assert any("Test command" in t for t in titles)
    assert any("Lint command" in t for t in titles)


@pytest.mark.asyncio
async def test_seed_test_registry(test_config: Config, test_db: DatabaseManager, tmp_workspace: Path):
    conn = await test_db.connect()
    workspace_root = str(tmp_workspace)
    for name in ("tests/test_foo.py", "tests/test_bar.py"):
        await conn.execute(
            "INSERT OR IGNORE INTO file_index (file_path, content_hash, workspace_root)"
            " VALUES (?, ?, ?)",
            (f"{workspace_root}/{name}", "hash123", workspace_root),
        )
    await conn.commit()

    seeder = MemorySeederService(config=test_config, db=test_db)
    count = await seeder.seed(workspace_root)
    assert count >= 1

    cursor = await conn.execute(
        "SELECT body FROM memory_items WHERE title = 'Test files in this workspace'"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert "test_foo.py" in row["body"] or "test_bar.py" in row["body"]


@pytest.mark.asyncio
async def test_seed_idempotent(test_config: Config, test_db: DatabaseManager, tmp_workspace: Path):
    conn = await test_db.connect()
    await _seed_symbol(
        conn, "app.py", "MainApp", "class",
        "The main application class that bootstraps all services and routes.",
        "stable_hash",
    )

    seeder = MemorySeederService(config=test_config, db=test_db)
    first = await seeder.seed(str(tmp_workspace))
    second = await seeder.seed(str(tmp_workspace))
    assert first >= 1
    # Second call inserts 0 because INSERT OR IGNORE skips duplicates
    assert second == 0


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------

def test_extract_conventions_language_and_frameworks():
    profile = {"primary_language": "TypeScript", "frameworks": ["React", "Next.js"]}
    convs = _extract_conventions(profile)
    assert any("TypeScript" in c["body"] for c in convs)
    assert any("React" in c["body"] for c in convs)


def test_extract_conventions_empty_profile():
    assert _extract_conventions({}) == []


def test_relative_path_strips_root(tmp_path: Path):
    full = str(tmp_path / "src" / "app.py")
    assert _relative(full, str(tmp_path)) in ("src/app.py", "src\\app.py")


def test_relative_path_fallback_on_non_subpath(tmp_path: Path):
    full = "/other/path/file.py"
    result = _relative(full, str(tmp_path))
    assert result == full
