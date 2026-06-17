"""Task history pattern detection for learning feed surfacing."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .config import Config
from .db import DatabaseManager


@dataclass(frozen=True)
class TaskPattern:
    pattern_type: str
    context_path: str
    details: dict[str, object]
    suggestion: str


@dataclass(frozen=True)
class SimilarTask:
    task_id: str
    user_request: str
    status: str
    model_used: str | None
    cost_usd: float
    created_at: str
    rejection_reason: str | None


class TaskPatternDetector:
    """Detects recurring task history patterns using deterministic SQL queries."""

    _PATTERN_LOOKBACK_DAYS = 30
    _SIMILAR_LOOKBACK_DAYS = 90

    def __init__(self, *, config: Config, db: DatabaseManager) -> None:
        self._config = config
        self._db = db

    async def detect_patterns(self, workspace_root: str) -> list[TaskPattern]:
        workspace = self._resolve_workspace_root(workspace_root)

        frequent_failures = await self._detect_frequent_failures(workspace)
        model_escalations = await self._detect_model_escalations(workspace)
        patterns = [*frequent_failures, *model_escalations]

        await self._store_patterns(workspace, patterns)
        return patterns

    async def find_similar_tasks(
        self,
        context_path: str,
        workspace_root: str,
        limit: int = 3,
    ) -> list[SimilarTask]:
        normalized_context = _normalize_path(context_path)
        if not normalized_context:
            return []

        workspace = self._resolve_workspace_root(workspace_root)
        basename = normalized_context.rsplit("/", 1)[-1]
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            WITH recent_tasks AS (
                SELECT
                    tr.id AS task_id,
                    tr.user_request,
                    tr.status,
                    tr.selected_model AS model_used,
                    COALESCE(tr.actual_cost, tr.estimated_cost, 0.0) AS cost_usd,
                    tr.created_at,
                    MAX(
                        CASE
                            WHEN EXISTS (
                                SELECT 1
                                FROM json_each(COALESCE(pa.files_changed_json, '[]')) AS file
                                WHERE lower(replace(CAST(file.value AS TEXT), '\\', '/')) = ?
                                   OR lower(replace(CAST(file.value AS TEXT), '\\', '/')) LIKE ?
                            ) THEN 1
                            ELSE 0
                        END
                    ) AS file_match,
                    MAX(
                        CASE
                            WHEN lower(replace(COALESCE(tr.context_pack_path, ''), '\\', '/')) LIKE ?
                            THEN 1
                            ELSE 0
                        END
                    ) AS pack_match,
                    MAX(COALESCE(pa.rejection_reason, '')) AS rejection_reason
                FROM task_runs AS tr
                LEFT JOIN patch_attempts AS pa ON pa.task_run_id = tr.id
                WHERE COALESCE(tr.workspace_root, '') = ?
                  AND datetime(tr.created_at) >= datetime('now', ?)
                GROUP BY
                    tr.id,
                    tr.user_request,
                    tr.status,
                    tr.selected_model,
                    cost_usd,
                    tr.created_at
            )
            SELECT
                task_id,
                user_request,
                status,
                model_used,
                cost_usd,
                created_at,
                NULLIF(rejection_reason, '') AS rejection_reason
            FROM recent_tasks
            WHERE file_match = 1 OR pack_match = 1
            ORDER BY datetime(created_at) DESC, task_id DESC
            LIMIT ?
            """,
            (
                normalized_context,
                f"%/{basename}",
                f"%{basename}%",
                workspace,
                f"-{self._SIMILAR_LOOKBACK_DAYS} days",
                max(1, limit),
            ),
        )
        rows = await cursor.fetchall()
        return [
            SimilarTask(
                task_id=str(row["task_id"]),
                user_request=str(row["user_request"] or ""),
                status=str(row["status"] or ""),
                model_used=str(row["model_used"]) if row["model_used"] else None,
                cost_usd=float(row["cost_usd"] or 0.0),
                created_at=str(row["created_at"] or ""),
                rejection_reason=(
                    str(row["rejection_reason"]) if row["rejection_reason"] is not None else None
                ),
            )
            for row in rows
        ]

    async def _detect_frequent_failures(self, workspace_root: str) -> list[TaskPattern]:
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            WITH changed_files AS (
                SELECT
                    lower(replace(CAST(file.value AS TEXT), '\\', '/')) AS context_path,
                    pa.id AS patch_attempt_id,
                    tr.created_at,
                    CASE
                        WHEN lower(COALESCE(pa.validation_status, '')) IN (
                            'failed', 'rejected', 'error', 'cancelled'
                        )
                          OR lower(COALESCE(tr.status, '')) IN ('failed', 'blocked', 'cancelled')
                          OR pa.rejection_reason IS NOT NULL
                        THEN 1
                        ELSE 0
                    END AS failed_flag
                FROM patch_attempts AS pa
                JOIN task_runs AS tr ON tr.id = pa.task_run_id
                JOIN json_each(COALESCE(pa.files_changed_json, '[]')) AS file
                WHERE COALESCE(tr.workspace_root, '') = ?
                  AND datetime(tr.created_at) >= datetime('now', ?)
            )
            SELECT
                context_path,
                COUNT(DISTINCT patch_attempt_id) AS patch_count,
                COUNT(DISTINCT CASE WHEN failed_flag = 1 THEN patch_attempt_id END) AS failure_count,
                MAX(created_at) AS last_seen_at
            FROM changed_files
            WHERE context_path <> ''
            GROUP BY context_path
            HAVING COUNT(DISTINCT patch_attempt_id) >= 3
               AND (
                    COUNT(DISTINCT CASE WHEN failed_flag = 1 THEN patch_attempt_id END) * 1.0
                    / COUNT(DISTINCT patch_attempt_id)
               ) >= 0.5
            ORDER BY
                (
                    COUNT(DISTINCT CASE WHEN failed_flag = 1 THEN patch_attempt_id END) * 1.0
                    / COUNT(DISTINCT patch_attempt_id)
                ) DESC,
                COUNT(DISTINCT patch_attempt_id) DESC,
                context_path ASC
            """,
            (workspace_root, f"-{self._PATTERN_LOOKBACK_DAYS} days"),
        )
        rows = await cursor.fetchall()

        patterns: list[TaskPattern] = []
        for row in rows:
            patch_count = int(row["patch_count"] or 0)
            failure_count = int(row["failure_count"] or 0)
            failure_rate = round(failure_count / patch_count, 2) if patch_count else 0.0
            context_path = str(row["context_path"])
            patterns.append(
                TaskPattern(
                    pattern_type="frequent_failures",
                    context_path=context_path,
                    details={
                        "patch_count": patch_count,
                        "failure_count": failure_count,
                        "failure_rate": failure_rate,
                        "lookback_days": self._PATTERN_LOOKBACK_DAYS,
                        "last_seen_at": str(row["last_seen_at"] or ""),
                    },
                    suggestion=(
                        f"Add a focused plan and regression coverage before patching {context_path}; "
                        f"{failure_count} of {patch_count} recent patch attempts failed."
                    ),
                )
            )
        return patterns

    async def _detect_model_escalations(self, workspace_root: str) -> list[TaskPattern]:
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            WITH changed_files AS (
                SELECT
                    lower(replace(CAST(file.value AS TEXT), '\\', '/')) AS context_path,
                    tr.id AS task_run_id,
                    lower(COALESCE(tr.selected_model, '')) AS model_used,
                    COALESCE(tr.actual_cost, tr.estimated_cost, 0.0) AS cost_usd,
                    CASE
                        WHEN COALESCE(tr.routing_escalation_source, '') <> ''
                          OR lower(COALESCE(tr.selected_model, '')) LIKE '%sonnet%'
                          OR lower(COALESCE(tr.selected_model, '')) LIKE '%opus%'
                          OR COALESCE(tr.actual_cost, tr.estimated_cost, 0.0) >= 0.05
                        THEN 1
                        ELSE 0
                    END AS escalated_flag
                FROM patch_attempts AS pa
                JOIN task_runs AS tr ON tr.id = pa.task_run_id
                JOIN json_each(COALESCE(pa.files_changed_json, '[]')) AS file
                WHERE COALESCE(tr.workspace_root, '') = ?
                  AND datetime(tr.created_at) >= datetime('now', ?)
                  AND COALESCE(tr.selected_model, '') <> ''
            )
            SELECT
                context_path,
                COUNT(DISTINCT task_run_id) AS task_count,
                COUNT(DISTINCT CASE WHEN escalated_flag = 1 THEN task_run_id END) AS escalated_count,
                ROUND(AVG(cost_usd), 4) AS avg_cost,
                GROUP_CONCAT(DISTINCT model_used) AS models_used
            FROM changed_files
            WHERE context_path <> ''
            GROUP BY context_path
            HAVING COUNT(DISTINCT task_run_id) >= 2
               AND COUNT(DISTINCT CASE WHEN escalated_flag = 1 THEN task_run_id END)
                   = COUNT(DISTINCT task_run_id)
            ORDER BY AVG(cost_usd) DESC, COUNT(DISTINCT task_run_id) DESC, context_path ASC
            """,
            (workspace_root, f"-{self._PATTERN_LOOKBACK_DAYS} days"),
        )
        rows = await cursor.fetchall()

        patterns: list[TaskPattern] = []
        for row in rows:
            context_path = str(row["context_path"])
            task_count = int(row["task_count"] or 0)
            escalated_count = int(row["escalated_count"] or 0)
            avg_cost = float(row["avg_cost"] or 0.0)
            models = [item for item in str(row["models_used"] or "").split(",") if item]
            patterns.append(
                TaskPattern(
                    pattern_type="model_escalation",
                    context_path=context_path,
                    details={
                        "task_count": task_count,
                        "escalated_count": escalated_count,
                        "avg_cost_usd": round(avg_cost, 4),
                        "models": models,
                        "lookback_days": self._PATTERN_LOOKBACK_DAYS,
                    },
                    suggestion=(
                        f"Pre-build deeper context for {context_path} or route it to an expensive model "
                        f"by default; {escalated_count} recent tasks escalated there."
                    ),
                )
            )
        return patterns

    async def _store_patterns(self, workspace_root: str, patterns: list[TaskPattern]) -> None:
        conn = await self._db.connect()
        current_ids = [self._pattern_id(workspace_root, pattern) for pattern in patterns]

        if current_ids:
            placeholders = ", ".join("?" for _ in current_ids)
            await conn.execute(
                f"DELETE FROM task_patterns WHERE workspace_root = ? AND dismissed = 0 AND id NOT IN ({placeholders})",
                (workspace_root, *current_ids),
            )
        else:
            await conn.execute(
                "DELETE FROM task_patterns WHERE workspace_root = ? AND dismissed = 0",
                (workspace_root,),
            )

        for pattern in patterns:
            pattern_id = self._pattern_id(workspace_root, pattern)
            await conn.execute(
                """
                INSERT INTO task_patterns (
                    id,
                    pattern_type,
                    context_path,
                    details_json,
                    suggestion,
                    workspace_root,
                    surfaced_at,
                    dismissed
                )
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'), 0)
                ON CONFLICT(id) DO UPDATE SET
                    pattern_type = excluded.pattern_type,
                    context_path = excluded.context_path,
                    details_json = excluded.details_json,
                    suggestion = excluded.suggestion,
                    workspace_root = excluded.workspace_root,
                    surfaced_at = excluded.surfaced_at,
                    dismissed = 0
                """,
                (
                    pattern_id,
                    pattern.pattern_type,
                    pattern.context_path,
                    json.dumps(pattern.details, sort_keys=True),
                    pattern.suggestion,
                    workspace_root,
                ),
            )
        await conn.commit()

    def _pattern_id(self, workspace_root: str, pattern: TaskPattern) -> str:
        payload = f"{workspace_root}|{pattern.pattern_type}|{pattern.context_path}".encode()
        return hashlib.sha1(payload).hexdigest()

    def _resolve_workspace_root(self, workspace_root: str) -> str:
        if workspace_root:
            return workspace_root
        return str(self._config.workspace_path.resolve())


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").strip().lower()
