"""Memory backup and restore for MemoPilot.

Backup: WAL checkpoint, copy DB + rules + templates, write manifest, exclude secrets.
Restore: verify manifest, replace DB, restore assets, rebuild FTS.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from .db import get_connection

logger = logging.getLogger(__name__)

EXCLUDED_PATTERNS = [".secrets.baseline", "providers.yaml", "*.key", "*.pem"]


class BackupManifest:
    def __init__(self, data: dict):
        self.schema_version = data.get("schema_version")
        self.memory_items_count = data.get("memory_items_count", 0)
        self.rules_count = data.get("rules_count", 0)
        self.skills_count = data.get("skills_count", 0)
        self.created_at = data.get("created_at")
        self.db_hash = data.get("db_hash")


async def create_backup(
    conn: aiosqlite.Connection,
    memopilot_dir: Path,
    snapshots_dir: Path,
) -> Path:
    """Create a full backup of the MemoPilot memory database and assets."""
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    backup_dir = snapshots_dir / f"backup-{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    try:
        await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        await conn.commit()
    except aiosqlite.DatabaseError:
        logger.debug("Skipping WAL checkpoint for backup", exc_info=True)

    db_path = memopilot_dir / "memory" / "memopilot.db"
    backup_db = backup_dir / "memopilot.db"
    if db_path.exists():
        shutil.copy2(str(db_path), str(backup_db))
    else:
        target_conn = await aiosqlite.connect(str(backup_db))
        try:
            await conn.backup(target_conn)
            await target_conn.commit()
        finally:
            await target_conn.close()

    rules_dir = memopilot_dir / "rules"
    if rules_dir.exists():
        _copytree_filtered(rules_dir, backup_dir / "rules")

    templates_dir = memopilot_dir / "context-templates"
    if templates_dir.exists():
        _copytree_filtered(templates_dir, backup_dir / "context-templates")

    memory_count = await _count_rows(conn, "memory_items")
    rules_count = await _count_rows(conn, "rules")
    skills_count = await _count_rows(conn, "skills")
    schema_ver = await _read_schema_version(conn)

    db_hash = ""
    if backup_db.exists():
        db_hash = hashlib.sha256(backup_db.read_bytes()).hexdigest()

    manifest = {
        "schema_version": schema_ver,
        "memory_items_count": memory_count,
        "rules_count": rules_count,
        "skills_count": skills_count,
        "created_at": datetime.now(UTC).isoformat(),
        "db_hash": db_hash,
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    logger.info("Backup created at %s", backup_dir)
    return backup_dir


async def restore_backup(
    backup_dir: Path,
    memopilot_dir: Path,
    conn: aiosqlite.Connection | None = None,
) -> bool:
    """Restore from a backup directory. Returns True on success."""
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.exists():
        logger.error("No manifest.json found in backup")
        return False

    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = BackupManifest(manifest_data)

    backup_db = backup_dir / "memopilot.db"
    if backup_db.exists() and manifest.db_hash:
        actual_hash = hashlib.sha256(backup_db.read_bytes()).hexdigest()
        if actual_hash != manifest.db_hash:
            logger.error("Backup database hash mismatch")
            return False

    target_db = memopilot_dir / "memory" / "memopilot.db"
    target_db.parent.mkdir(parents=True, exist_ok=True)

    is_in_memory = conn is not None and await _connection_is_in_memory(conn)
    if backup_db.exists():
        if is_in_memory:
            source_conn = await aiosqlite.connect(str(backup_db))
            try:
                await source_conn.backup(conn)
                await conn.commit()
            finally:
                await source_conn.close()
        else:
            for ext in ("-wal", "-shm"):
                sidecar = Path(f"{target_db}{ext}")
                if sidecar.exists():
                    sidecar.unlink()
            shutil.copy2(str(backup_db), str(target_db))

    backup_rules = backup_dir / "rules"
    if backup_rules.exists():
        target_rules = memopilot_dir / "rules"
        if target_rules.exists():
            shutil.rmtree(str(target_rules))
        _copytree_filtered(backup_rules, target_rules)

    backup_templates = backup_dir / "context-templates"
    if backup_templates.exists():
        target_templates = memopilot_dir / "context-templates"
        if target_templates.exists():
            shutil.rmtree(str(target_templates))
        _copytree_filtered(backup_templates, target_templates)

    if is_in_memory and conn is not None:
        await conn.execute("INSERT INTO memory_fts(memory_fts) VALUES('rebuild')")
        await conn.commit()
    else:
        new_conn = await get_connection(target_db)
        try:
            await new_conn.execute("INSERT INTO memory_fts(memory_fts) VALUES('rebuild')")
            await new_conn.commit()
        finally:
            await new_conn.close()

    logger.info("Restore completed from %s", backup_dir)
    return True


async def _count_rows(conn: aiosqlite.Connection, table_name: str) -> int:
    try:
        cursor = await conn.execute(f"SELECT COUNT(*) FROM {table_name}")
        row = await cursor.fetchone()
        return int(row[0] if row is not None else 0)
    except aiosqlite.DatabaseError:
        return 0


async def _read_schema_version(conn: aiosqlite.Connection) -> int:
    try:
        cursor = await conn.execute(
            "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        return int(row[0] if row is not None and row[0] is not None else 0)
    except aiosqlite.DatabaseError:
        return 0


async def _connection_is_in_memory(conn: aiosqlite.Connection) -> bool:
    cursor = await conn.execute("PRAGMA database_list")
    rows = await cursor.fetchall()
    for row in rows:
        if row[1] == "main":
            location = str(row[2] or "").strip()
            return location in {"", ":memory:"}
    return False


def _copytree_filtered(source: Path, destination: Path) -> None:
    def ignore(_: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        for name in names:
            if any(fnmatch.fnmatch(name, pattern) for pattern in EXCLUDED_PATTERNS):
                ignored.add(name)
        return ignored

    shutil.copytree(str(source), str(destination), dirs_exist_ok=True, ignore=ignore)
