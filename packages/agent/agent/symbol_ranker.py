"""FTS5-ranked symbol relevance scoring for symbol-level context assembly."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import aiosqlite

from .db import DatabaseManager
from .keyword_extraction import extract_search_keywords

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RankedSymbol:
    id: str
    file_path: str
    name: str
    kind: str
    start_line: int
    end_line: int
    signature: str | None
    summary: str | None
    score: float


async def rank_symbols_for_task(
    *,
    db: DatabaseManager,
    task_description: str,
    file_paths: list[str] | None,
    limit: int,
) -> list[RankedSymbol]:
    """Rank symbols by FTS5 relevance to the task.

    file_paths=None searches the whole workspace (used for file discovery —
    finding which files are relevant by symbol name/signature/summary
    content, not by matching the task text against file paths). A non-empty
    list scopes the search to those files (used once candidate files are
    already known, to rank symbols within them). An explicitly empty list
    returns no results — there's nothing to scope to.

    Returns an empty list if there are no usable keywords or no candidate
    files — callers should fall back to whole-file inclusion in that case.
    """
    if file_paths is not None and not file_paths:
        return []
    if limit <= 0:
        return []

    keywords = extract_search_keywords(task_description)
    if not keywords:
        return []

    fts_query = " OR ".join(keywords)
    conn = await db.connect()

    scope_clause = ""
    params: tuple[object, ...] = (fts_query,)
    if file_paths is not None:
        placeholders = ",".join("?" for _ in file_paths)
        scope_clause = f"AND s.file_path IN ({placeholders})"
        params = (fts_query, *file_paths)
    params = (*params, limit)

    logger.info(
        "rank_symbols_for_task: query=%r file_paths=%s limit=%d",
        fts_query, "ALL" if file_paths is None else len(file_paths), limit,
    )

    try:
        cursor = await conn.execute(
            f"""
            SELECT
                s.id, s.file_path, s.name, s.kind, s.start_line, s.end_line,
                s.signature, s.summary,
                bm25(symbols_fts) AS fts_rank
            FROM symbols_fts
            JOIN symbols AS s ON s.rowid = symbols_fts.rowid
            WHERE symbols_fts MATCH ?
              {scope_clause}
            ORDER BY fts_rank ASC
            LIMIT ?
            """,
            params,
        )
        rows = await cursor.fetchall()
    except aiosqlite.Error:
        logger.exception("rank_symbols_for_task: FTS query failed")
        return []

    logger.info("rank_symbols_for_task: returned %d rows", len(rows))

    return [
        RankedSymbol(
            id=row["id"],
            file_path=row["file_path"],
            name=row["name"],
            kind=row["kind"],
            start_line=row["start_line"],
            end_line=row["end_line"],
            signature=row["signature"],
            summary=row["summary"],
            # bm25 is lower-is-better and unbounded; invert to a positive
            # "higher is more relevant" score for callers that sort descending.
            score=-float(row["fts_rank"]),
        )
        for row in rows
    ]
