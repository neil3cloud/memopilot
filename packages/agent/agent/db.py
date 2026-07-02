"""SQLite connection manager for MemoPilot.

Manages the SQLite database with:
  - WAL journal mode for concurrent reads during background indexing
  - foreign_keys = ON enforced on every connection
  - async access via aiosqlite
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)


class CorruptedDatabaseError(RuntimeError):
    """Raised when SQLite integrity checks fail."""


async def get_connection(db_path: Path) -> aiosqlite.Connection:
    """Open a new async SQLite connection with required pragmas."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(str(db_path))
    try:
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute("PRAGMA journal_mode = WAL")
        # NORMAL is the recommended pairing with WAL: still durable against app
        # crashes, skips the fsync-per-commit cost of the FULL default. Safe for
        # a local single-user tool where OS/power-loss durability isn't required.
        await conn.execute("PRAGMA synchronous = NORMAL")
        conn.row_factory = aiosqlite.Row
        return conn
    except aiosqlite.DatabaseError:
        await conn.close()
        raise


class DatabaseManager:
    """Manages the SQLite database lifecycle."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._recovery_backup_path: Path | None = None

    async def connect(self) -> aiosqlite.Connection:
        """Get or create the database connection."""
        if self._conn is None:
            self._conn = await self._connect_with_recovery()
        return self._conn

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def connection(self) -> aiosqlite.Connection | None:
        return self._conn

    @property
    def recovery_backup_path(self) -> Path | None:
        return self._recovery_backup_path

    async def _connect_with_recovery(self) -> aiosqlite.Connection:
        conn: aiosqlite.Connection | None = None
        try:
            conn = await get_connection(self.db_path)
            await self._ensure_integrity(conn)
            return conn
        except (aiosqlite.DatabaseError, CorruptedDatabaseError) as exc:
            if conn is not None:
                await conn.close()
            if self._is_in_memory_database():
                raise
            logger.warning("Detected corrupted database at %s: %s", self.db_path, exc)
            return await self._recover_corrupted_database()

    async def _ensure_integrity(self, conn: aiosqlite.Connection) -> None:
        cursor = await conn.execute("PRAGMA integrity_check")
        row = await cursor.fetchone()
        if row is None or row[0] != "ok":
            raise CorruptedDatabaseError("integrity_check_failed")

    async def _recover_corrupted_database(self) -> aiosqlite.Connection:
        backup_path: Path | None = None
        if self.db_path.exists():
            timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
            suffix = f"{self.db_path.suffix}.corrupt-{timestamp}"
            backup_path = self.db_path.with_suffix(suffix)
            self.db_path.replace(backup_path)

        for sidecar in ("-wal", "-shm"):
            sidecar_path = Path(f"{self.db_path}{sidecar}")
            if sidecar_path.exists():
                sidecar_path.unlink()

        self._recovery_backup_path = backup_path
        recovered = await get_connection(self.db_path)
        await self._ensure_integrity(recovered)
        return recovered

    def _is_in_memory_database(self) -> bool:
        return str(self.db_path) == ":memory:"
