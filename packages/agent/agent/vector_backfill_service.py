"""Vector index backfill service for existing memory items and symbols.

Generates embeddings for existing memory items and symbols to enable
semantic search functionality.
"""

from __future__ import annotations

from typing import Any

from .config import Config
from .db import DatabaseManager
from .vector_index_service import VectorIndexService


class VectorBackfillService:
    """Backfills vector embeddings for existing memory items and symbols."""

    def __init__(self, db: DatabaseManager, config: Config) -> None:
        self._db = db
        self._config = config
        self._vector_service = VectorIndexService(db, config)

    async def backfill_memory_items(
        self,
        workspace_root: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """
        Generate embeddings for memory items.
        
        Args:
            workspace_root: Filter to specific workspace (None = all)
            limit: Max items to process (None = all)
            
        Returns:
            Stats: total_items, embedded_count, failed_count, model_used
        """
        try:
            conn = await self._db.connect()

            # Get preferred model from config
            config_row = await conn.execute(
                "SELECT preferred_model, embedding_dimension FROM vector_config WHERE id = 1"
            )
            config_data = await config_row.fetchone()
            model = config_data[0] if config_data else "ollama:nomic-embed-text"

            # Query memory items
            query = "SELECT id, title, body FROM memory_items"
            params: list[Any] = []

            if workspace_root:
                query += " WHERE source_path LIKE ?"
                params.append(f"{workspace_root}%")

            if limit:
                query += " LIMIT ?"
                params.append(limit)

            cursor = await conn.execute(query, params)
            items = await cursor.fetchall()

            embedded_count = 0
            failed_count = 0

            # Process each memory item
            for item_id, title, body in items:
                try:
                    # Create text to embed (title + body summary)
                    text_to_embed = f"{title}\n{body[:500]}"  # First 500 chars of body

                    # Generate embedding
                    embedding = await self._vector_service.embed_text(text_to_embed, model)

                    if embedding:
                        # Store in vectors table
                        stored = await self._vector_service.store_vector(
                            entity_type="memory_item",
                            entity_id=item_id,
                            embedding=embedding,
                            model=model,
                        )
                        if stored:
                            embedded_count += 1
                        else:
                            failed_count += 1
                    else:
                        failed_count += 1
                except Exception:
                    failed_count += 1

            # Update indexing status
            if workspace_root:
                await self._vector_service.update_index_status(
                    workspace_root=workspace_root,
                    symbols_indexed=0,
                    memory_items_indexed=embedded_count,
                    model=model,
                )

            return {
                "total_items": len(items),
                "embedded_count": embedded_count,
                "failed_count": failed_count,
                "model_used": model,
                "workspace_root": workspace_root,
            }
        except Exception as e:
            return {
                "total_items": 0,
                "embedded_count": 0,
                "failed_count": 0,
                "error": str(e),
            }

    async def backfill_symbols(
        self,
        workspace_root: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """
        Generate embeddings for code symbols.
        
        Args:
            workspace_root: Filter to specific workspace (None = all)
            limit: Max items to process (None = all)
            
        Returns:
            Stats: total_items, embedded_count, failed_count, model_used
        """
        try:
            conn = await self._db.connect()

            # Get preferred model from config
            config_row = await conn.execute(
                "SELECT preferred_model FROM vector_config WHERE id = 1"
            )
            config_data = await config_row.fetchone()
            model = config_data[0] if config_data else "ollama:nomic-embed-text"

            # Query symbols
            query = "SELECT id, name, summary, signature FROM symbols"
            params: list[Any] = []

            if workspace_root:
                query += " WHERE file_path LIKE ?"
                params.append(f"{workspace_root}%")

            if limit:
                query += " LIMIT ?"
                params.append(limit)

            cursor = await conn.execute(query, params)
            symbols = await cursor.fetchall()

            embedded_count = 0
            failed_count = 0

            # Process each symbol
            for symbol_id, name, summary, signature in symbols:
                try:
                    # Create text to embed
                    parts = [name]
                    if summary:
                        parts.append(summary)
                    if signature:
                        parts.append(signature)

                    text_to_embed = "\n".join(parts)

                    # Generate embedding
                    embedding = await self._vector_service.embed_text(text_to_embed, model)

                    if embedding:
                        # Store in vectors table
                        stored = await self._vector_service.store_vector(
                            entity_type="symbol",
                            entity_id=symbol_id,
                            embedding=embedding,
                            model=model,
                        )
                        if stored:
                            embedded_count += 1
                        else:
                            failed_count += 1
                    else:
                        failed_count += 1
                except Exception:
                    failed_count += 1

            # Update indexing status
            if workspace_root:
                await self._vector_service.update_index_status(
                    workspace_root=workspace_root,
                    symbols_indexed=embedded_count,
                    memory_items_indexed=0,
                    model=model,
                )

            return {
                "total_items": len(symbols),
                "embedded_count": embedded_count,
                "failed_count": failed_count,
                "model_used": model,
                "workspace_root": workspace_root,
            }
        except Exception as e:
            return {
                "total_items": 0,
                "embedded_count": 0,
                "failed_count": 0,
                "error": str(e),
            }

    async def backfill_all(
        self,
        workspace_root: str | None = None,
    ) -> dict[str, Any]:
        """
        Backfill both memory items and symbols.
        
        Returns combined stats from both operations.
        """
        memory_stats = await self.backfill_memory_items(workspace_root)
        symbol_stats = await self.backfill_symbols(workspace_root)

        return {
            "memory_items": memory_stats,
            "symbols": symbol_stats,
            "total_embedded": memory_stats.get("embedded_count", 0) + symbol_stats.get("embedded_count", 0),
            "total_failed": memory_stats.get("failed_count", 0) + symbol_stats.get("failed_count", 0),
            "workspace_root": workspace_root,
        }
