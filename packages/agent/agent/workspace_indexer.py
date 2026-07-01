"""Workspace indexing pipeline for Phase 3."""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from .config import Config
from .csharp_extractor import CSharpExtractor
from .csharp_resolver import CSharpResolver
from .db import DatabaseManager
from .extractor_registry import ExtractorRegistry
from .graph_retriever import GraphRetriever, SymbolRelationshipRecord
from .jedi_resolver import JediResolver
from .project_scanner import WorkspaceScanner
from .python_extractor import PythonExtractor
from .symbol_summarizer import SymbolSummarizer
from .typescript_extractor import TypeScriptExtractor

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
    """Indexes workspace files and updates file + symbol tables."""

    def __init__(
        self,
        *,
        config: Config,
        db: DatabaseManager,
        scanner: WorkspaceScanner | None = None,
        summarizer: SymbolSummarizer | None = None,
        registry: ExtractorRegistry | None = None,
    ) -> None:
        self._config = config
        self._db = db
        self._summarizer = summarizer
        self._jedi_resolver = JediResolver(str(config.workspace_path))
        self._csharp_resolver = CSharpResolver(str(config.workspace_path))
        self._graph = GraphRetriever(db=self._db)

        # Initialize extractor registry with Python, TypeScript, and C# extractors
        self._registry = registry or ExtractorRegistry()
        if not self._registry.all_languages():
            self._registry.register(PythonExtractor())
            self._registry.register(TypeScriptExtractor())
            self._registry.register(CSharpExtractor())

        # Initialize scanner with supported extensions
        extensions = self._registry.all_extensions()
        self._scanner = scanner or WorkspaceScanner(config.workspace_path, file_extensions=extensions)

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
            file_ext = rel_path.suffix

            # Get the appropriate extractor for this file type
            extractor = self._registry.get(file_ext)
            if extractor is None:
                # Skip unsupported file types (shouldn't happen if scanner is correct)
                continue

            content = self._read_text(self._config.workspace_path / rel_path)
            content_hash = self._content_hash(content)
            previous_hash = existing_hashes.get(file_path)
            language = extractor.language

            if previous_hash == content_hash:
                unchanged_files += 1
                await self._upsert_file_index(
                    conn=conn,
                    file_path=file_path,
                    language=language,
                    content_hash=content_hash,
                )
                continue

            indexed_files += 1
            raw_symbols = extractor.extract(
                file_path=file_path,
                source=content,
                content_hash=content_hash,
            )
            # Deduplicate by ID — extractors can produce the same node via
            # multiple code paths (e.g. regex mis-match in C# method names).
            seen_ids: set[str] = set()
            symbols = []
            for s in raw_symbols:
                if s.id not in seen_ids:
                    seen_ids.add(s.id)
                    symbols.append(s)
                else:
                    logger.warning(
                        "Duplicate symbol id %s (%s:%s) in %s — skipping",
                        s.id, s.kind, s.name, file_path,
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
                    INSERT OR IGNORE INTO symbols
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
            relationships = extractor.extract_relationships(
                file_path=file_path,
                source=content,
                symbols=list(symbols),
                workspace_root=str(self._config.workspace_path),
            )
            relationships = await self._resolve_cross_module_calls(
                conn=conn,
                source=content,
                abs_file_path=str(self._config.workspace_path / rel_path),
                relationships=relationships,
                language=language,
            )
            await self._graph.store_relationships(conn, relationships)

            await self._upsert_file_index(
                conn=conn,
                file_path=file_path,
                language=language,
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

    async def _resolve_cross_module_calls(
        self,
        *,
        conn: aiosqlite.Connection,
        source: str,
        abs_file_path: str,
        relationships: list[SymbolRelationshipRecord],
        language: str,
    ) -> list[SymbolRelationshipRecord]:
        """Enrich to_symbol_id for cross-module call relationships.

        Supports Python call resolution (Jedi) and C# namespace/DI backfill.
        """
        if language == "csharp":
            file_namespace = self._csharp_resolver.extract_namespace_from_source(source)
            return await self._csharp_resolver.backfill_relationship_symbols(
                relationships,
                conn,
                file_namespace=file_namespace,
            )

        # Only use Jedi for Python files
        if language != "python":
            return relationships
        if not self._jedi_resolver.available:
            return relationships

        unresolved = [
            (r.call_line, r.call_col, r.id)
            for r in relationships
            if r.relation_type == "calls"
            and r.to_symbol_id is None
            and r.call_line is not None
            and r.call_col is not None
        ]
        if not unresolved:
            return relationships

        resolved = self._jedi_resolver.resolve(
            source=source,
            abs_file_path=abs_file_path,
            call_sites=unresolved,
        )
        if not resolved:
            return relationships

        # Build lookup: rel_id → (relative_file_path, bare_name)
        workspace_root = str(self._config.workspace_path)
        resolution_map: dict[str, tuple[str, str]] = {}
        for rc in resolved:
            try:
                rel = Path(rc.module_path).relative_to(workspace_root)
                resolution_map[rc.rel_id] = (rel.as_posix(), rc.bare_name)
            except ValueError:
                pass

        if not resolution_map:
            return relationships

        enriched: list[SymbolRelationshipRecord] = []
        for rel in relationships:
            if rel.id not in resolution_map:
                enriched.append(rel)
                continue

            target_file, bare_name = resolution_map[rel.id]
            # Find the symbol in the target file — methods are stored as
            # "ClassName.method_name", top-level functions as "method_name".
            cursor = await conn.execute(
                """
                SELECT id FROM symbols
                WHERE file_path = ?
                  AND (name = ? OR name LIKE ?)
                LIMIT 1
                """,
                (target_file, bare_name, f"%.{bare_name}"),
            )
            row = await cursor.fetchone()
            if row:
                enriched.append(SymbolRelationshipRecord(
                    id=rel.id,
                    from_symbol_id=rel.from_symbol_id,
                    to_symbol_id=row["id"],
                    to_symbol_name=rel.to_symbol_name,
                    to_file_path=target_file,
                    relation_type=rel.relation_type,
                    workspace_root=rel.workspace_root,
                ))
            else:
                enriched.append(rel)

        resolved_count = sum(1 for r in enriched if r.to_symbol_id is not None
                             and r.id in resolution_map)
        if resolved_count:
            logger.debug(
                "_resolve_cross_module_calls: resolved %d/%d cross-module calls in %s",
                resolved_count, len(unresolved), abs_file_path,
            )
        return enriched

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
            except Exception as exc:
                logger.exception("_summarize_pending_symbols: batch %d failed", i // batch_size + 1)
                # Provider unreachable — stop batching rather than hammering a dead endpoint.
                import httpx
                if isinstance(exc, httpx.ConnectError):
                    logger.warning("_summarize_pending_symbols: provider unreachable, aborting remaining batches")
                    break

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
            WHERE workspace_root = ?
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
