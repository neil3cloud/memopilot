"""Compact repo map generator.

Generates a structural overview of the workspace: file paths + top-level
symbol signatures.  Used as a reserved ~500-token background context item
included in every context pack so the AI model knows where things live.

Excluded from the map:
  - test files (test_*.py, *_test.py, tests/ directories)
  - migration files (migrations/)
  - __pycache__ / generated files
"""

from __future__ import annotations

from dataclasses import dataclass

from .db import DatabaseManager

_EXCLUDED_PREFIXES = ("tests/", "test_", "migrations/", "__pycache__/")
_EXCLUDED_KINDS = {"import"}
_TOP_LEVEL_KINDS = {"function", "class"}
_TOKENS_PER_CHAR = 0.25  # ~4 chars per token


def _estimate_tokens(text: str) -> int:
    return max(0, int(len(text) * _TOKENS_PER_CHAR))


def _is_excluded(file_path: str) -> bool:
    parts = file_path.replace("\\", "/")
    return any(parts.startswith(p) or f"/{p}" in parts for p in _EXCLUDED_PREFIXES)


@dataclass(frozen=True)
class RepoMapEntry:
    file_path: str
    line: int
    signature: str
    kind: str


class RepoMapGenerator:
    """Build a compact repo map from indexed symbols."""

    def __init__(self, *, db: DatabaseManager) -> None:
        self._db = db

    async def generate(
        self, workspace_root: str = "", max_tokens: int = 1000
    ) -> str:
        """Return a compact text repo map within max_tokens budget."""
        entries = await self._fetch_entries(workspace_root)
        return self._format(entries, max_tokens)

    async def _fetch_entries(self, workspace_root: str) -> list[RepoMapEntry]:
        conn = await self._db.connect()

        if workspace_root:
            cursor = await conn.execute(
                """
                SELECT file_path, start_line, name, kind, signature
                FROM symbols
                WHERE kind IN ('function', 'class', 'method')
                ORDER BY file_path ASC, start_line ASC
                """
            )
        else:
            cursor = await conn.execute(
                """
                SELECT file_path, start_line, name, kind, signature
                FROM symbols
                WHERE kind IN ('function', 'class', 'method')
                ORDER BY file_path ASC, start_line ASC
                """
            )
        rows = await cursor.fetchall()

        entries: list[RepoMapEntry] = []
        for row in rows:
            fp = row["file_path"]
            if _is_excluded(fp):
                continue
            sig = row["signature"] or row["name"]
            entries.append(
                RepoMapEntry(
                    file_path=fp,
                    line=row["start_line"],
                    signature=sig,
                    kind=row["kind"],
                )
            )
        return entries

    def _format(self, entries: list[RepoMapEntry], max_tokens: int) -> str:
        if not entries:
            return "# Repo Map\n(no symbols indexed yet)\n"

        lines: list[str] = ["# Repo Map\n"]
        current_file: str | None = None
        budget_chars = int(max_tokens / _TOKENS_PER_CHAR)

        for entry in entries:
            if entry.file_path != current_file:
                current_file = entry.file_path
                line = f"\n{entry.file_path}\n"
                if len("\n".join(lines)) + len(line) > budget_chars:
                    lines.append("  ... (truncated)")
                    break
                lines.append(line)

            indent = "  " if entry.kind == "method" else "  "
            sig_line = f"{indent}{entry.signature}\n"
            if len("\n".join(lines)) + len(sig_line) > budget_chars:
                lines.append("  ... (truncated)")
                break
            lines.append(sig_line)

        return "".join(lines)
