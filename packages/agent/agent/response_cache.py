"""Response cache for context-pack keyed output reuse."""

from __future__ import annotations

from dataclasses import dataclass

from .db import DatabaseManager

CRITICAL_TASK_TYPES = {"security_change", "billing_change", "schema_change"}


@dataclass(frozen=True)
class CachedResponse:
    context_pack_hash: str
    response_text: str
    provider: str | None
    model: str | None
    estimated_cost: float
    actual_cost: float | None
    hit_count: int
    response_status: str


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
        response_status: str = "success",
    ) -> None:
        normalized_status = self._normalize_status(response_status)
        conn = await self._db.connect()
        await conn.execute(
            """
            INSERT INTO response_cache
            (
                context_pack_hash, response_text, provider, model,
                estimated_cost, actual_cost, response_status, hit_count, last_hit_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL)
            ON CONFLICT(context_pack_hash) DO UPDATE SET
                response_text = excluded.response_text,
                provider = excluded.provider,
                model = excluded.model,
                estimated_cost = excluded.estimated_cost,
                actual_cost = excluded.actual_cost,
                response_status = excluded.response_status,
                updated_at = datetime('now')
            """,
            (
                context_pack_hash,
                response_text,
                provider,
                model,
                max(estimated_cost, 0.0),
                actual_cost,
                normalized_status,
            ),
        )
        await conn.commit()

    async def lookup(
        self,
        *,
        context_pack_hash: str,
        task_type: str | None = None,
    ) -> CachedResponse | None:
        if task_type in CRITICAL_TASK_TYPES:
            return None

        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT
                context_pack_hash, response_text, provider, model,
                estimated_cost, actual_cost, hit_count, response_status
            FROM response_cache
            WHERE context_pack_hash = ?
              AND response_status = 'success'
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
            response_status=str(row["response_status"] or "success"),
        )

    async def get(self, *, context_pack_hash: str) -> CachedResponse | None:
        return await self.lookup(context_pack_hash=context_pack_hash)

    def _normalize_status(self, response_status: str | None) -> str:
        normalized = str(response_status or "success").strip().lower()
        valid_statuses = {"pending", "running", "success", "failed", "cancelled"}
        return normalized if normalized in valid_statuses else "failed"
