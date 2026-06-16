"""Memory seeder — populates memory_items from workspace index on first install.

Seeds three categories without any LLM call:
  1. Symbol summaries (classes/functions with non-trivial summaries)
  2. Project conventions extracted from workspace profile
  3. Test file registry

All seeded items are trust_level=5, memory_status='confirmed'.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from .config import Config
    from .db import DatabaseManager


class MemorySeederService:
    def __init__(self, *, config: "Config", db: "DatabaseManager") -> None:
        self._config = config
        self._db = db

    async def seed(self, workspace_root: str) -> int:
        """Seed memory_items from indexed workspace data.

        Returns count of items inserted (skips existing via INSERT OR IGNORE).
        """
        conn = await self._db.connect()
        seeded = 0

        seeded += await self._seed_symbols(conn, workspace_root)
        seeded += await self._seed_conventions(conn, workspace_root)
        seeded += await self._seed_test_registry(conn, workspace_root)

        await conn.commit()
        return seeded

    # ------------------------------------------------------------------
    # Symbol docstrings / summaries
    # ------------------------------------------------------------------

    async def _seed_symbols(self, conn, workspace_root: str) -> int:
        cursor = await conn.execute(
            """
            SELECT s.name, s.kind, s.file_path, s.summary, s.signature,
                   f.content_hash
            FROM symbols s
            JOIN file_index f ON f.file_path = s.file_path
            WHERE s.summary IS NOT NULL
              AND length(trim(s.summary)) > 30
              AND s.kind IN ('class', 'function', 'module')
              AND NOT EXISTS (
                  SELECT 1 FROM memory_items m
                  WHERE m.source_path = s.file_path
                    AND m.source_hash = f.content_hash
                    AND m.type = 'seeded'
              )
            LIMIT 100
            """,
        )
        rows = await cursor.fetchall()

        count = 0
        for row in rows:
            short_path = _relative(row["file_path"], workspace_root)
            title = f"{row['kind'].title()}: {row['name']} ({short_path})"
            body = (
                f"{row['signature']}\n\n{row['summary']}"
                if row["signature"]
                else row["summary"]
            )
            await conn.execute(
                """
                INSERT OR IGNORE INTO memory_items (
                    id, type, title, body,
                    source, source_path, source_hash,
                    memory_class, memory_status, trust_level,
                    tags_json, stale, reusable, review_required,
                    workspace_root, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,0,1,0,?,datetime('now'),datetime('now'))
                """,
                (
                    _uid(), "seeded", title, body,
                    "workspace_index", row["file_path"], row["content_hash"],
                    "fact", "confirmed", 5,
                    "[]", workspace_root,
                ),
            )
            count += 1
        return count

    # ------------------------------------------------------------------
    # Workspace profile conventions
    # ------------------------------------------------------------------

    async def _seed_conventions(self, conn, workspace_root: str) -> int:
        cursor = await conn.execute(
            "SELECT profile_yaml FROM workspace_profile ORDER BY updated_at DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        if not row:
            return 0

        try:
            profile: dict = yaml.safe_load(row["profile_yaml"]) or {}
        except Exception:
            return 0

        count = 0
        for conv in _extract_conventions(profile):
            await conn.execute(
                """
                INSERT OR IGNORE INTO memory_items (
                    id, type, title, body,
                    source, memory_class, memory_status, trust_level,
                    tags_json, stale, reusable, review_required,
                    workspace_root, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,0,1,0,?,datetime('now'),datetime('now'))
                """,
                (
                    _uid(), "seeded", conv["title"], conv["body"],
                    "workspace_profile",
                    "fact", "confirmed", 5,
                    "[]", workspace_root,
                ),
            )
            count += 1
        return count

    # ------------------------------------------------------------------
    # Test file registry
    # ------------------------------------------------------------------

    async def _seed_test_registry(self, conn, workspace_root: str) -> int:
        cursor = await conn.execute(
            """
            SELECT file_path FROM file_index
            WHERE (file_path LIKE '%/test_%' OR file_path LIKE '%_test.py'
                   OR file_path LIKE '%\\test_%' OR file_path LIKE '%\\_test.py')
              AND (workspace_root = ? OR workspace_root IS NULL)
            ORDER BY file_path LIMIT 20
            """,
            (workspace_root,),
        )
        rows = await cursor.fetchall()
        if not rows:
            return 0

        paths = "\n".join(
            f"- {_relative(r['file_path'], workspace_root)}" for r in rows
        )
        body = f"Known test files:\n{paths}"
        title = "Test files in this workspace"

        # Only insert once per workspace_root
        exists = await conn.execute(
            "SELECT 1 FROM memory_items WHERE title=? AND workspace_root=? AND type='seeded'",
            (title, workspace_root),
        )
        if await exists.fetchone():
            return 0

        await conn.execute(
            """
            INSERT OR IGNORE INTO memory_items (
                id, type, title, body,
                source, memory_class, memory_status, trust_level,
                tags_json, stale, reusable, review_required,
                workspace_root, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,0,1,0,?,datetime('now'),datetime('now'))
            """,
            (
                _uid(), "seeded", title, body,
                "workspace_index",
                "fact", "confirmed", 5,
                "[]", workspace_root,
            ),
        )
        return 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return str(uuid.uuid4())


def _relative(path: str, root: str) -> str:
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return path


def _extract_conventions(profile: dict) -> list[dict]:
    out: list[dict] = []

    lang = profile.get("primary_language") or profile.get("language")
    if lang:
        fwks = ", ".join(profile.get("frameworks", []))
        body = f"Language: {lang}"
        if fwks:
            body += f". Frameworks: {fwks}"
        out.append({"title": f"Primary language: {lang}", "body": body})

    for key, label in (("test_commands", "Test command"), ("lint_commands", "Lint command")):
        cmds = profile.get(key) or []
        if cmds:
            joined = ", ".join(cmds) if isinstance(cmds, list) else str(cmds)
            out.append({"title": label, "body": f"{label}: {joined}"})

    return out
