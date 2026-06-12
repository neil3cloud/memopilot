"""Wave 2 services for team policy packs and local flow builder."""

from __future__ import annotations

import fnmatch
import json
import uuid
from dataclasses import dataclass

from .config import Config
from .db import DatabaseManager
from .wavec_service import WaveCService


@dataclass(frozen=True)
class PolicyPackItem:
    pack_id: str
    name: str
    description: str
    enforcement_mode: str
    rules: list[str]
    active: bool
    version: int


@dataclass(frozen=True)
class PolicyEvaluationResult:
    allowed: bool
    decision: str
    stage: str
    active_pack_id: str | None
    active_pack_name: str | None
    violations: list[str]
    applied_policies: list[str]


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


class Wave2Service:
    """Implements Wave 2 capabilities for policy and local flow orchestration."""

    def __init__(self, *, config: Config, db: DatabaseManager) -> None:
        self._config = config
        self._db = db

    async def list_policy_packs(self, *, limit: int = 100) -> list[PolicyPackItem]:
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT p.id, p.name, p.description, p.enforcement_mode, p.rules_json, p.active,
                   COALESCE(v.version, 0) AS version
            FROM policy_packs p
            LEFT JOIN (
                SELECT pack_id, MAX(version) AS version
                FROM policy_pack_versions
                GROUP BY pack_id
            ) v ON v.pack_id = p.id
            ORDER BY p.updated_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            PolicyPackItem(
                pack_id=row["id"],
                name=row["name"],
                description=row["description"] or "",
                enforcement_mode=row["enforcement_mode"],
                rules=self._load_rules(row["rules_json"]),
                active=bool(row["active"]),
                version=int(row["version"] or 0),
            )
            for row in rows
        ]

    async def save_policy_pack(
        self,
        *,
        name: str,
        description: str,
        enforcement_mode: str,
        rules: list[str],
    ) -> PolicyPackItem:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("policy pack name is required")
        clean_rules = [rule.strip() for rule in rules if rule.strip()]
        if not clean_rules:
            raise ValueError("policy pack requires at least one rule")
        if enforcement_mode not in {"enforce", "advisory"}:
            raise ValueError("enforcement_mode must be 'enforce' or 'advisory'")

        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT id, active
            FROM policy_packs
            WHERE lower(name) = lower(?)
            LIMIT 1
            """,
            (clean_name,),
        )
        existing = await cursor.fetchone()
        pack_id = existing["id"] if existing is not None else uuid.uuid4().hex
        active = bool(existing["active"]) if existing is not None else False
        content_json = json.dumps(
            {
                "name": clean_name,
                "description": description,
                "enforcement_mode": enforcement_mode,
                "rules": clean_rules,
            }
        )

        await conn.execute(
            """
            INSERT INTO policy_packs
            (id, name, description, enforcement_mode, rules_json, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                description = excluded.description,
                enforcement_mode = excluded.enforcement_mode,
                rules_json = excluded.rules_json,
                updated_at = datetime('now')
            """,
            (
                pack_id,
                clean_name,
                description.strip(),
                enforcement_mode,
                json.dumps(clean_rules),
                1 if active else 0,
            ),
        )

        version_cursor = await conn.execute(
            """
            SELECT COALESCE(MAX(version), 0) AS max_version
            FROM policy_pack_versions
            WHERE pack_id = ?
            """,
            (pack_id,),
        )
        version_row = await version_cursor.fetchone()
        next_version = int(version_row["max_version"] or 0) + 1
        await conn.execute(
            """
            INSERT INTO policy_pack_versions
            (id, pack_id, version, content_json, created_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            """,
            (uuid.uuid4().hex, pack_id, next_version, content_json),
        )
        await conn.commit()
        return PolicyPackItem(
            pack_id=pack_id,
            name=clean_name,
            description=description.strip(),
            enforcement_mode=enforcement_mode,
            rules=clean_rules,
            active=active,
            version=next_version,
        )

    async def activate_policy_pack(self, *, pack_id: str) -> None:
        conn = await self._db.connect()
        cursor = await conn.execute(
            "SELECT id FROM policy_packs WHERE id = ? LIMIT 1",
            (pack_id,),
        )
        existing = await cursor.fetchone()
        if existing is None:
            raise ValueError(f"Policy pack not found: {pack_id}")
        await conn.execute("UPDATE policy_packs SET active = 0")
        await conn.execute(
            "UPDATE policy_packs SET active = 1, updated_at = datetime('now') WHERE id = ?",
            (pack_id,),
        )
        await conn.commit()

    async def evaluate_policy(
        self,
        *,
        stage: str,
        task_text: str,
        files_changed: list[str],
        selected_model: str | None,
    ) -> PolicyEvaluationResult:
        active = await self._active_policy_pack()
        if active is None:
            return PolicyEvaluationResult(
                allowed=True,
                decision="allow",
                stage=stage,
                active_pack_id=None,
                active_pack_name=None,
                violations=[],
                applied_policies=[],
            )

        violations: list[str] = []
        applied = active.rules[:]
        selected = (selected_model or "").lower()
        changed = [path.lower() for path in files_changed]
        task_lower = task_text.lower()

        for rule in active.rules:
            lower = rule.lower()
            if lower.startswith("deny_model:") and stage == "model_call":
                denied_tokens = self._split_csv(lower.split(":", 1)[1])
                if selected and any(token in selected for token in denied_tokens):
                    violations.append(f"Denied model by policy: {rule}")
            if lower.startswith("forbid_path:") and stage == "patch_execution":
                patterns = self._split_csv(lower.split(":", 1)[1])
                if any(
                    any(fnmatch.fnmatch(path, pattern) for pattern in patterns)
                    for path in changed
                ):
                    violations.append(f"Forbidden file path by policy: {rule}")
            if lower.startswith("require_keyword:") and stage == "model_call":
                required = self._split_csv(lower.split(":", 1)[1])
                missing = [token for token in required if token not in task_lower]
                if missing:
                    violations.append(f"Missing required task keywords: {', '.join(missing)}")
            if (
                ("require_test_file" in lower or "must include tests" in lower)
                and stage == "patch_execution"
                and changed
                and not any("test" in path for path in changed)
            ):
                violations.append("Policy requires at least one test file change.")

        decision = "allow"
        allowed = True
        if violations:
            if active.enforcement_mode == "enforce":
                decision = "block"
                allowed = False
            else:
                decision = "warn"

        return PolicyEvaluationResult(
            allowed=allowed,
            decision=decision,
            stage=stage,
            active_pack_id=active.pack_id,
            active_pack_name=active.name,
            violations=violations,
            applied_policies=applied,
        )

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
                result = await self.evaluate_policy(
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
                optimizer = WaveCService(config=self._config, db=self._db)
                recommendation = await optimizer.optimize_tools_and_skills(
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

    async def _active_policy_pack(self) -> PolicyPackItem | None:
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT id, name, description, enforcement_mode, rules_json, active
            FROM policy_packs
            WHERE active = 1
            ORDER BY updated_at DESC
            LIMIT 1
            """
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return PolicyPackItem(
            pack_id=row["id"],
            name=row["name"],
            description=row["description"] or "",
            enforcement_mode=row["enforcement_mode"],
            rules=self._load_rules(row["rules_json"]),
            active=bool(row["active"]),
            version=0,
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

    def _load_rules(self, raw: str | None) -> list[str]:
        if not raw:
            return []
        value = json.loads(raw)
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()]

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

    def _split_csv(self, values: str) -> list[str]:
        return [item.strip() for item in values.split(",") if item.strip()]
