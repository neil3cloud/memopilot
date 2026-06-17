"""Memory recall service with use policy and visibility filtering."""

from __future__ import annotations

import hashlib
import json
import uuid
from enum import StrEnum
from typing import Any

import aiosqlite
from pydantic import BaseModel, Field, ValidationError

from .config import Config
from .db import DatabaseManager
from .vector_index_service import VectorIndexService


class VisibilityScope(StrEnum):
    workspace = "workspace"
    global_dev = "global_dev"
    local_only = "local_only"
    restricted = "restricted"


class VisibilityTarget(StrEnum):
    cloud_context = "cloud_context"
    local_context = "local_context"
    patch_generation = "patch_generation"
    rule_enforcement = "rule_enforcement"
    review_only = "review_only"


class UsePolicy(BaseModel):
    allowed_in_cloud_context: bool = True
    allowed_in_local_context: bool = True
    allowed_for_patch_generation: bool = True
    allowed_for_rule_enforcement: bool = True
    can_override_ai_suggestion: bool = False
    review_required_before_use: bool = False
    restriction_reason: str | None = None


class ProvenanceEntry(BaseModel):
    source_type: str
    source_ref: str
    source_path: str | None = None
    source_hash: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    commit_sha: str | None = None


class RecallItem(BaseModel):
    memory_id: str
    title: str
    body: str
    trust_level: int
    memory_class: str
    memory_status: str
    visibility_scope: VisibilityScope
    use_policy: UsePolicy
    provenance: list[ProvenanceEntry] = Field(default_factory=list)
    relevance_score: float = 0.0


class RecallRequest(BaseModel):
    query: str
    visibility_target: VisibilityTarget = VisibilityTarget.cloud_context
    limit: int = 20
    min_trust_level: int = 1
    include_stale: bool = False
    workspace_root: str | None = None
    caller: str = "memopilot_ui"
    output_format: str = "full"
    max_output_tokens: int = 2000


class RecallResponse(BaseModel):
    items: list[RecallItem]
    trace_id: str
    context_pack_hash: str | None = None


