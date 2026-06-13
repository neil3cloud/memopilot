"""Local agent flow builder: define and execute multi-step flows (v2 capability)."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

import yaml

from .config import Config
from .db import DatabaseManager
from .policy_packs import PolicyPacksService
from .skill_loader import SkillLoaderService

REQUIRED_FLOW_FIELDS = {"flow_id", "name", "steps"}
VALID_ACTIONS = {"recall_memory", "run_validation", "generate_patch", "analyze_task"}
_SUPPORTED_ACTIONS = VALID_ACTIONS | {"policy_check", "tool_recommend", "approval_gate"}
_DEFAULT_TOOLS = [
    "Ask",
    "Plan",
    "Context Pack",
    "Patch",
    "Test",
    "Review",
    "Autofix",
    "Investigate",
]
_FRONTIER_MODEL = "gpt-4o"
_DESTRUCTIVE_PATTERNS = (
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r"\bdel\s+/(?:[sqf]|s\s+/q|q\s+/s)\b", re.IGNORECASE),
    re.compile(r"\bremove-item\b", re.IGNORECASE),
    re.compile(r"\bformat\s+[a-z]:", re.IGNORECASE),
    re.compile(r"\bdrop\s+table\b", re.IGNORECASE),
    re.compile(r"\bdelete\s+from\b", re.IGNORECASE),
    re.compile(r"\btruncate\s+table\b", re.IGNORECASE),
    re.compile(r"\bshutdown\s+/s\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class LocalFlowItem:
    flow_id: str
    name: str
    description: str
    enabled: bool
    steps: list[dict[str, Any]]


@dataclass(frozen=True)
class LocalFlowRunResult:
    run_id: str
    flow_id: str
    flow_name: str
    status: str
    steps: list[dict[str, Any]]
    blocked_reason: str | None


def validate_flow(flow_yaml: str) -> tuple[bool, list[str]]:
    """Validate a YAML flow definition and return (valid, errors)."""
    try:
        payload = yaml.safe_load(flow_yaml)
    except yaml.YAMLError as exc:
        return False, [f"Invalid YAML: {exc}"]

    if not isinstance(payload, dict):
        return False, ["flow YAML must define an object"]

    errors = _validate_flow_payload(payload)
    return len(errors) == 0, errors


def _validate_flow_payload(flow_definition: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    missing = REQUIRED_FLOW_FIELDS - set(flow_definition)
    if missing:
        errors.append(f"flow definition missing required fields: {', '.join(sorted(missing))}")
        return errors

    flow_id = str(flow_definition.get("flow_id") or "").strip()
    if not flow_id:
        errors.append("flow_id is required")

    name = str(flow_definition.get("name") or "").strip()
    if not name:
        errors.append("name is required")

    steps = flow_definition.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append("flow requires at least one step")
        return errors

    approval_seen = False
    for index, step in enumerate(steps):
        prefix = f"steps[{index}]"
        if not isinstance(step, dict):
            errors.append(f"{prefix} must be an object")
            continue

        step_name = str(step.get("name") or "").strip()
        if not step_name:
            errors.append(f"{prefix}.name is required")

        action = str(step.get("action") or "").strip().lower()
        if not action:
            errors.append(f"{prefix}.action is required")
        elif action not in VALID_ACTIONS:
            errors.append(
                f"{prefix}.action '{action}' is invalid; expected one of: "
                f"{', '.join(sorted(VALID_ACTIONS))}"
            )

        if _step_contains_destructive_command(step):
            errors.append(f"{prefix} includes a destructive command")

        requires_approval = _step_requires_approval(step)
        if action == "generate_patch" and not (approval_seen or requires_approval):
            errors.append(f"{prefix} cannot modify files without an approval step")
        if requires_approval:
            approval_seen = True

    return errors


def _normalize_validated_flow(flow_definition: dict[str, Any]) -> dict[str, Any]:
    normalized_steps: list[dict[str, Any]] = []
    for index, step in enumerate(flow_definition["steps"]):
        step_name = str(step.get("name") or "").strip()
        action = str(step.get("action") or "").strip().lower()
        normalized_step: dict[str, Any] = {
            "id": str(step.get("id") or f"step-{index + 1}"),
            "name": step_name,
            "title": step_name,
            "action": action,
        }
        _apply_optional_step_fields(normalized_step, step)
        normalized_steps.append(normalized_step)

    return {
        "flow_id": str(flow_definition["flow_id"]).strip(),
        "name": str(flow_definition["name"]).strip(),
        "description": str(flow_definition.get("description") or "").strip(),
        "steps": normalized_steps,
    }


def _step_requires_approval(step: dict[str, Any]) -> bool:
    return _as_bool(step.get("requires_approval")) or _as_bool(step.get("approval_required"))


def _apply_optional_step_fields(normalized_step: dict[str, Any], raw_step: dict[str, Any]) -> None:
    for key in ("stage", "escalate_to_model", "command"):
        value = raw_step.get(key)
        if isinstance(value, str) and value.strip():
            normalized_step[key] = value.strip().lower() if key == "stage" else value.strip()

    for key in ("requires_approval", "approval_required", "requires_mcp", "simulate_failure"):
        if key in raw_step:
            normalized_step[key] = _as_bool(raw_step.get(key))

    if _step_requires_approval(raw_step):
        normalized_step["requires_approval"] = True
        normalized_step["approval_required"] = True

    if (
        "escalate_after_failures" in raw_step
        and str(raw_step.get("escalate_after_failures") or "").strip()
    ):
        normalized_step["escalate_after_failures"] = int(
            raw_step.get("escalate_after_failures") or 0
        )

    available_tools = raw_step.get("available_tools")
    if isinstance(available_tools, list):
        normalized_step["available_tools"] = [
            str(tool) for tool in available_tools if str(tool).strip()
        ]


def _step_contains_destructive_command(step: dict[str, Any]) -> bool:
    for key in ("command", "shell_command", "script", "tool_input"):
        value = step.get(key)
        if isinstance(value, str) and _contains_destructive_text(value):
            return True
    return False


def _contains_destructive_text(value: str) -> bool:
    return any(pattern.search(value) for pattern in _DESTRUCTIVE_PATTERNS)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


class FlowBuilderService:
    """Manages local agent flows and their execution."""

    def __init__(
        self,
        *,
        config: Config,
        db: DatabaseManager,
        policy_service: PolicyPacksService | None = None,
        skill_service: SkillLoaderService | None = None,
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

    def validate_flow_definition(self, flow_definition: dict[str, Any]) -> dict[str, Any]:
        errors = _validate_flow_payload(flow_definition)
        if errors:
            raise ValueError("; ".join(errors))
        return _normalize_validated_flow(flow_definition)

    def validate_flow_yaml(self, flow_yaml: str) -> dict[str, Any]:
        valid, errors = validate_flow(flow_yaml)
        if not valid:
            raise ValueError("; ".join(errors))
        payload = yaml.safe_load(flow_yaml)
        if not isinstance(payload, dict):
            raise ValueError("flow YAML must define an object")
        return _normalize_validated_flow(payload)

    async def save_flow(
        self,
        *,
        name: str,
        description: str,
        steps: list[dict[str, Any]],
        flow_id: str | None = None,
        flow_yaml: str | None = None,
    ) -> LocalFlowItem:
        if flow_yaml:
            validated = self.validate_flow_yaml(flow_yaml)
            flow_id = validated["flow_id"]
            clean_name = validated["name"]
            description = validated["description"]
            normalized_steps = validated["steps"]
        else:
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
        resolved_flow_id = flow_id or (existing["id"] if existing is not None else uuid.uuid4().hex)
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
                resolved_flow_id,
                clean_name,
                description.strip(),
                json.dumps(normalized_steps),
                1 if enabled else 0,
            ),
        )
        await conn.commit()
        return LocalFlowItem(
            flow_id=resolved_flow_id,
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
        constraints: list[str] | None = None,
        approved_steps: list[str] | None = None,
        planned_mcp_calls: int = 0,
        mcp_cap: int | None = None,
        failure_count: int = 0,
        allow_file_modifications: bool = False,
        workspace_root: str | None = None,
    ) -> LocalFlowRunResult:
        flow = await self._get_flow(flow_id)
        blocked_reason: str | None = None
        steps_result: list[dict[str, Any]] = []
        status = "completed"
        constraints_set = {item.strip().lower() for item in (constraints or []) if item.strip()}
        approvals = set(approved_steps or [])
        current_model = selected_model
        effective_mcp_cap = self._resolve_mcp_cap(mcp_cap)

        for index, step in enumerate(flow.steps):
            action = str(step.get("action") or "").lower()
            label = str(step.get("title") or step.get("name") or action or "step")
            step_id = str(step.get("id") or label)
            step_result: dict[str, Any] = {
                "title": label,
                "name": str(step.get("name") or label),
                "action": action,
                "status": "ok",
            }

            if _step_contains_destructive_command(step):
                step_result["status"] = "blocked"
                step_result["violations"] = ["Destructive commands are blocked"]
                steps_result.append(step_result)
                blocked_reason = step_result["violations"][0]
                status = "blocked"
                break

            escalate_after = int(step.get("escalate_after_failures") or 0)
            if escalate_after > 0 and failure_count >= escalate_after:
                current_model = str(step.get("escalate_to_model") or _FRONTIER_MODEL)
                step_result["model_escalated_to"] = current_model

            if (
                effective_mcp_cap is not None
                and _as_bool(step.get("requires_mcp"))
                and planned_mcp_calls > effective_mcp_cap
            ):
                step_result["status"] = "blocked"
                step_result["violations"] = [
                    f"MCP cap exceeded ({planned_mcp_calls}>{effective_mcp_cap})"
                ]
                blocked_reason = step_result["violations"][0]
                steps_result.append(step_result)
                status = "blocked"
                break

            requires_approval = _step_requires_approval(step)
            approved = (
                allow_file_modifications
                or step_id in approvals
                or label in approvals
                or str(step.get("name") or "") in approvals
            )
            if requires_approval:
                step_result["approved"] = approved
                if not approved:
                    step_result["status"] = "blocked"
                    blocked_reason = f"Approval required for step '{label}'"
                    steps_result.append(step_result)
                    status = "blocked"
                    break

            if action == "policy_check":
                stage = str(step.get("stage") or "model_call")
                result = await self._policy_service.evaluate_policy(
                    stage=stage,
                    task_text=task_text,
                    files_changed=files_changed,
                    selected_model=current_model,
                    workspace_root=workspace_root,
                )
                step_result["status"] = "ok" if result.allowed else "blocked"
                step_result["decision"] = result.decision
                step_result["violations"] = result.violations
                steps_result.append(step_result)
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
                    if isinstance(item, str) and item.strip()
                ]
                recommendation = await self._skill_service.optimize_tools_and_skills(
                    task_text=task_text,
                    available_tools=available_tools or _DEFAULT_TOOLS,
                )
                step_result["suggested_tools"] = recommendation.suggested_tools
                step_result["suggested_skills"] = recommendation.suggested_skills
                steps_result.append(step_result)
                continue

            if action == "approval_gate":
                step_result["approved"] = approved
                step_result["status"] = "ok" if approved else "blocked"
                steps_result.append(step_result)
                if not approved:
                    blocked_reason = f"Approval required for step '{label}'"
                    status = "blocked"
                    break
                continue

            if action == "analyze_task":
                step_result["summary"] = task_text[:200]
                step_result["files_changed"] = files_changed
                steps_result.append(step_result)
                continue

            if action == "recall_memory":
                step_result["query"] = task_text[:120]
                step_result["workspace_root"] = workspace_root
                steps_result.append(step_result)
                continue

            if action == "run_validation":
                if _as_bool(step.get("simulate_failure")):
                    step_result["status"] = "failed"
                    step_result["message"] = "Validation failed"
                    status = "failed"
                else:
                    step_result["message"] = "Validation completed"
                steps_result.append(step_result)
                if step_result["status"] == "failed":
                    break
                continue

            if action == "generate_patch":
                if not self._has_modification_approval(flow.steps, index):
                    step_result["status"] = "blocked"
                    blocked_reason = "File modification requires approval"
                    step_result["approved"] = False
                    steps_result.append(step_result)
                    status = "blocked"
                    break
                if "no_file_modification_without_approval" in constraints_set and not approved:
                    step_result["status"] = "blocked"
                    step_result["approved"] = False
                    blocked_reason = "File modification requires approval"
                    steps_result.append(step_result)
                    status = "blocked"
                    break
                step_result["approved"] = True
                step_result["planned_files"] = files_changed
                step_result["model_used"] = current_model
                steps_result.append(step_result)
                continue

            step_result["status"] = "skipped"
            steps_result.append(step_result)

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
                        "constraints": sorted(constraints_set),
                        "planned_mcp_calls": planned_mcp_calls,
                        "mcp_cap": effective_mcp_cap,
                        "workspace_root": workspace_root,
                    }
                ),
                json.dumps({"steps": steps_result, "blocked_reason": blocked_reason}),
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

    def _load_steps(self, raw: str | None) -> list[dict[str, Any]]:
        if not raw:
            return []
        value = json.loads(raw)
        if not isinstance(value, list):
            return []
        return self._normalize_steps(value)

    def _normalize_steps(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            action = str(step.get("action") or "").strip().lower()
            if not action:
                continue
            if action not in _SUPPORTED_ACTIONS:
                raise ValueError(f"Unsupported flow action: {action}")
            if _step_contains_destructive_command(step):
                raise ValueError("Destructive commands are blocked")
            step_name = str(
                step.get("name") or step.get("title") or action.replace("_", " ").title()
            ).strip()
            normalized_step: dict[str, Any] = {
                "id": str(step.get("id") or f"step-{index + 1}"),
                "name": step_name,
                "title": str(step.get("title") or step_name),
                "action": action,
            }
            _apply_optional_step_fields(normalized_step, step)
            normalized.append(normalized_step)
        return normalized

    def _resolve_mcp_cap(self, requested_cap: int | None) -> int:
        hard_cap = max(int(self._config.mcp_hard_absolute_cap), 0)
        if requested_cap is None:
            return hard_cap
        return min(max(int(requested_cap), 0), hard_cap)

    def _has_modification_approval(self, steps: list[dict[str, Any]], step_index: int) -> bool:
        for step in steps[: step_index + 1]:
            if str(step.get("action") or "").lower() == "approval_gate":
                return True
            if _step_requires_approval(step):
                return True
        return False
