"""Structured rejection handling: per-category actions that improve the next attempt.

When a developer rejects a patch with a category, each category triggers a specific
learning action that stores constraints/rules for future context packs.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from .config import Config
from .db import DatabaseManager


@dataclass(frozen=True)
class RejectionHandlerResult:
    category: str
    action_taken: str
    memory_item_id: str | None = None
    suggestion: str | None = None


class RejectionHandlerService:
    """Handles structured rejections by category, creating targeted learning artifacts."""

    def __init__(self, *, config: Config, db: DatabaseManager) -> None:
        self._config = config
        self._db = db

    async def handle_rejection(
        self,
        *,
        patch_attempt_id: str,
        category: str,
        reason: str,
        workspace_root: str | None = None,
    ) -> RejectionHandlerResult:
        """Dispatch rejection handling by category.

        Each category produces a different type of learning artifact that
        improves subsequent patch attempts for the same module.
        """
        handlers = {
            "wrong_approach": self._handle_wrong_approach,
            "missed_business_rule": self._handle_missed_business_rule,
            "wrong_file": self._handle_wrong_scope,
            "broke_existing_behavior": self._handle_broke_behavior,
            "incomplete": self._handle_incomplete,
            "other": self._handle_other,
        }

        handler = handlers.get(category, self._handle_other)
        return await handler(
            patch_attempt_id=patch_attempt_id,
            reason=reason,
            workspace_root=workspace_root,
        )

    async def _handle_wrong_approach(
        self, *, patch_attempt_id: str, reason: str, workspace_root: str | None
    ) -> RejectionHandlerResult:
        """Store the rejected approach so next attempt tries differently."""
        conn = await self._db.connect()

        # Get task description from the patch attempt
        cursor = await conn.execute(
            """SELECT tr.user_request FROM task_runs tr
               JOIN patch_attempts pa ON pa.task_run_id = tr.id
               WHERE pa.id = ?""",
            (patch_attempt_id,),
        )
        row = await cursor.fetchone()
        task_desc = row[0] if row else "unknown task"

        # Store as a lesson memory item
        memory_id = await self._store_rejection_memory(
            conn=conn,
            title=f"Rejected approach for: {task_desc[:80]}",
            body=(
                f"Previous patch attempt was rejected — wrong approach.\n"
                f"Reason: {reason}\n\n"
                f"Instruction: Try a different implementation strategy. "
                f"Do not repeat the rejected approach."
            ),
            memory_class="lesson",
            workspace_root=workspace_root,
            tags={"rejection_category": "wrong_approach", "patch_attempt_id": patch_attempt_id},
        )

        return RejectionHandlerResult(
            category="wrong_approach",
            action_taken="Stored rejected approach as lesson — next attempt will try differently",
            memory_item_id=memory_id,
        )

    async def _handle_missed_business_rule(
        self, *, patch_attempt_id: str, reason: str, workspace_root: str | None
    ) -> RejectionHandlerResult:
        """Store the missed rule as a pending instruction for the developer to confirm."""
        conn = await self._db.connect()

        memory_id = await self._store_rejection_memory(
            conn=conn,
            title=f"Business rule: {reason[:100]}",
            body=(
                f"This rule was missed in a previous patch attempt.\n"
                f"Rule: {reason}\n\n"
                f"This constraint must be followed in future patches for this module."
            ),
            memory_class="instruction",
            memory_status="pending_review",
            review_required=True,
            workspace_root=workspace_root,
            tags={"rejection_category": "missed_business_rule", "patch_attempt_id": patch_attempt_id},
        )

        return RejectionHandlerResult(
            category="missed_business_rule",
            action_taken="Created pending instruction for review — confirm to add as permanent rule",
            memory_item_id=memory_id,
            suggestion="Review and confirm this rule in the Memory Manager",
        )

    async def _handle_wrong_scope(
        self, *, patch_attempt_id: str, reason: str, workspace_root: str | None
    ) -> RejectionHandlerResult:
        """Store a negative scope rule: do not modify these files for this task."""
        conn = await self._db.connect()

        # Get files changed by the rejected patch
        cursor = await conn.execute(
            "SELECT files_changed_json, task_run_id FROM patch_attempts WHERE id = ?",
            (patch_attempt_id,),
        )
        row = await cursor.fetchone()
        files_changed = []
        if row and row[0]:
            try:
                files_changed = json.loads(row[0])
            except (json.JSONDecodeError, TypeError):
                pass

        files_str = ", ".join(files_changed[:5]) if files_changed else "unknown files"

        memory_id = await self._store_rejection_memory(
            conn=conn,
            title=f"Scope restriction: do not modify {files_str}",
            body=(
                f"Previous patch was rejected for modifying files outside the intended scope.\n"
                f"Files that should NOT have been modified: {files_str}\n"
                f"Reason: {reason}\n\n"
                f"Instruction: Do not modify these files when working on this task."
            ),
            memory_class="instruction",
            workspace_root=workspace_root,
            tags={
                "rejection_category": "wrong_file",
                "patch_attempt_id": patch_attempt_id,
                "restricted_files": files_changed[:10],
            },
        )

        return RejectionHandlerResult(
            category="wrong_file",
            action_taken=f"Stored scope restriction — {files_str} excluded from future patches",
            memory_item_id=memory_id,
        )

    async def _handle_broke_behavior(
        self, *, patch_attempt_id: str, reason: str, workspace_root: str | None
    ) -> RejectionHandlerResult:
        """Store as evidence — may indicate missing test coverage."""
        conn = await self._db.connect()

        memory_id = await self._store_rejection_memory(
            conn=conn,
            title=f"Regression risk: {reason[:80]}",
            body=(
                f"A patch was rejected because it would break existing behavior.\n"
                f"Behavior that must be preserved: {reason}\n\n"
                f"Consider adding a test to guard this behavior before the next change."
            ),
            memory_class="fact",
            workspace_root=workspace_root,
            tags={"rejection_category": "broke_existing_behavior", "patch_attempt_id": patch_attempt_id},
        )

        return RejectionHandlerResult(
            category="broke_existing_behavior",
            action_taken="Stored regression evidence — suggests missing test coverage",
            memory_item_id=memory_id,
            suggestion="Consider adding a test for the behavior that was broken",
        )

    async def _handle_incomplete(
        self, *, patch_attempt_id: str, reason: str, workspace_root: str | None
    ) -> RejectionHandlerResult:
        """Feed back into Plan mode for a more complete plan."""
        conn = await self._db.connect()

        memory_id = await self._store_rejection_memory(
            conn=conn,
            title=f"Incomplete patch: {reason[:80]}",
            body=(
                f"Previous patch was rejected as incomplete.\n"
                f"What was missing: {reason}\n\n"
                f"Instruction: Generate a more complete plan before the next attempt. "
                f"Ensure all required steps are included."
            ),
            memory_class="lesson",
            workspace_root=workspace_root,
            tags={"rejection_category": "incomplete", "patch_attempt_id": patch_attempt_id},
        )

        return RejectionHandlerResult(
            category="incomplete",
            action_taken="Stored incompleteness feedback — next attempt will use Plan mode for completeness",
            memory_item_id=memory_id,
            suggestion="Use Plan mode to generate a complete plan before patching",
        )

    async def _handle_other(
        self, *, patch_attempt_id: str, reason: str, workspace_root: str | None
    ) -> RejectionHandlerResult:
        """Generic rejection — store the reason as a lesson."""
        conn = await self._db.connect()

        memory_id = await self._store_rejection_memory(
            conn=conn,
            title=f"Rejection note: {reason[:80]}",
            body=f"Patch rejected. Reason: {reason}",
            memory_class="lesson",
            workspace_root=workspace_root,
            tags={"rejection_category": "other", "patch_attempt_id": patch_attempt_id},
        )

        return RejectionHandlerResult(
            category="other",
            action_taken="Stored rejection reason as lesson",
            memory_item_id=memory_id,
        )

    async def _store_rejection_memory(
        self,
        *,
        conn,
        title: str,
        body: str,
        memory_class: str,
        workspace_root: str | None,
        tags: dict,
        memory_status: str = "confirmed",
        review_required: bool = False,
    ) -> str:
        """Store a rejection-derived memory item."""
        from pathlib import Path

        memory_id = uuid.uuid4().hex
        now = datetime.now(UTC).isoformat()

        normalized_root = None
        if workspace_root:
            normalized_root = str(Path(workspace_root).resolve())
        elif self._config:
            normalized_root = str(self._config.workspace_path.resolve())

        trust_level = 3 if memory_status == "confirmed" else 4
        tags_json = json.dumps({**tags, "pending_approval": review_required})

        await conn.execute(
            """
            INSERT INTO memory_items
            (
                id, type, title, body, source, source_path, source_hash,
                trust_level, tags_json, stale, memory_class, memory_status,
                visibility_scope, reusable, review_required, workspace_root,
                created_at, updated_at
            )
            VALUES (
                ?, 'ai_summary', ?, ?, 'rejection_learning', NULL, NULL,
                ?, ?, 0, ?, ?,
                'workspace', 1, ?, ?,
                ?, ?
            )
            """,
            (
                memory_id,
                title,
                body,
                trust_level,
                tags_json,
                memory_class,
                memory_status,
                1 if review_required else 0,
                normalized_root,
                now,
                now,
            ),
        )
        await conn.commit()
        return memory_id

    async def get_rejection_constraints(
        self,
        *,
        context_path: str | None = None,
        workspace_root: str | None = None,
        limit: int = 5,
    ) -> list[str]:
        """Retrieve rejection-derived constraints for context injection.

        Returns constraint text from prior rejections relevant to the
        current module/workspace, for inclusion in the patch context pack.
        """
        from pathlib import Path

        conn = await self._db.connect()

        normalized_root = None
        if workspace_root:
            normalized_root = str(Path(workspace_root).resolve())
        elif self._config:
            normalized_root = str(self._config.workspace_path.resolve())

        query = """
            SELECT title, body FROM memory_items
            WHERE source = 'rejection_learning'
              AND memory_status = 'confirmed'
              AND stale = 0
              AND memory_class IN ('instruction', 'lesson')
        """
        params: list[object] = []

        if normalized_root:
            query += " AND COALESCE(workspace_root, ?) = ?"
            params.extend([normalized_root, normalized_root])

        if context_path:
            query += " AND (body LIKE ? OR title LIKE ?)"
            pattern = f"%{context_path}%"
            params.extend([pattern, pattern])

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cursor = await conn.execute(query, tuple(params))
        rows = await cursor.fetchall()

        return [
            f"[PRIOR REJECTION] {row[0]}: {row[1][:200]}"
            for row in rows
        ]
