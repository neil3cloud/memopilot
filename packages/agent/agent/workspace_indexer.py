"""Workspace indexing pipeline for Phase 3."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from .config import Config
from .db import DatabaseManager
from .graph_retriever import GraphRetriever
from .project_scanner import WorkspaceScanner
from .symbol_extractor import SymbolExtractor


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
    ) -> None:
        self._config = config
        self._db = db
        self._scanner = scanner or WorkspaceScanner(config.workspace_path)
        self._symbol_extractor = symbol_extractor or SymbolExtractor()

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
                "(SELECT id FROM symbols WHERE file_path = ?)",
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

    async def rebuild_memory(self) -> WorkspaceIndexResult:
        """Rebuild indexed file/symbol memory from source files."""
        conn = await self._db.connect()
        await conn.execute("DELETE FROM symbols")
        await conn.execute("DELETE FROM file_index")
        await conn.execute("UPDATE memory_items SET stale = 1")
        await conn.commit()
        return await self.index_workspace()

    async def _fetch_existing_hashes(self, conn: aiosqlite.Connection) -> dict[str, str]:
        cursor = await conn.execute(
            "SELECT file_path, content_hash FROM file_index WHERE language = 'python'"
        )
        rows = await cursor.fetchall()
        return {row["file_path"]: row["content_hash"] for row in rows}

    async def _upsert_file_index(
        self, *, conn: aiosqlite.Connection, file_path: str, language: str, content_hash: str
    ) -> None:
        await conn.execute(
            """
            INSERT INTO file_index (file_path, language, content_hash, stale)
            VALUES (?, ?, ?, 0)
            ON CONFLICT(file_path) DO UPDATE SET
                language=excluded.language,
                content_hash=excluded.content_hash,
                stale=0,
                last_indexed_at=datetime('now')
            """,
            (file_path, language, content_hash),
        )

    def _read_text(self, file_path: Path) -> str:
        return file_path.read_text(encoding="utf-8", errors="replace")

    def _content_hash(self, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
