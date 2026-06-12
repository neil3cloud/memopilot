"""Migration runner for MemoPilot SQLite database.

Applies numbered SQL migration files sequentially.
Tracks applied migrations in the schema_version table.
"""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def run_migrations(conn: aiosqlite.Connection) -> int:
    """Apply all pending migrations and return current schema version.

    Creates the schema_version table if it doesn't exist.
    Returns the final schema version after applying all migrations.
    """
    # Ensure schema_version tracking table exists
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            version INTEGER PRIMARY KEY,
            filename TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    await conn.commit()

    # Get current version
    cursor = await conn.execute("SELECT MAX(version) FROM _migrations")
    row = await cursor.fetchone()
    current_version = row[0] if row[0] is not None else 0

    # Find and apply pending migrations
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    applied_count = 0

    for migration_file in migration_files:
        # Extract version number from filename (e.g., "001_initial.sql" -> 1)
        version_str = migration_file.stem.split("_")[0]
        try:
            version = int(version_str)
        except ValueError:
            logger.warning(f"Skipping migration with invalid name: {migration_file.name}")
            continue

        if version <= current_version:
            continue

        logger.info(f"Applying migration {migration_file.name} (version {version})")
        sql = migration_file.read_text(encoding="utf-8")

        # Execute migration SQL (may contain multiple statements)
        await conn.executescript(sql)

        # Record applied migration
        await conn.execute(
            "INSERT INTO _migrations (version, filename) VALUES (?, ?)",
            (version, migration_file.name),
        )
        await conn.commit()
        applied_count += 1
        current_version = version

    if applied_count > 0:
        logger.info(f"Applied {applied_count} migration(s). Current version: {current_version}")
    else:
        logger.debug(f"No pending migrations. Current version: {current_version}")

    return current_version