class MemoryRecallService:
    """Recalls memory items using DB-backed search plus policy filters."""

    def __init__(
        self,
        db: DatabaseManager | aiosqlite.Connection,
        config: Config | None = None,
    ) -> None:
        self._db = db
        self._config = config
        self._vector_service = VectorIndexService(db, config) if config else None

    async def recall(self, request: RecallRequest) -> RecallResponse:
        conn = await self._get_connection()
        
        # Try hybrid search: FTS + vector search (if embeddings available)
        rows = await self._search_rows_hybrid(conn, request)

        included_items: list[RecallItem] = []
        included_ids: list[str] = []
        excluded_ids: list[str] = []

        for row in rows:
            use_policy = self._parse_use_policy(row["use_policy_json"], row["review_required"])
            if not self._row_is_included(row, request, use_policy):
                excluded_ids.append(str(row["id"]))
                continue

            item = self._build_item(row, use_policy)
            included_items.append(item)
            included_ids.append(item.memory_id)
            if len(included_items) >= request.limit:
                break

        context_pack_hash = hashlib.sha256(
            json.dumps(
                {
                    "query": request.query,
                    "visibility_target": request.visibility_target.value,
                    "included_ids": included_ids,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

        trace_id = await self.record_recall_trace(
            context_pack_hash=context_pack_hash,
            request_json=request.model_dump_json(),
            included_memory_ids=included_ids,
            excluded_memory_ids=excluded_ids,
            conn=conn,
        )
        return RecallResponse(
            items=included_items, trace_id=trace_id, context_pack_hash=context_pack_hash
        )

    async def record_recall_trace(
        self,
        *,
        context_pack_hash: str,
        included_memory_ids: list[str],
        excluded_memory_ids: list[str],
        request_json: str | None = None,
        conn: aiosqlite.Connection | None = None,
    ) -> str:
        active_conn = conn or await self._get_connection()
        trace_id = uuid.uuid4().hex
        await active_conn.execute(
            """
            INSERT INTO recall_traces (
                id, context_pack_hash, request_json,
                included_memory_ids_json, excluded_memory_ids_json
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                trace_id,
                context_pack_hash,
                request_json,
                json.dumps(included_memory_ids),
                json.dumps(excluded_memory_ids),
            ),
        )
        await active_conn.commit()
        return trace_id

    async def _get_connection(self) -> aiosqlite.Connection:
        if isinstance(self._db, DatabaseManager):
            return await self._db.connect()
        return self._db

    async def _search_rows_hybrid(
        self,
        conn: aiosqlite.Connection,
        request: RecallRequest,
    ):
        """Search using FTS + vector search (if available)."""
        fts_rows = await self._search_rows(conn, request)
        
        # If no vector service or query is empty, return FTS results only
        if not self._vector_service or not request.query.strip():
            return fts_rows
        
        # Try vector search for semantic enhancement
        try:
            embedding = await self._vector_service.embed_text(request.query)
            if not embedding:
                return fts_rows
            
            # Get semantic neighbors from vector index
            vector_results = await self._vector_service.search_vectors(
                embedding=embedding,
                entity_type="memory_item",
                limit=max(request.limit * 3, 30),
            )
            
            # Convert vector results to memory IDs with scores
            vector_scores: dict[str, float] = {}
            for result in vector_results:
                entity_id = result.get("entity_id")
                similarity = result.get("similarity", 0.0)
                if entity_id:
                    vector_scores[entity_id] = similarity
            
            # Merge FTS and vector results
            if vector_scores:
                return await self._merge_search_results(
                    conn, fts_rows, vector_scores, request
                )
        except Exception:
            # Vector search failed — return FTS results
            pass
        
        return fts_rows

    async def _merge_search_results(
        self,
        conn: aiosqlite.Connection,
        fts_rows: list[Any],
        vector_scores: dict[str, float],
        request: RecallRequest,
    ) -> list[Any]:
        """Merge FTS and vector results, re-ranking by combined score."""
        # Create score map for FTS results
        fts_scores: dict[str, tuple[int, Any]] = {}
        for idx, row in enumerate(fts_rows):
            memory_id = str(row["id"])
            fts_rank = row.get("fts_rank") or 0.0
            # Normalize FTS rank (lower is better, invert for scoring)
            fts_score = max(0.0, 1.0 - abs(fts_rank) / 100.0) if fts_rank else 0.5
            fts_scores[memory_id] = (idx, fts_score)
        
        # Combine all memory IDs from both sources
        all_memory_ids = set(fts_scores.keys()) | set(vector_scores.keys())
        
        # Score each result: weighted average of FTS and vector scores
        combined_scores: dict[str, float] = {}
        for memory_id in all_memory_ids:
            fts_score = fts_scores.get(memory_id, (999, 0.0))[1]
            vector_score = vector_scores.get(memory_id, 0.0)
            
            # Weight: 60% FTS, 40% vector (can be tuned)
            combined = (fts_score * 0.6) + (vector_score * 0.4)
            combined_scores[memory_id] = combined
        
        # Fetch full rows for all combined IDs (FTS might not have some)
        all_rows = list(fts_rows)
        fts_ids = {str(row["id"]) for row in fts_rows}
        
        # Fetch vector-only results that weren't in FTS
        vector_only_ids = all_memory_ids - fts_ids
        if vector_only_ids:
            id_list = [f"'{id_}'" for id_ in vector_only_ids]
            cursor = await conn.execute(
                f"""
                SELECT
                    id, title, body, trust_level,
                    COALESCE(memory_class, 'fact') AS memory_class,
                    COALESCE(memory_status, 'discovered') AS memory_status,
                    COALESCE(visibility_scope, 'workspace') AS visibility_scope,
                    use_policy_json, provenance_json, review_required, stale,
                    NULL AS fts_rank
                FROM memory_items
                WHERE id IN ({','.join(id_list)})
                """
            )
            vector_rows = await cursor.fetchall()
            all_rows.extend(vector_rows)
        
        # Sort by combined score (descending)
        all_rows.sort(
            key=lambda row: combined_scores.get(str(row["id"]), 0.0),
            reverse=True,
        )
        
        return all_rows

    async def _search_rows(
        self,
        conn: aiosqlite.Connection,
        request: RecallRequest,
    ):
        fetch_limit = max(request.limit * 5, 50)
        if request.query.strip():
            try:
                if request.workspace_root:
                    cursor = await conn.execute(
                        """
                        SELECT
                            m.id,
                            m.title,
                            m.body,
                            m.trust_level,
                            COALESCE(m.memory_class, 'fact') AS memory_class,
                            COALESCE(m.memory_status, 'discovered') AS memory_status,
                            COALESCE(m.visibility_scope, 'workspace') AS visibility_scope,
                            m.use_policy_json,
                            m.provenance_json,
                            m.review_required,
                            m.stale,
                            bm25(memory_fts) AS fts_rank
                        FROM memory_fts
                        JOIN memory_items AS m ON m.rowid = memory_fts.rowid
                        WHERE memory_fts MATCH ?
                          AND COALESCE(m.workspace_root, ?) = ?
                        ORDER BY fts_rank ASC, m.updated_at DESC
                        LIMIT ?
                        """,
                        (
                            request.query,
                            request.workspace_root,
                            request.workspace_root,
                            fetch_limit,
                        ),
                    )
                else:
                    cursor = await conn.execute(
                        """
                        SELECT
                            m.id,
                            m.title,
                            m.body,
                            m.trust_level,
                            COALESCE(m.memory_class, 'fact') AS memory_class,
                            COALESCE(m.memory_status, 'discovered') AS memory_status,
                            COALESCE(m.visibility_scope, 'workspace') AS visibility_scope,
                            m.use_policy_json,
                            m.provenance_json,
                            m.review_required,
                            m.stale,
                            bm25(memory_fts) AS fts_rank
                        FROM memory_fts
                        JOIN memory_items AS m ON m.rowid = memory_fts.rowid
                        WHERE memory_fts MATCH ?
                        ORDER BY fts_rank ASC, m.updated_at DESC
                        LIMIT ?
                        """,
                        (request.query, fetch_limit),
                    )
                return await cursor.fetchall()
            except aiosqlite.Error:
                pass

        like_query = f"%{request.query.strip()}%"
        if request.workspace_root:
            cursor = await conn.execute(
                """
                SELECT
                    id,
                    title,
                    body,
                    trust_level,
                    COALESCE(memory_class, 'fact') AS memory_class,
                    COALESCE(memory_status, 'discovered') AS memory_status,
                    COALESCE(visibility_scope, 'workspace') AS visibility_scope,
                    use_policy_json,
                    provenance_json,
                    review_required,
                    stale,
                    NULL AS fts_rank
                FROM memory_items
                WHERE (? = '' OR title LIKE ? OR body LIKE ?)
                  AND COALESCE(workspace_root, ?) = ?
                ORDER BY updated_at DESC, created_at DESC
                LIMIT ?
                """,
                (
                    request.query.strip(),
                    like_query,
                    like_query,
                    request.workspace_root,
                    request.workspace_root,
                    fetch_limit,
                ),
            )
        else:
            cursor = await conn.execute(
                """
                SELECT
                    id,
                    title,
                    body,
                    trust_level,
                    COALESCE(memory_class, 'fact') AS memory_class,
                    COALESCE(memory_status, 'discovered') AS memory_status,
                    COALESCE(visibility_scope, 'workspace') AS visibility_scope,
                    use_policy_json,
                    provenance_json,
                    review_required,
                    stale,
                    NULL AS fts_rank
                FROM memory_items
                WHERE (? = '' OR title LIKE ? OR body LIKE ?)
                ORDER BY updated_at DESC, created_at DESC
                LIMIT ?
                """,
                (request.query.strip(), like_query, like_query, fetch_limit),
            )
        return await cursor.fetchall()

    def _row_is_included(self, row, request: RecallRequest, use_policy: UsePolicy) -> bool:
        trust_level = int(row["trust_level"] or 0)
        if trust_level < request.min_trust_level:
            return False

        memory_status = str(row["memory_status"] or "discovered")
        if memory_status in {"superseded", "rejected"}:
            return False
        if not request.include_stale and (memory_status == "stale" or bool(row["stale"])):
            return False

        visibility_scope = str(row["visibility_scope"] or VisibilityScope.workspace.value)
        if (
            request.visibility_target == VisibilityTarget.cloud_context
            and visibility_scope == VisibilityScope.local_only.value
        ):
            return False

        return self._policy_allows_target(use_policy, request.visibility_target)

    def _policy_allows_target(
        self,
        use_policy: UsePolicy,
        target: VisibilityTarget,
    ) -> bool:
        if target == VisibilityTarget.cloud_context:
            return use_policy.allowed_in_cloud_context
        if target == VisibilityTarget.local_context:
            return use_policy.allowed_in_local_context
        if target == VisibilityTarget.patch_generation:
            return use_policy.allowed_for_patch_generation
        if target == VisibilityTarget.rule_enforcement:
            return use_policy.allowed_for_rule_enforcement
        return True

    def _parse_use_policy(
        self, raw_policy: str | None, review_required: int | bool | None
    ) -> UsePolicy:
        review_flag = bool(review_required)
        if not raw_policy:
            return UsePolicy(review_required_before_use=review_flag)
        try:
            payload = json.loads(raw_policy)
        except json.JSONDecodeError:
            return UsePolicy(review_required_before_use=review_flag)
        if isinstance(payload, dict):
            payload.setdefault("review_required_before_use", review_flag)
            try:
                return UsePolicy.model_validate(payload)
            except ValidationError:
                return UsePolicy(review_required_before_use=review_flag)
        return UsePolicy(review_required_before_use=review_flag)

    def _parse_provenance(self, raw_provenance: str | None) -> list[ProvenanceEntry]:
        if not raw_provenance:
            return []
        try:
            payload = json.loads(raw_provenance)
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, list):
            return []
        entries: list[ProvenanceEntry] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                entries.append(ProvenanceEntry.model_validate(item))
            except ValidationError:
                continue
        return entries

    @staticmethod
    def _recency_boost(updated_at: str | None) -> float:
        """Compute a small recency multiplier (1.0–1.2) based on updated_at age."""
        if not updated_at:
            return 1.0
        try:
            from datetime import datetime as _dt
            dt = _dt.fromisoformat(updated_at.rstrip("Z"))
            days_old = max(0, (_dt.now() - dt).days)
            if days_old <= 7:
                return 1.2
            if days_old <= 30:
                return 1.1
            if days_old <= 90:
                return 1.05
        except Exception:
            pass
        return 1.0

    def _build_item(self, row, use_policy: UsePolicy) -> RecallItem:
        rank = row["fts_rank"]
        relevance_score = 0.0
        if rank is not None:
            # bm25() returns negative values — negate so higher = more relevant
            normalized_rank = max(-float(rank), 0.0)
            base_score = 1.0 / (1.0 + normalized_rank)
            try:
                updated_at = row["updated_at"] if "updated_at" in row.keys() else None
            except Exception:
                updated_at = None
            # Recency boost is additive to the score, not multiplicative, to avoid 1.0 ceiling clipping
            boost = self._recency_boost(updated_at) - 1.0  # 0.0–0.2
            relevance_score = min(1.0, base_score + base_score * boost)

        return RecallItem(
            memory_id=str(row["id"]),
            title=str(row["title"]),
            body=str(row["body"]),
            trust_level=int(row["trust_level"]),
            memory_class=str(row["memory_class"]),
            memory_status=str(row["memory_status"]),
            visibility_scope=VisibilityScope(str(row["visibility_scope"])),
            use_policy=use_policy,
            provenance=self._parse_provenance(row["provenance_json"]),
            relevance_score=relevance_score,
        )
