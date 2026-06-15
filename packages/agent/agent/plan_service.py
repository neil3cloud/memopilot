"""Plan mode service: generate, store, and recall plans as decision memory items.

Plans are stored as memory_items with memory_class='decision' and feed directly
into subsequent patch generation as high-priority context constraints.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from .config import Config
from .db import DatabaseManager


@dataclass(frozen=True)
class PlanStep:
    step_number: int
    description: str
    target_file: str | None = None
    target_symbol: str | None = None


@dataclass(frozen=True)
class PlanResult:
    plan_id: str
    memory_item_id: str
    title: str
    steps: list[PlanStep]
    raw_text: str
    created_at: str


@dataclass(frozen=True)
class PlanRecallResult:
    memory_item_id: str
    title: str
    body: str
    trust_level: int
    created_at: str


class PlanModeService:
    """Manages plan generation storage and recall for patch context injection."""

    def __init__(self, *, config: Config, db: DatabaseManager) -> None:
        self._config = config
        self._db = db

    async def store_plan(
        self,
        *,
        title: str,
        steps: list[PlanStep],
        task_description: str,
        workspace_root: str | None = None,
        task_run_id: str | None = None,
    ) -> PlanResult:
        """Store a plan as a confirmed decision memory item.

        Plans are stored immediately as confirmed (not pending_review) because
        the developer deliberately triggered plan generation — this is an
        intentional decision, not an AI suggestion.
        """
        conn = await self._db.connect()
        memory_item_id = uuid.uuid4().hex
        now = datetime.now(UTC).isoformat()

        # Build structured plan text
        steps_text = "\n".join(
            f"  Step {s.step_number}: {s.description}"
            + (f" [{s.target_file}]" if s.target_file else "")
            for s in steps
        )
        plan_body = f"Task: {task_description}\n\nPlan:\n{steps_text}"

        # Store as decision memory item — confirmed immediately
        tags_json = json.dumps({
            "pending_approval": False,
            "approved": True,
            "plan_source": "plan_mode",
            "task_run_id": task_run_id,
            "steps_count": len(steps),
            "steps": [
                {
                    "step_number": s.step_number,
                    "description": s.description,
                    "target_file": s.target_file,
                    "target_symbol": s.target_symbol,
                }
                for s in steps
            ],
        })

        normalized_root = None
        if workspace_root:
            from pathlib import Path
            normalized_root = str(Path(workspace_root).resolve())
        elif self._config:
            normalized_root = str(self._config.workspace_path.resolve())

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
                ?, 'ai_summary', ?, ?, 'plan_mode', NULL, NULL,
                3, ?, 0, 'decision', 'confirmed',
                'workspace', 1, 0, ?,
                ?, ?
            )
            """,
            (
                memory_item_id,
                title,
                plan_body,
                tags_json,
                normalized_root,
                now,
                now,
            ),
        )

        # Link plan to task_run if provided
        if task_run_id:
            await conn.execute(
                "UPDATE task_runs SET plan_memory_id = ? WHERE id = ?",
                (memory_item_id, task_run_id),
            )

        await conn.commit()

        return PlanResult(
            plan_id=memory_item_id,
            memory_item_id=memory_item_id,
            title=title,
            steps=steps,
            raw_text=plan_body,
            created_at=now,
        )

    async def recall_plans_for_context(
        self,
        *,
        workspace_root: str | None = None,
        module_path: str | None = None,
        limit: int = 3,
    ) -> list[PlanRecallResult]:
        """Recall confirmed decision plans for inclusion in patch context.

        Returns plans relevant to the current workspace/module, ordered by
        most recent first. These are injected into the context pack with
        elevated priority.
        """
        conn = await self._db.connect()

        normalized_root = None
        if workspace_root:
            from pathlib import Path
            normalized_root = str(Path(workspace_root).resolve())
        elif self._config:
            normalized_root = str(self._config.workspace_path.resolve())

        query = """
            SELECT id, title, body, trust_level, created_at
            FROM memory_items
            WHERE memory_class = 'decision'
              AND memory_status = 'confirmed'
              AND stale = 0
        """
        params: list[object] = []

        if normalized_root:
            query += " AND COALESCE(workspace_root, ?) = ?"
            params.extend([normalized_root, normalized_root])

        if module_path:
            query += " AND (body LIKE ? OR title LIKE ?)"
            pattern = f"%{module_path}%"
            params.extend([pattern, pattern])

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cursor = await conn.execute(query, tuple(params))
        rows = await cursor.fetchall()

        return [
            PlanRecallResult(
                memory_item_id=row[0],
                title=row[1],
                body=row[2],
                trust_level=row[3],
                created_at=row[4],
            )
            for row in rows
        ]

    async def link_plan_to_patch(
        self,
        *,
        patch_attempt_id: str,
        plan_memory_id: str,
    ) -> None:
        """Link a plan to a patch attempt for traceability."""
        conn = await self._db.connect()
        await conn.execute(
            "UPDATE patch_attempts SET plan_memory_id = ? WHERE id = ?",
            (plan_memory_id, patch_attempt_id),
        )
        await conn.commit()

    async def check_plan_compliance(
        self,
        *,
        plan_memory_id: str,
        files_changed: list[str],
    ) -> list[str]:
        """Check if a patch contradicts its source plan.

        Returns a list of compliance warning messages. Empty list means
        the patch is consistent with the plan.
        """
        conn = await self._db.connect()
        cursor = await conn.execute(
            "SELECT title, body, tags_json FROM memory_items WHERE id = ?",
            (plan_memory_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return []

        _title, body, tags_json = row
        warnings: list[str] = []

        # Extract planned target files from steps
        planned_files: set[str] = set()
        try:
            tags = json.loads(tags_json) if tags_json else {}
            steps = tags.get("steps", [])
            for step in steps:
                target = step.get("target_file")
                if target:
                    planned_files.add(target)
        except (json.JSONDecodeError, TypeError):
            pass

        # If plan specified target files, check for unexpected file modifications
        if planned_files:
            unexpected_files = [
                f for f in files_changed
                if not any(planned in f for planned in planned_files)
            ]
            if unexpected_files:
                warnings.append(
                    f"Plan contradicted: patch modifies files not in plan — "
                    f"{', '.join(unexpected_files[:3])}"
                )

        # Check if plan body mentions symbols/modules not touched by patch
        plan_lines = body.strip().splitlines()
        step_lines = [
            line.strip() for line in plan_lines
            if line.strip().startswith("Step ")
        ]
        if step_lines and not files_changed:
            warnings.append(
                "Plan contradicted: plan has steps but patch changes no files"
            )

        return warnings
