"""Vector embedding and search service using sqlite-vec.

Provides semantic search via vector embeddings for symbols, memory items,
and context packs.
"""

from __future__ import annotations

import struct
import uuid
from typing import Any

from .config import Config
from .db import DatabaseManager


class VectorIndexService:
    """Manages vector embeddings and semantic search."""

    def __init__(self, db: DatabaseManager, config: Config) -> None:
        self._db = db
        self._config = config

    async def embed_text(self, text: str, model: str | None = None) -> list[float] | None:
        """
        Generate embedding for text using configured model.
        
        Tries in order: ollama, anthropic, openai
        Returns None if no embedder available.
        """
        model_to_use = model or self._config.get("embedding_model", "")

        # Try ollama first
        try:
            embedding = await self._embed_ollama(text, model_to_use or "nomic-embed-text")
            if embedding:
                return embedding
        except Exception:
            pass

        # Try anthropic
        try:
            if self._config.get("anthropic_api_key"):
                embedding = await self._embed_anthropic(text)
                if embedding:
                    return embedding
        except Exception:
            pass

        # Try openai
        try:
            if self._config.get("openai_api_key"):
                embedding = await self._embed_openai(text)
                if embedding:
                    return embedding
        except Exception:
            pass

        return None

    async def _embed_ollama(self, text: str, model: str = "nomic-embed-text") -> list[float] | None:
        """Generate embedding using ollama."""
        import httpx

        url = "http://127.0.0.1:11434/api/embed"
        payload = {"model": model, "input": text}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                return data.get("embeddings", [None])[0] if data.get("embeddings") else None
        except Exception:
            return None

    async def _embed_anthropic(self, text: str) -> list[float] | None:
        """Generate embedding using anthropic."""
        # Anthropic embeddings would require their embedding API
        # For now, return None as it's not implemented
        return None

    async def _embed_openai(self, text: str) -> list[float] | None:
        """Generate embedding using OpenAI."""
        import httpx

        api_key = self._config.get("openai_api_key")
        if not api_key:
            return None

        url = "https://api.openai.com/v1/embeddings"
        headers = {"Authorization": f"Bearer {api_key}"}
        payload = {"model": "text-embedding-3-small", "input": text}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
                embeddings = data.get("data", [])
                return embeddings[0].get("embedding") if embeddings else None
        except Exception:
            return None

    async def store_vector(
        self,
        entity_type: str,
        entity_id: str,
        embedding: list[float],
        model: str,
    ) -> bool:
        """Store embedding for an entity (symbol, memory item, etc.)."""
        if not embedding:
            return False

        try:
            conn = await self._db.connect()
            # Convert embedding list to binary format for sqlite-vec
            embedding_bytes = self._embedding_to_bytes(embedding)

            await conn.execute(
                """
                INSERT OR REPLACE INTO vectors
                (id, entity_type, entity_id, embedding, dimension, model)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    entity_type,
                    entity_id,
                    embedding_bytes,
                    len(embedding),
                    model,
                ),
            )
            await conn.commit()
            return True
        except Exception:
            return False

    async def search_vectors(
        self,
        embedding: list[float],
        entity_type: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Search for similar vectors using sqlite-vec.
        
        Returns list of (entity_id, similarity_score) tuples.
        """
        if not embedding:
            return []

        try:
            conn = await self._db.connect()
            embedding_bytes = self._embedding_to_bytes(embedding)

            # sqlite-vec query: find k-nearest neighbors
            query = """
                SELECT 
                    entity_id,
                    entity_type,
                    distance
                FROM vectors
                WHERE embedding MATCH ?
                  AND k = ?
            """

            params = [embedding_bytes, limit]

            if entity_type:
                query = query.replace("FROM vectors", "FROM vectors WHERE entity_type = ?", 1)
                params.insert(1, entity_type)

            query += " ORDER BY distance ASC"

            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()

            results = []
            for row in rows:
                entity_id, etype, distance = row
                # Convert distance to similarity score (0-1 range)
                similarity = max(0, 1 - distance / 2)  # Rough approximation
                results.append({
                    "entity_id": entity_id,
                    "entity_type": etype,
                    "distance": distance,
                    "similarity": similarity,
                })

            return results
        except Exception:
            return []

    def _embedding_to_bytes(self, embedding: list[float]) -> bytes:
        """Convert embedding list to bytes for sqlite-vec."""
        # sqlite-vec uses little-endian float32
        return struct.pack(f"<{len(embedding)}f", *embedding)

    def _bytes_to_embedding(self, data: bytes) -> list[float]:
        """Convert bytes back to embedding list."""
        return list(struct.unpack(f"<{len(data)//4}f", data))

    async def get_vector_config(self) -> dict[str, Any]:
        """Get current vector configuration."""
        try:
            conn = await self._db.connect()
            cursor = await conn.execute("SELECT enabled, preferred_model, embedding_dimension FROM vector_config WHERE id = 1")
            row = await cursor.fetchone()
            if row:
                return {
                    "enabled": bool(row[0]),
                    "preferred_model": row[1],
                    "embedding_dimension": row[2],
                }
        except Exception:
            pass

        return {
            "enabled": False,
            "preferred_model": None,
            "embedding_dimension": 768,
        }

    async def update_index_status(
        self,
        workspace_root: str,
        symbols_indexed: int,
        memory_items_indexed: int,
        model: str,
    ) -> bool:
        """Update vector indexing status for a workspace."""
        try:
            conn = await self._db.connect()
            await conn.execute(
                """
                INSERT OR REPLACE INTO vector_index_status
                (workspace_root, symbols_indexed, memory_items_indexed, last_indexed_at, last_indexed_model)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?)
                """,
                (workspace_root, symbols_indexed, memory_items_indexed, model),
            )
            await conn.commit()
            return True
        except Exception:
            return False
