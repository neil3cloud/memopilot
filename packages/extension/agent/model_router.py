"""Outcome-aware model routing helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import aiosqlite

from .db import DatabaseManager


class ModelTier(str, Enum):
    LOCAL = "local"
    CHEAP_CLOUD = "cheap_cloud"
    FRONTIER = "frontier"


TIER_ORDER = {
    ModelTier.LOCAL: 0,
    ModelTier.CHEAP_CLOUD: 1,
    ModelTier.FRONTIER: 2,
}

_FRONTIER_MODELS = {"claude-3.5-sonnet"}
_LOCAL_TASK_TYPES = {"auto", "fix", "refactor", "test"}
_FRONTIER_TASK_TYPES = {"architecture", "complex"}


@dataclass(frozen=True)
class RoutingDecision:
    tier: ModelTier
    reason: str
    base_tier: ModelTier
    escalation_source: str | None = None


def _normalize_file_path(file_path: str) -> str:
    return file_path.replace("\\", "/").lower()


def _classify_base_tier(task_type: str, model_max_tokens: int) -> ModelTier:
    normalized_task = (task_type or "auto").strip().lower().replace("-", "_")
    if model_max_tokens <= 32_000 and normalized_task in _LOCAL_TASK_TYPES:
        return ModelTier.LOCAL
    if normalized_task in _FRONTIER_TASK_TYPES or model_max_tokens > 128_000:
        return ModelTier.FRONTIER
    return ModelTier.CHEAP_CLOUD


async def get_outcome_routing_hint(
    task_type: str,
    files_in_context: list[str] | None,
    db_conn: aiosqlite.Connection,
    lookback_days: int = 30,
) -> tuple[ModelTier | None, str | None]:
    del task_type
    if not files_in_context:
        return None, None

    normalized_files = []
    for file_path in files_in_context:
        normalized = _normalize_file_path(file_path)
        if normalized and normalized not in normalized_files:
            normalized_files.append(normalized)

    for file_path in normalized_files:
        cursor = await db_conn.execute(
            """
            SELECT COUNT(DISTINCT tr.id) AS failure_count
            FROM task_runs AS tr
            LEFT JOIN patch_attempts AS pa ON pa.task_run_id = tr.id
            WHERE tr.status = 'failed'
              AND COALESCE(tr.selected_model, '') NOT IN ('claude-3.5-sonnet')
              AND datetime(tr.created_at) >= datetime('now', ?)
              AND EXISTS (
                  SELECT 1
                  FROM json_each(COALESCE(pa.files_changed_json, '[]'))
                  WHERE lower(replace(CAST(value AS TEXT), '\\', '/')) = ?
              )
            """,
            (f"-{lookback_days} days", file_path),
        )
        row = await cursor.fetchone()
        failure_count = int(row[0] or 0) if row else 0
        if failure_count >= 2:
            return (
                ModelTier.FRONTIER,
                (
                    f"Escalating to frontier because {file_path} has {failure_count} failed "
                    f"non-frontier attempts in the last {lookback_days} days."
                ),
            )

    return None, None


async def route_with_outcome(
    task_type: str,
    files_in_context: list[str] | None,
    model_max_tokens: int,
    db: DatabaseManager,
) -> RoutingDecision:
    base_tier = _classify_base_tier(task_type, model_max_tokens)
    normalized_task = (task_type or "auto").strip().lower().replace("-", "_")

    if base_tier == ModelTier.LOCAL:
        return RoutingDecision(
            tier=base_tier,
            base_tier=base_tier,
            reason=(
                f"Routing to local for task_type={normalized_task} because the request fits the "
                "32K local context window. Frontier escalation would only trigger after 2 failed "
                "non-frontier attempts on the same file within 30 days, and local routes are not "
                "escalated automatically."
            ),
        )

    if base_tier == ModelTier.FRONTIER:
        return RoutingDecision(
            tier=base_tier,
            base_tier=base_tier,
            reason=(
                f"Routing directly to frontier for task_type={normalized_task} because the task "
                "already exceeds the cheap-cloud lane. Frontier escalation would otherwise "
                "trigger after 2 failed non-frontier attempts on the same file within 30 days, "
                "and no higher escalation tier exists."
            ),
        )

    conn = await db.connect()
    hinted_tier, hinted_reason = await get_outcome_routing_hint(
        task_type=task_type,
        files_in_context=files_in_context,
        db_conn=conn,
    )
    if hinted_tier is not None and TIER_ORDER[hinted_tier] > TIER_ORDER[base_tier]:
        return RoutingDecision(
            tier=hinted_tier,
            base_tier=base_tier,
            escalation_source="recent_file_failures",
            reason=(
                f"{hinted_reason} Without repeated file failures, this request would stay on "
                "cheap_cloud."
            ),
        )

    return RoutingDecision(
        tier=base_tier,
        base_tier=base_tier,
        reason=(
            f"Routing to cheap_cloud for task_type={normalized_task} based on the current "
            "context budget. Frontier escalation would trigger after 2 failed non-frontier "
            "attempts on the same file within 30 days."
        ),
    )
