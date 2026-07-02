"""Graph-based structural retriever for the call/import relationship graph.

Populated at index time by the symbol extractor. Provides:
  - get_callers(symbol_id)  — who calls this function (up the call graph)
  - get_callees(symbol_id)  — what this function calls (down the call graph)
  - get_import_dependents(file_path) — which files import this file
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import aiosqlite

from .db import DatabaseManager


@dataclass(frozen=True)
class RelatedSymbol:
    id: str
    file_path: str
    name: str
    kind: str
    signature: str | None
    depth: int
    relation_type: str
    # Populated by get_callees_batch() for Tier 3 cross-file context pull-in;
    # left as None by callers that don't need them (get_callers, etc).
    start_line: int | None = None
    end_line: int | None = None
    summary: str | None = None


@dataclass(frozen=True)
class SymbolRelationshipRecord:
    id: str
    from_symbol_id: str
    to_symbol_id: str | None
    to_symbol_name: str
    to_file_path: str | None
    relation_type: str
    workspace_root: str
    # Position of the call site in source — used by JediResolver, never stored in SQLite
    call_line: int | None = None
    call_col: int | None = None


def make_relationship_id(
    from_symbol_id: str, to_symbol_name: str, relation_type: str, to_file_path: str | None
) -> str:
    key = f"{from_symbol_id}|{to_symbol_name}|{relation_type}|{to_file_path or ''}"
    return hashlib.sha1(key.encode()).hexdigest()


class GraphRetriever:
    """Query the structural symbol relationship graph stored in SQLite."""

    MAX_DEPTH = 2
    MAX_RESULTS = 20

    def __init__(self, *, db: DatabaseManager) -> None:
        self._db = db

    async def get_callers(
        self, symbol_id: str, depth: int | None = None
    ) -> list[RelatedSymbol]:
        """Return symbols that call the given symbol (up the call graph)."""
        max_depth = min(depth or self.MAX_DEPTH, self.MAX_DEPTH)
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            WITH RECURSIVE callers(symbol_id, depth) AS (
                SELECT sr.from_symbol_id, 1
                FROM symbol_relationships sr
                WHERE sr.to_symbol_id = ? AND sr.relation_type = 'calls'

                UNION ALL

                SELECT sr.from_symbol_id, c.depth + 1
                FROM symbol_relationships sr
                JOIN callers c ON sr.to_symbol_id = c.symbol_id
                WHERE c.depth < ?
            )
            SELECT DISTINCT
                s.id, s.file_path, s.name, s.kind, s.signature,
                MIN(c.depth) AS depth,
                'calls' AS relation_type
            FROM callers c
            JOIN symbols s ON s.id = c.symbol_id
            GROUP BY s.id
            ORDER BY depth ASC
            LIMIT ?
            """,
            (symbol_id, max_depth, self.MAX_RESULTS),
        )
        rows = await cursor.fetchall()
        return [self._row_to_related(row) for row in rows]

    async def get_callees(
        self, symbol_id: str, depth: int | None = None
    ) -> list[RelatedSymbol]:
        """Return symbols that the given symbol calls (down the call graph)."""
        max_depth = min(depth or self.MAX_DEPTH, self.MAX_DEPTH)
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            WITH RECURSIVE callees(symbol_id, depth) AS (
                SELECT sr.to_symbol_id, 1
                FROM symbol_relationships sr
                WHERE sr.from_symbol_id = ? AND sr.relation_type = 'calls'
                  AND sr.to_symbol_id IS NOT NULL

                UNION ALL

                SELECT sr.to_symbol_id, c.depth + 1
                FROM symbol_relationships sr
                JOIN callees c ON sr.from_symbol_id = c.symbol_id
                WHERE c.depth < ? AND sr.to_symbol_id IS NOT NULL
            )
            SELECT DISTINCT
                s.id, s.file_path, s.name, s.kind, s.signature,
                MIN(c.depth) AS depth,
                'calls' AS relation_type
            FROM callees c
            JOIN symbols s ON s.id = c.symbol_id
            GROUP BY s.id
            ORDER BY depth ASC
            LIMIT ?
            """,
            (symbol_id, max_depth, self.MAX_RESULTS),
        )
        rows = await cursor.fetchall()
        return [self._row_to_related(row) for row in rows]

    async def get_callees_batch(
        self, symbol_ids: list[str]
    ) -> dict[str, list[RelatedSymbol]]:
        """Single-hop, batched callee lookup for many symbols at once.

        Unlike get_callees() (single-id, recursive up to MAX_DEPTH), this is
        depth=1 only and batched — built for Tier 3 cross-file context
        pull-in, which needs the immediate callees of many Tier-1 symbols in
        one query. Calling get_callees() once per symbol would reintroduce
        the N+1 pattern fixed elsewhere in this codebase.
        """
        if not symbol_ids:
            return {}
        conn = await self._db.connect()
        placeholders = ",".join("?" for _ in symbol_ids)
        cursor = await conn.execute(
            f"""
            SELECT DISTINCT
                sr.from_symbol_id AS caller_id,
                s.id, s.file_path, s.name, s.kind, s.signature,
                s.start_line, s.end_line, s.summary
            FROM symbol_relationships sr
            JOIN symbols s ON s.id = sr.to_symbol_id
            WHERE sr.from_symbol_id IN ({placeholders})
              AND sr.relation_type = 'calls'
              AND sr.to_symbol_id IS NOT NULL
            """,
            symbol_ids,
        )
        rows = await cursor.fetchall()

        result: dict[str, list[RelatedSymbol]] = {symbol_id: [] for symbol_id in symbol_ids}
        for row in rows:
            result[row["caller_id"]].append(
                RelatedSymbol(
                    id=row["id"],
                    file_path=row["file_path"],
                    name=row["name"],
                    kind=row["kind"],
                    signature=row["signature"],
                    depth=1,
                    relation_type="calls",
                    start_line=row["start_line"],
                    end_line=row["end_line"],
                    summary=row["summary"],
                )
            )
        return result

    async def get_import_dependents(self, file_path: str) -> list[str]:
        """Return file paths that import the given file."""
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT DISTINCT s.file_path
            FROM symbol_relationships sr
            JOIN symbols s ON s.id = sr.from_symbol_id
            WHERE sr.to_file_path = ? AND sr.relation_type = 'imports'
            LIMIT ?
            """,
            (file_path, self.MAX_RESULTS),
        )
        rows = await cursor.fetchall()
        return [row["file_path"] for row in rows]

    async def get_related_symbols(
        self, symbol_id: str, relation_types: list[str] | None = None
    ) -> list[RelatedSymbol]:
        """Return directly related symbols (depth=1) by relation type."""
        types = relation_types or ["calls", "imports", "inherits", "instantiates"]
        placeholders = ", ".join("?" * len(types))
        conn = await self._db.connect()
        cursor = await conn.execute(
            f"""
            SELECT DISTINCT
                s.id, s.file_path, s.name, s.kind, s.signature,
                1 AS depth,
                sr.relation_type
            FROM symbol_relationships sr
            JOIN symbols s ON s.id = sr.to_symbol_id
            WHERE sr.from_symbol_id = ?
              AND sr.relation_type IN ({placeholders})
              AND sr.to_symbol_id IS NOT NULL
            LIMIT ?
            """,
            (symbol_id, *types, self.MAX_RESULTS),
        )
        rows = await cursor.fetchall()
        return [self._row_to_related(row) for row in rows]

    async def find_callers_not_in_context(
        self, symbol_id: str, context_file_paths: set[str]
    ) -> list[RelatedSymbol]:
        """Return callers whose files are NOT already in the context pack."""
        all_callers = await self.get_callers(symbol_id)
        return [c for c in all_callers if c.file_path not in context_file_paths]

    async def store_relationships(
        self,
        conn: aiosqlite.Connection,
        relationships: list[SymbolRelationshipRecord],
    ) -> None:
        """Upsert a batch of relationship records."""
        if not relationships:
            return
        await conn.executemany(
            """
            INSERT INTO symbol_relationships
                (id, from_symbol_id, to_symbol_id, to_symbol_name,
                 to_file_path, relation_type, workspace_root)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                to_symbol_id = excluded.to_symbol_id,
                to_file_path = excluded.to_file_path
            """,
            [
                (
                    rel.id,
                    rel.from_symbol_id,
                    rel.to_symbol_id,
                    rel.to_symbol_name,
                    rel.to_file_path,
                    rel.relation_type,
                    rel.workspace_root,
                )
                for rel in relationships
            ],
        )

    def _row_to_related(self, row: aiosqlite.Row) -> RelatedSymbol:
        return RelatedSymbol(
            id=row["id"],
            file_path=row["file_path"],
            name=row["name"],
            kind=row["kind"],
            signature=row["signature"],
            depth=row["depth"],
            relation_type=row["relation_type"],
        )
