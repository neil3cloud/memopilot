"""Reads a symbol's source slice from disk, and formats compact skeleton lines.

Used by symbol-level context assembly (api.py) and by the indexer's
summarization batching (workspace_indexer.py), which previously duplicated
this read+slice pattern inline.
"""

from __future__ import annotations

import asyncio
from pathlib import Path


def _read_text(file_path: Path) -> str:
    return file_path.read_text(encoding="utf-8", errors="replace")


async def read_symbol_source(
    *,
    workspace_root: Path,
    file_path: str,
    start_line: int,
    end_line: int,
) -> str:
    """Read just the given 1-indexed, inclusive line range of a file."""
    text = await asyncio.to_thread(_read_text, workspace_root / file_path)
    lines = text.splitlines()
    return "\n".join(lines[start_line - 1 : end_line])


def build_skeleton_line(
    *,
    name: str,
    kind: str,
    signature: str | None,
    summary: str | None,
) -> str:
    """One-line representation of a symbol not expanded to full source.

    `name` and `kind` are always present and authoritative (methods are
    qualified as "ClassName.method"); `signature` varies in shape across
    the three language extractors and is shown as supplementary detail
    rather than relied on as the primary label, to avoid assuming a format.

    Never fabricates a summary — a pending (not-yet-summarized) symbol is
    shown explicitly as pending rather than silently omitted or guessed at.
    """
    header = f"{kind} {name}"
    if signature:
        header = f"{header} — {signature.strip()}"
    if summary:
        return f"- {header}: {summary.strip()}"
    return f"- {header} (summary pending)"
