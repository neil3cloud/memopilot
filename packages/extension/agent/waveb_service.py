"""Wave B services for templates, scoring, capabilities, and replay."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .db import DatabaseManager


@dataclass(frozen=True)
class ContextTemplateRecord:
    template_id: str
    name: str
    scope: str
    path: str
    selected: bool


@dataclass(frozen=True)
class ContextPackVersionRecord:
    version_id: str
    task_run_id: str | None
    pack_path: str
    pack_hash: str
    token_estimate: int | None
    selected_model: str | None
    template_id: str | None
    created_at: str


@dataclass(frozen=True)
class PatchAssessmentResult:
    patch_attempt_id: str
    risk_level: str
    rule_compliance_score: float
    reasons: list[str]


@dataclass(frozen=True)
class ProviderCapabilityRecord:
    model_id: str
    source: str
    max_context_tokens: int | None
    supports_tool_calling: bool
    supports_json_mode: bool
    estimated_cost_per_1m_input: float
    estimated_cost_per_1m_output: float
    privacy_level: str
    allowed_task_types: list[str]
    denied_task_types: list[str]
    requires_approval: bool


@dataclass(frozen=True)
class ReplayCallResult:
    ai_call_id: str
    task_run_id: str
    provider: str
    model: str
    purpose: str | None
    context_pack_path: str | None
    context_pack_text: str
    replay_payload: dict[str, str | int | float | bool | None]


@dataclass(frozen=True)
class ContextPackDiffResult:
    left_version_id: str
    right_version_id: str
    diff_text: str


class WaveBService:
    """Implements Phase 17 wave-B capabilities."""

    def __init__(self, *, config: Config, db: DatabaseManager) -> None:
        self._config = config
        self._db = db
        self._template_state_path = (
            self._config.memopilot_dir / "context-templates" / "active-template.json"
        )

    async def list_templates(self) -> list[ContextTemplateRecord]:
        active = self._active_template_id()
        records: list[ContextTemplateRecord] = []

        for scope, base_dir in (
            ("workspace", self._config.memopilot_dir / "context-templates"),
            ("global", self._config.global_dir / "context-templates"),
        ):
            if not base_dir.exists():
                continue
            for file_path in sorted(base_dir.glob("*.md")):
                template_id = f"{scope}:{file_path.stem}"
                records.append(
                    ContextTemplateRecord(
                        template_id=template_id,
                        name=file_path.stem,
                        scope=scope,
                        path=str(file_path),
                        selected=template_id == active,
                    )
                )

        if records:
            return records

        default_id = await self.save_template(
            name="default-investigation",
            content=(
                "# Investigation Template\n\n"
                "## Summary\n- Describe the problem clearly.\n\n"
                "## Constraints\n- Keep changes minimal.\n\n"
                "## Output\n- Root cause\n- Plan\n- Tests\n"
            ),
            scope="workspace",
        )
        await self.select_template(default_id)
        return await self.list_templates()

    async def save_template(self, *, name: str, content: str, scope: str) -> str:
        if scope not in {"workspace", "global"}:
            raise ValueError("scope must be 'workspace' or 'global'")
        clean_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip().lower()).strip("-")
        if not clean_name:
            raise ValueError("template name is required")
        base_dir = (
            self._config.memopilot_dir / "context-templates"
            if scope == "workspace"
            else self._config.global_dir / "context-templates"
        )
        base_dir.mkdir(parents=True, exist_ok=True)
        template_path = base_dir / f"{clean_name}.md"
        template_path.write_text(content, encoding="utf-8")
        return f"{scope}:{clean_name}"

    async def select_template(self, template_id: str) -> None:
        available = {item.template_id for item in await self.list_templates()}
        if template_id not in available:
            raise ValueError(f"Template not found: {template_id}")
        self._template_state_path.parent.mkdir(parents=True, exist_ok=True)
        self._template_state_path.write_text(
            json.dumps({"template_id": template_id}),
            encoding="utf-8",
        )

    def _active_template_id(self) -> str | None:
        if not self._template_state_path.exists():
            return None
        payload = json.loads(self._template_state_path.read_text(encoding="utf-8"))
        template_id = payload.get("template_id")
        if isinstance(template_id, str) and template_id:
            return template_id
        return None

    async def store_context_pack_version(
        self,
        *,
        task_run_id: str | None,
        context_pack_text: str,
        pack_path: str | None,
        token_estimate: int | None,
        selected_model: str | None,
        template_id: str | None,
    ) -> ContextPackVersionRecord:
        version_id = uuid.uuid4().hex
        pack_hash = hashlib.sha256(context_pack_text.encode("utf-8")).hexdigest()

        resolved_path = Path(pack_path) if pack_path else (
            self._config.memopilot_dir / "context-packs" / f"version-{version_id}.md"
        )
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_path.write_text(context_pack_text, encoding="utf-8")

        conn = await self._db.connect()
        await conn.execute(
            """
            INSERT INTO context_pack_versions
            (id, task_run_id, pack_path, pack_hash, token_estimate, selected_model, template_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                task_run_id,
                str(resolved_path),
                pack_hash,
                token_estimate,
                selected_model,
                template_id,
            ),
        )
        await conn.commit()
        return await self.get_context_pack_version(version_id)

    async def get_context_pack_version(self, version_id: str) -> ContextPackVersionRecord:
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT
                id, task_run_id, pack_path, pack_hash,
                token_estimate, selected_model, template_id, created_at
            FROM context_pack_versions
            WHERE id = ?
            """,
            (version_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError(f"Context pack version not found: {version_id}")
        return ContextPackVersionRecord(
            version_id=row["id"],
            task_run_id=row["task_run_id"],
            pack_path=row["pack_path"],
            pack_hash=row["pack_hash"],
            token_estimate=row["token_estimate"],
            selected_model=row["selected_model"],
            template_id=row["template_id"],
            created_at=row["created_at"],
        )

    async def list_context_pack_versions(
        self,
        *,
        task_run_id: str | None,
        limit: int,
    ) -> list[ContextPackVersionRecord]:
        conn = await self._db.connect()
        if task_run_id:
            cursor = await conn.execute(
                """
                SELECT
                    id, task_run_id, pack_path, pack_hash,
                    token_estimate, selected_model, template_id, created_at
                FROM context_pack_versions
                WHERE task_run_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (task_run_id, limit),
            )
        else:
            cursor = await conn.execute(
                """
                SELECT
                    id, task_run_id, pack_path, pack_hash,
                    token_estimate, selected_model, template_id, created_at
                FROM context_pack_versions
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        rows = await cursor.fetchall()
        return [
            ContextPackVersionRecord(
                version_id=row["id"],
                task_run_id=row["task_run_id"],
                pack_path=row["pack_path"],
                pack_hash=row["pack_hash"],
                token_estimate=row["token_estimate"],
                selected_model=row["selected_model"],
                template_id=row["template_id"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def diff_context_pack_versions(
        self,
        *,
        left_version_id: str,
        right_version_id: str,
    ) -> ContextPackDiffResult:
        left = await self.get_context_pack_version(left_version_id)
        right = await self.get_context_pack_version(right_version_id)
        left_text = Path(left.pack_path).read_text(encoding="utf-8", errors="replace")
        right_text = Path(right.pack_path).read_text(encoding="utf-8", errors="replace")
        diff = "\n".join(
            difflib.unified_diff(
                left_text.splitlines(),
                right_text.splitlines(),
                fromfile=left.pack_path,
                tofile=right.pack_path,
                lineterm="",
            )
        )
        return ContextPackDiffResult(
            left_version_id=left_version_id,
            right_version_id=right_version_id,
            diff_text=diff,
        )

    async def assess_patch(
        self,
        *,
        task_run_id: str,
        diff_text: str,
        files_changed: list[str],
        active_rules: list[str],
    ) -> PatchAssessmentResult:
        reasons: list[str] = []
        risk_level = "low"
        lowered_diff = diff_text.lower()
        lowered_files = [item.lower() for item in files_changed]

        if "drop table" in lowered_diff or "delete from" in lowered_diff:
            risk_level = "high"
            reasons.append("destructive_sql_detected")
        if any(item.endswith(".env") for item in lowered_files):
            risk_level = "high"
            reasons.append("sensitive_file_touched")
        if risk_level != "high" and (len(files_changed) > 10 or "migration" in lowered_diff):
            risk_level = "medium"
            reasons.append("wide_or_schema_change")

        score = 1.0
        if risk_level == "high":
            score -= 0.45
        elif risk_level == "medium":
            score -= 0.2

        for rule in active_rules:
            low_rule = rule.lower()
            if (
                "must include tests" in low_rule
                and not any("test" in item for item in lowered_files)
            ):
                score -= 0.25
                reasons.append("missing_test_file_for_rule")
            if "no hardcoded secrets" in low_rule and "api_key" in lowered_diff:
                score -= 0.35
                reasons.append("possible_secret_violation")

        score = max(0.0, min(1.0, score))
        patch_attempt_id = uuid.uuid4().hex
        conn = await self._db.connect()
        await conn.execute(
            """
            INSERT INTO patch_attempts
            (
                id, task_run_id, patch_path, files_changed_json,
                risk_level, rule_compliance_score, approved, applied, validation_status
            )
            VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?)
            """,
            (
                patch_attempt_id,
                task_run_id,
                "inline-diff",
                json.dumps(files_changed),
                risk_level,
                score,
                "pending",
            ),
        )
        await conn.commit()
        return PatchAssessmentResult(
            patch_attempt_id=patch_attempt_id,
            risk_level=risk_level,
            rule_compliance_score=score,
            reasons=sorted(set(reasons)),
        )

    async def list_provider_capabilities(
        self,
        *,
        limit: int = 100,
    ) -> list[ProviderCapabilityRecord]:
        conn = await self._db.connect()
        cursor = await conn.execute("SELECT COUNT(*) AS total FROM provider_capabilities")
        row = await cursor.fetchone()
        if int(row["total"] or 0) == 0:
            await self._seed_provider_capabilities()

        cursor = await conn.execute(
            """
            SELECT
                model_id, source, max_context_tokens, supports_tool_calling, supports_json_mode,
                estimated_cost_per_1m_input, estimated_cost_per_1m_output, privacy_level,
                allowed_task_types_json, denied_task_types_json, requires_approval
            FROM provider_capabilities
            ORDER BY source, model_id
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_capability(row) for row in rows]

    async def upsert_provider_capability(self, capability: ProviderCapabilityRecord) -> None:
        conn = await self._db.connect()
        await conn.execute(
            """
            INSERT INTO provider_capabilities
            (
                model_id, source, max_context_tokens, supports_tool_calling, supports_json_mode,
                estimated_cost_per_1m_input, estimated_cost_per_1m_output, privacy_level,
                allowed_task_types_json, denied_task_types_json, requires_approval, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(model_id) DO UPDATE SET
                source = excluded.source,
                max_context_tokens = excluded.max_context_tokens,
                supports_tool_calling = excluded.supports_tool_calling,
                supports_json_mode = excluded.supports_json_mode,
                estimated_cost_per_1m_input = excluded.estimated_cost_per_1m_input,
                estimated_cost_per_1m_output = excluded.estimated_cost_per_1m_output,
                privacy_level = excluded.privacy_level,
                allowed_task_types_json = excluded.allowed_task_types_json,
                denied_task_types_json = excluded.denied_task_types_json,
                requires_approval = excluded.requires_approval,
                updated_at = datetime('now')
            """,
            (
                capability.model_id,
                capability.source,
                capability.max_context_tokens,
                1 if capability.supports_tool_calling else 0,
                1 if capability.supports_json_mode else 0,
                capability.estimated_cost_per_1m_input,
                capability.estimated_cost_per_1m_output,
                capability.privacy_level,
                json.dumps(capability.allowed_task_types),
                json.dumps(capability.denied_task_types),
                1 if capability.requires_approval else 0,
            ),
        )
        await conn.commit()

    async def replay_ai_call(self, ai_call_id: str) -> ReplayCallResult:
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT id, task_run_id, provider, model, purpose
            FROM ai_calls
            WHERE id = ?
            """,
            (ai_call_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError(f"AI call not found: {ai_call_id}")

        task_run_id = str(row["task_run_id"])
        versions = await self.list_context_pack_versions(task_run_id=task_run_id, limit=1)
        context_pack_path = versions[0].pack_path if versions else None
        context_pack_text = (
            Path(context_pack_path).read_text(encoding="utf-8", errors="replace")
            if context_pack_path
            else ""
        )
        replay_payload: dict[str, str | int | float | bool | None] = {
            "task_run_id": task_run_id,
            "provider": row["provider"],
            "model": row["model"],
            "purpose": row["purpose"],
            "context_pack_path": context_pack_path,
        }
        return ReplayCallResult(
            ai_call_id=str(row["id"]),
            task_run_id=task_run_id,
            provider=str(row["provider"]),
            model=str(row["model"]),
            purpose=row["purpose"],
            context_pack_path=context_pack_path,
            context_pack_text=context_pack_text,
            replay_payload=replay_payload,
        )

    async def _seed_provider_capabilities(self) -> None:
        defaults = [
            ProviderCapabilityRecord(
                model_id="gpt-4o-mini",
                source="openai",
                max_context_tokens=128000,
                supports_tool_calling=True,
                supports_json_mode=True,
                estimated_cost_per_1m_input=0.15,
                estimated_cost_per_1m_output=0.6,
                privacy_level="cloud",
                allowed_task_types=["ask", "plan", "review"],
                denied_task_types=[],
                requires_approval=False,
            ),
            ProviderCapabilityRecord(
                model_id="claude-sonnet-4.5",
                source="anthropic",
                max_context_tokens=200000,
                supports_tool_calling=True,
                supports_json_mode=True,
                estimated_cost_per_1m_input=3.0,
                estimated_cost_per_1m_output=15.0,
                privacy_level="cloud",
                allowed_task_types=["patch", "autofix", "investigate"],
                denied_task_types=[],
                requires_approval=True,
            ),
            ProviderCapabilityRecord(
                model_id="llama3.1",
                source="ollama",
                max_context_tokens=32000,
                supports_tool_calling=False,
                supports_json_mode=False,
                estimated_cost_per_1m_input=0.0,
                estimated_cost_per_1m_output=0.0,
                privacy_level="local",
                allowed_task_types=["ask", "plan"],
                denied_task_types=["autofix"],
                requires_approval=False,
            ),
        ]
        for item in defaults:
            await self.upsert_provider_capability(item)

    def _row_to_capability(self, row) -> ProviderCapabilityRecord:
        def parse_list(raw: str | None) -> list[str]:
            if not raw:
                return []
            value = json.loads(raw)
            if isinstance(value, list):
                return [str(item) for item in value]
            return []

        return ProviderCapabilityRecord(
            model_id=row["model_id"],
            source=row["source"],
            max_context_tokens=row["max_context_tokens"],
            supports_tool_calling=bool(row["supports_tool_calling"]),
            supports_json_mode=bool(row["supports_json_mode"]),
            estimated_cost_per_1m_input=float(row["estimated_cost_per_1m_input"] or 0),
            estimated_cost_per_1m_output=float(row["estimated_cost_per_1m_output"] or 0),
            privacy_level=row["privacy_level"],
            allowed_task_types=parse_list(row["allowed_task_types_json"]),
            denied_task_types=parse_list(row["denied_task_types_json"]),
            requires_approval=bool(row["requires_approval"]),
        )
