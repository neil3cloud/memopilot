"""SQLite connection manager for MemoPilot.

Manages the SQLite database with:
  - WAL journal mode for concurrent reads during background indexing
  - foreign_keys = ON enforced on every connection
  - async access via aiosqlite
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite


async def get_connection(db_path: Path) -> aiosqlite.Connection:
    """Open a new async SQLite connection with required pragmas."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(str(db_path))
    await conn.execute("PRAGMA foreign_keys = ON")
    await conn.execute("PRAGMA journal_mode = WAL")
    conn.row_factory = aiosqlite.Row
    return conn


class DatabaseManager:
    """Manages the SQLite database lifecycle."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> aiosqlite.Connection:
        """Get or create the database connection."""
        if self._conn is None:
            self._conn = await get_connection(self.db_path)
        return self._conn

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def connection(self) -> aiosqlite.Connection | None:
        return self._conn
