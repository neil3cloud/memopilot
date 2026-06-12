"""Response cache for context-pack keyed output reuse."""

from __future__ import annotations

from dataclasses import dataclass

from .db import DatabaseManager


@dataclass(frozen=True)
class CachedResponse:
    context_pack_hash: str
    response_text: str
    provider: str | None
    model: str | None
    estimated_cost: float
    actual_cost: float | None
    hit_count: int


class ResponseCacheService:
    """Stores and fetches cached responses for repeated context packs."""

    def __init__(self, *, db: DatabaseManager) -> None:
        self._db = db

    async def put(
        self,
        *,
        context_pack_hash: str,
        response_text: str,
        provider: str | None,
        model: str | None,
        estimated_cost: float,
        actual_cost: float | None,
    ) -> None:
        conn = await self._db.connect()
        await conn.execute(
            """
            INSERT INTO response_cache
            (
                context_pack_hash, response_text, provider, model,
                estimated_cost, actual_cost, hit_count, last_hit_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 0, NULL)
            ON CONFLICT(context_pack_hash) DO UPDATE SET
                response_text = excluded.response_text,
                provider = excluded.provider,
                model = excluded.model,
                estimated_cost = excluded.estimated_cost,
                actual_cost = excluded.actual_cost,
                updated_at = datetime('now')
            """,
            (
                context_pack_hash,
                response_text,
                provider,
                model,
                max(estimated_cost, 0.0),
                actual_cost,
            ),
        )
        await conn.commit()

    async def get(self, *, context_pack_hash: str) -> CachedResponse | None:
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT
                context_pack_hash, response_text, provider, model,
                estimated_cost, actual_cost, hit_count
            FROM response_cache
            WHERE context_pack_hash = ?
            """,
            (context_pack_hash,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        await conn.execute(
            """
            UPDATE response_cache
            SET hit_count = hit_count + 1, last_hit_at = datetime('now')
            WHERE context_pack_hash = ?
            """,
            (context_pack_hash,),
        )
        await conn.commit()

        return CachedResponse(
            context_pack_hash=row["context_pack_hash"],
            response_text=row["response_text"],
            provider=row["provider"],
            model=row["model"],
            estimated_cost=float(row["estimated_cost"] or 0.0),
            actual_cost=None if row["actual_cost"] is None else float(row["actual_cost"]),
            hit_count=int(row["hit_count"] or 0) + 1,
        )
