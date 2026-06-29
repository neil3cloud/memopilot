"""Workspace indexing pipeline for Phase 3."""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from .config import Config
from .db import DatabaseManager
from .graph_retriever import GraphRetriever
from .project_scanner import WorkspaceScanner
from .symbol_extractor import SymbolExtractor
from .symbol_summarizer import SymbolSummarizer

logger = logging.getLogger(__name__)

_SUMMARY_CAP_PER_RUN = 500
_DEFAULT_BATCH_SIZE = 25


@dataclass(frozen=True)
class WorkspaceIndexResult:
    python_project: bool
    total_files_scanned: int
    indexed_files: int
    unchanged_files: int
    stale_files: int
    skipped_files: int
    symbols_extracted: int
    duration_ms: int


class WorkspaceIndexer:
    """Indexes workspace Python files and updates file + symbol tables."""

    def __init__(
        self,
        *,
        config: Config,
        db: DatabaseManager,
        scanner: WorkspaceScanner | None = None,
        symbol_extractor: SymbolExtractor | None = None,
        summarizer: SymbolSummarizer | None = None,
    ) -> None:
        self._config = config
        self._db = db
        self._scanner = scanner or WorkspaceScanner(config.workspace_path)
        self._symbol_extractor = symbol_extractor or SymbolExtractor()
        self._summarizer = summarizer

    async def index_workspace(self) -> WorkspaceIndexResult:
        started_at = time.perf_counter()
        scan_result = self._scanner.scan()
        conn = await self._db.connect()

        existing_hashes = await self._fetch_existing_hashes(conn)
        scanned_paths = {path.as_posix() for path in scan_result.python_files}
        removed_paths = sorted(set(existing_hashes) - scanned_paths)

        indexed_files = 0
        unchanged_files = 0
        symbols_extracted = 0

        for rel_path in scan_result.python_files:
            file_path = rel_path.as_posix()
            content = self._read_text(self._config.workspace_path / rel_path)
            content_hash = self._content_hash(content)
            previous_hash = existing_hashes.get(file_path)

            if previous_hash == content_hash:
                unchanged_files += 1
                await self._upsert_file_index(
                    conn=conn,
                    file_path=file_path,
                    language="python",
                    content_hash=content_hash,
                )
                continue

            indexed_files += 1
            symbols = self._symbol_extractor.extract(
                file_path=file_path,
                source=content,
                content_hash=content_hash,
            )
            symbols_extracted += len(symbols)

            # Delete stale relationships BEFORE deleting symbols (subquery needs them)
            await conn.execute(
                "DELETE FROM symbol_relationships WHERE from_symbol_id IN "
                "(SELECT id FROM symbols WHERE file_path = ?) "
                "OR to_symbol_id IN (SELECT id FROM symbols WHERE file_path = ?)",
                (file_path, file_path),
            )
            await conn.execute(
                "DELETE FROM symbol_relationships WHERE to_file_path = ?",
                (file_path,),
            )
            await conn.execute("DELETE FROM symbols WHERE file_path = ?", (file_path,))
            for symbol in symbols:
                await conn.execute(
                    """
                    INSERT INTO symbols
                    (
                        id, file_path, name, kind, start_line,
                        end_line, signature, summary, content_hash
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)
                    """,
                    (
                        symbol.id,
                        symbol.file_path,
                        symbol.name,
                        symbol.kind,
                        symbol.start_line,
                        symbol.end_line,
                        symbol.signature,
                        symbol.content_hash,
                    ),
                )

            # Extract and store structural relationships
            relationships = self._symbol_extractor.extract_relationships(
                file_path=file_path,
                source=content,
                symbols=list(symbols),
                workspace_root=str(self._config.workspace_path),
            )
            graph = GraphRetriever(db=self._db)
            await graph.store_relationships(conn, relationships)

            await self._upsert_file_index(
                conn=conn,
                file_path=file_path,
                language="python",
                content_hash=content_hash,
            )

        for file_path in removed_paths:
            await conn.execute(
                "DELETE FROM symbol_relationships WHERE from_symbol_id IN "
                "(SELECT id FROM symbols WHERE file_path = ?) "
                "OR to_symbol_id IN (SELECT id FROM symbols WHERE file_path = ?)",
                (file_path, file_path),
            )
            await conn.execute("DELETE FROM symbol_relationships WHERE to_file_path = ?", (file_path,))
            await conn.execute(
                """
                UPDATE file_index
                SET stale = 1, last_indexed_at = datetime('now')
                WHERE file_path = ?
                """,
                (file_path,),
            )
            await conn.execute("DELETE FROM symbols WHERE file_path = ?", (file_path,))

        await conn.commit()

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        return WorkspaceIndexResult(
            python_project=scan_result.python_project,
            total_files_scanned=len(scan_result.python_files),
            indexed_files=indexed_files,
            unchanged_files=unchanged_files,
            stale_files=len(removed_paths),
            skipped_files=scan_result.skipped_files,
            symbols_extracted=symbols_extracted,
            duration_ms=duration_ms,
        )

    async def _summarize_pending_symbols(self, batch_size: int = _DEFAULT_BATCH_SIZE) -> None:
        """Background task: fill NULL summaries for function/class symbols.

        Sends symbols in batches of batch_size to reduce LLM call count.
        Commits each batch immediately so partial progress is preserved.
        """
        if self._summarizer is None:
            return
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT s.id, s.file_path, s.name, s.kind, s.signature, s.start_line, s.end_line
            FROM symbols s
            JOIN file_index fi ON fi.file_path = s.file_path
            WHERE s.summary IS NULL
              AND s.kind IN ('function', 'class')
              AND fi.stale = 0
            LIMIT ?
            """,
            (_SUMMARY_CAP_PER_RUN,),
        )
        rows = await cursor.fetchall()
        logger.info("_summarize_pending_symbols: %d symbols to process, batch_size=%d", len(rows), batch_size)

        # Group into batches of batch_size
        for i in range(0, len(rows), batch_size):
            batch_rows = rows[i : i + batch_size]
            batch_symbols: list[dict] = []
            for row in batch_rows:
                try:
                    lines = self._read_text(
                        self._config.workspace_path / row["file_path"]
                    ).splitlines()
                    source = "\n".join(lines[row["start_line"] - 1 : row["end_line"]])
                    batch_symbols.append({
                        "id": row["id"],
                        "name": row["name"],
                        "kind": row["kind"],
                        "signature": row["signature"] or "",
                        "source": source,
                    })
                except Exception:
                    pass

            if not batch_symbols:
                continue

            logger.info("_summarize_pending_symbols: sending batch %d/%d (%d symbols)", i // batch_size + 1, (len(rows) + batch_size - 1) // batch_size, len(batch_symbols))
            try:
                summaries = await self._summarizer.summarize_batch(batch_symbols)
                logger.info("_summarize_pending_symbols: batch returned %d summaries", len(summaries))
                for sym_id, summary in summaries.items():
                    await conn.execute(
                        "UPDATE symbols SET summary = ? WHERE id = ?",
                        (summary, sym_id),
                    )
                await conn.commit()
            except Exception:
                logger.exception("_summarize_pending_symbols: batch %d failed", i // batch_size + 1)

    async def rebuild_memory(self) -> WorkspaceIndexResult:
        conn = await self._db.connect()
        await conn.execute("DELETE FROM symbols")
        await conn.execute("DELETE FROM file_index")
        await conn.execute("UPDATE memory_items SET stale = 1")
        await conn.commit()
        return await self.index_workspace()

    async def _fetch_existing_hashes(self, conn: aiosqlite.Connection) -> dict[str, str]:
        cursor = await conn.execute(
            """
            SELECT file_path, content_hash
            FROM file_index
            WHERE language = 'python'
              AND workspace_root = ?
            """,
            (str(self._config.workspace_path),),
        )
        rows = await cursor.fetchall()
        return {row["file_path"]: row["content_hash"] for row in rows}

    async def _upsert_file_index(
        self, *, conn: aiosqlite.Connection, file_path: str, language: str, content_hash: str
    ) -> None:
        await conn.execute(
            """
            INSERT INTO file_index (file_path, language, content_hash, stale, workspace_root)
            VALUES (?, ?, ?, 0, ?)
            ON CONFLICT(file_path) DO UPDATE SET
                language=excluded.language,
                content_hash=excluded.content_hash,
                stale=0,
                workspace_root=excluded.workspace_root,
                last_indexed_at=datetime('now')
            """,
            (file_path, language, content_hash, str(self._config.workspace_path)),
        )

    def _read_text(self, file_path: Path) -> str:
        return file_path.read_text(encoding="utf-8", errors="replace")

    def _content_hash(self, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
