"""Local agent flow builder: define and execute multi-step flows (v2 capability)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from .config import Config
from .db import DatabaseManager
from .policy_packs import PolicyPacksService
from .skill_loader import SkillLoaderService


@dataclass(frozen=True)
class LocalFlowItem:
    flow_id: str
    name: str
    description: str
    enabled: bool
    steps: list[dict[str, str | list[str] | bool]]


@dataclass(frozen=True)
class LocalFlowRunResult:
    run_id: str
    flow_id: str
    flow_name: str
    status: str
    steps: list[dict[str, str | bool | list[str]]]
    blocked_reason: str | None


class FlowBuilderService:
    """Manages local agent flows and their execution."""

    def __init__(
        self,
        *,
        config: Config,
        db: DatabaseManager,
        policy_service: PolicyPacksService | None = None,
        skill_service: "SkillLoaderService | None" = None,
    ) -> None:
        self._config = config
        self._db = db
        self._policy_service = policy_service or PolicyPacksService(config=config, db=db)
        self._skill_service = skill_service or SkillLoaderService(config=config, db=db)

    async def list_flows(self, *, limit: int = 100) -> list[LocalFlowItem]:
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT id, name, description, enabled, steps_json
            FROM local_flows
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            LocalFlowItem(
                flow_id=row["id"],
                name=row["name"],
                description=row["description"] or "",
                enabled=bool(row["enabled"]),
                steps=self._load_steps(row["steps_json"]),
            )
            for row in rows
        ]

    async def save_flow(
        self,
        *,
        name: str,
        description: str,
        steps: list[dict[str, str | list[str] | bool]],
    ) -> LocalFlowItem:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("flow name is required")
        normalized_steps = self._normalize_steps(steps)
        if not normalized_steps:
            raise ValueError("flow requires at least one step")

        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT id, enabled
            FROM local_flows
            WHERE lower(name) = lower(?)
            LIMIT 1
            """,
            (clean_name,),
        )
        existing = await cursor.fetchone()
        flow_id = existing["id"] if existing is not None else uuid.uuid4().hex
        enabled = bool(existing["enabled"]) if existing is not None else True
        await conn.execute(
            """
            INSERT INTO local_flows
            (id, name, description, steps_json, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                description = excluded.description,
                steps_json = excluded.steps_json,
                enabled = excluded.enabled,
                updated_at = datetime('now')
            """,
            (
                flow_id,
                clean_name,
                description.strip(),
                json.dumps(normalized_steps),
                1 if enabled else 0,
            ),
        )
        await conn.commit()
        return LocalFlowItem(
            flow_id=flow_id,
            name=clean_name,
            description=description.strip(),
            enabled=enabled,
            steps=normalized_steps,
        )

    async def run_flow(
        self,
        *,
        flow_id: str,
        task_text: str,
        files_changed: list[str],
        selected_model: str | None,
    ) -> LocalFlowRunResult:
        flow = await self._get_flow(flow_id)
        blocked_reason: str | None = None
        steps_result: list[dict[str, str | bool | list[str]]] = []
        status = "completed"

        for step in flow.steps:
            action = str(step.get("action") or "").lower()
            label = str(step.get("title") or action or "step")
            if action == "policy_check":
                stage = str(step.get("stage") or "model_call")
                result = await self._policy_service.evaluate_policy(
                    stage=stage,
                    task_text=task_text,
                    files_changed=files_changed,
                    selected_model=selected_model,
                )
                steps_result.append(
                    {
                        "title": label,
                        "action": action,
                        "status": "ok" if result.allowed else "blocked",
                        "decision": result.decision,
                        "violations": result.violations,
                    }
                )
                if not result.allowed:
                    blocked_reason = (
                        f"Policy blocked at stage '{stage}': {result.violations[0]}"
                        if result.violations
                        else f"Policy blocked at stage '{stage}'."
                    )
                    status = "blocked"
                    break
                continue

            if action == "tool_recommend":
                available_tools = [
                    str(item)
                    for item in step.get("available_tools", [])
                    if isinstance(item, str) and item
                ]
                if not available_tools:
                    available_tools = [
                        "Ask",
                        "Plan",
                        "Context Pack",
                        "Patch",
                        "Test",
                        "Review",
                        "Autofix",
                        "Investigate",
                    ]
                recommendation = await self._skill_service.optimize_tools_and_skills(
                    task_text=task_text,
                    available_tools=available_tools,
                )
                steps_result.append(
                    {
                        "title": label,
                        "action": action,
                        "status": "ok",
                        "suggested_tools": recommendation.suggested_tools,
                        "suggested_skills": recommendation.suggested_skills,
                    }
                )
                continue

            if action == "approval_gate":
                approved = blocked_reason is None
                steps_result.append(
                    {
                        "title": label,
                        "action": action,
                        "status": "ok" if approved else "blocked",
                        "approved": approved,
                    }
                )
                if not approved:
                    status = "blocked"
                    break
                continue

            steps_result.append(
                {
                    "title": label,
                    "action": action or "unknown",
                    "status": "skipped",
                }
            )

        run_id = uuid.uuid4().hex
        conn = await self._db.connect()
        await conn.execute(
            """
            INSERT INTO local_flow_runs
            (id, flow_id, task_text, input_json, result_json, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                run_id,
                flow.flow_id,
                task_text,
                json.dumps(
                    {
                        "files_changed": files_changed,
                        "selected_model": selected_model,
                    }
                ),
                json.dumps(
                    {
                        "steps": steps_result,
                        "blocked_reason": blocked_reason,
                    }
                ),
                status,
            ),
        )
        await conn.commit()
        return LocalFlowRunResult(
            run_id=run_id,
            flow_id=flow.flow_id,
            flow_name=flow.name,
            status=status,
            steps=steps_result,
            blocked_reason=blocked_reason,
        )

    async def _get_flow(self, flow_id: str) -> LocalFlowItem:
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT id, name, description, enabled, steps_json
            FROM local_flows
            WHERE id = ?
            LIMIT 1
            """,
            (flow_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError(f"Local flow not found: {flow_id}")
        return LocalFlowItem(
            flow_id=row["id"],
            name=row["name"],
            description=row["description"] or "",
            enabled=bool(row["enabled"]),
            steps=self._load_steps(row["steps_json"]),
        )

    def _load_steps(self, raw: str | None) -> list[dict[str, str | list[str] | bool]]:
        if not raw:
            return []
        value = json.loads(raw)
        if not isinstance(value, list):
            return []
        return self._normalize_steps(value)

    def _normalize_steps(
        self,
        steps: list[dict[str, str | list[str] | bool]],
    ) -> list[dict[str, str | list[str] | bool]]:
        normalized: list[dict[str, str | list[str] | bool]] = []
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            action = str(step.get("action") or "").strip().lower()
            if not action:
                continue
            normalized_step: dict[str, str | list[str] | bool] = {
                "id": str(step.get("id") or f"step-{index + 1}"),
                "title": str(step.get("title") or action.replace("_", " ").title()),
                "action": action,
            }
            stage = step.get("stage")
            if isinstance(stage, str) and stage.strip():
                normalized_step["stage"] = stage.strip().lower()
            available_tools = step.get("available_tools")
            if isinstance(available_tools, list):
                normalized_step["available_tools"] = [
                    str(tool) for tool in available_tools if str(tool).strip()
                ]
            normalized.append(normalized_step)
        return normalized
