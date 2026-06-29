"""Provider capability registry, seeding, and AI call replay (v1 capability)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .db import DatabaseManager


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


class ProviderRegistryService:
    """Manages provider capabilities and supports AI call replay."""

    def __init__(self, *, config: Config, db: DatabaseManager) -> None:
        self._config = config
        self._db = db

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
        # Find associated context pack version
        versions_cursor = await conn.execute(
            """
            SELECT pack_path
            FROM context_pack_versions
            WHERE task_run_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (task_run_id,),
        )
        version_row = await versions_cursor.fetchone()
        context_pack_path = version_row["pack_path"] if version_row else None
        context_pack_text = ""
        if context_pack_path:
            pack_path = Path(context_pack_path)
            if not pack_path.exists():
                raise ValueError(f"Context pack not available: {context_pack_path}")
            context_pack_text = pack_path.read_text(encoding="utf-8", errors="replace")
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
        # Seeding removed: only real configured providers (via POST endpoint) are stored
        pass

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
