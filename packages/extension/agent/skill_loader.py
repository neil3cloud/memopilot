"""Skill store, versioning, conflict detection, and tool/skill optimizer (v1.5 capability)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from .config import Config
from .db import DatabaseManager
from .evidence_classifier import EvidenceSourceClassifier


@dataclass(frozen=True)
class SkillStoreItem:
    skill_id: str
    name: str
    applies_when: str
    enabled: bool
    version: int
    conflict: bool


@dataclass(frozen=True)
class OptimizerResult:
    suggested_tools: list[str]
    suggested_skills: list[str]
    reasons: list[str]


class SkillLoaderService:
    """Manages the skill store, versioning, and task-aware tool/skill optimization."""

    def __init__(self, *, config: Config, db: DatabaseManager) -> None:
        self._config = config
        self._db = db
        self._classifier = EvidenceSourceClassifier()

    async def create_or_update_skill(
        self,
        *,
        name: str,
        applies_when: str,
        rules: list[str],
        tools: list[str],
    ) -> SkillStoreItem:
        name = name.strip()
        if not name:
            raise ValueError("skill name is required")
        applies_when = applies_when.strip()
        if not applies_when:
            raise ValueError("applies_when is required")

        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT id
            FROM skills
            WHERE lower(name) = lower(?)
            LIMIT 1
            """,
            (name,),
        )
        row = await cursor.fetchone()
        skill_id = row["id"] if row is not None else uuid.uuid4().hex

        await conn.execute(
            """
            INSERT INTO skills
            (id, name, applies_when, rules_json, tools_json, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, datetime('now'), datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                applies_when = excluded.applies_when,
                rules_json = excluded.rules_json,
                tools_json = excluded.tools_json,
                updated_at = datetime('now')
            """,
            (
                skill_id,
                name,
                applies_when,
                json.dumps(rules),
                json.dumps(tools),
            ),
        )

        version_cursor = await conn.execute(
            """
            SELECT COALESCE(MAX(version), 0) AS max_version
            FROM skill_store_versions
            WHERE skill_id = ?
            """,
            (skill_id,),
        )
        version_row = await version_cursor.fetchone()
        next_version = int(version_row["max_version"] or 0) + 1
        payload_hash = self._hash_payload(
            {
                "name": name,
                "applies_when": applies_when,
                "rules": rules,
                "tools": tools,
            }
        )
        conflict = await self._detect_skill_conflict(name=name, rules=rules, tools=tools)
        await conn.execute(
            """
            INSERT INTO skill_store_versions
            (id, skill_id, name, version, payload_hash, content_json, conflict, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                uuid.uuid4().hex,
                skill_id,
                name,
                next_version,
                payload_hash,
                json.dumps(
                    {
                        "name": name,
                        "applies_when": applies_when,
                        "rules": rules,
                        "tools": tools,
                    }
                ),
                1 if conflict else 0,
            ),
        )
        await conn.commit()
        return SkillStoreItem(
            skill_id=skill_id,
            name=name,
            applies_when=applies_when,
            enabled=True,
            version=next_version,
            conflict=conflict,
        )

    async def list_skills(self, *, limit: int = 100) -> list[SkillStoreItem]:
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT
                s.id AS skill_id,
                s.name,
                s.applies_when,
                s.enabled,
                COALESCE(v.version, 0) AS version,
                COALESCE(v.conflict, 0) AS conflict
            FROM skills s
            LEFT JOIN (
                SELECT sv.skill_id, sv.version, sv.conflict
                FROM skill_store_versions sv
                JOIN (
                    SELECT skill_id, MAX(version) AS max_version
                    FROM skill_store_versions
                    GROUP BY skill_id
                ) latest
                ON latest.skill_id = sv.skill_id AND latest.max_version = sv.version
            ) v
            ON v.skill_id = s.id
            ORDER BY s.updated_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            SkillStoreItem(
                skill_id=row["skill_id"],
                name=row["name"],
                applies_when=row["applies_when"],
                enabled=bool(row["enabled"]),
                version=int(row["version"] or 0),
                conflict=bool(row["conflict"]),
            )
            for row in rows
        ]

    async def optimize_tools_and_skills(
        self,
        *,
        task_text: str,
        available_tools: list[str],
    ) -> OptimizerResult:
        task_lower = task_text.lower()
        suggested_tools: list[str] = []
        reasons: list[str] = []

        def include(tool: str, reason: str) -> None:
            if tool in available_tools and tool not in suggested_tools:
                suggested_tools.append(tool)
                reasons.append(f"{tool}: {reason}")

        if any(keyword in task_lower for keyword in ("bug", "error", "fail", "trace")):
            include("Investigate", "Task indicates debugging/investigation.")
        if any(keyword in task_lower for keyword in ("test", "coverage", "assert")):
            include("Test", "Task references testing.")
        if any(keyword in task_lower for keyword in ("patch", "fix", "implement")):
            include("Patch", "Task requires implementation changes.")
        if any(keyword in task_lower for keyword in ("review", "risk", "compliance")):
            include("Review", "Task requests assessment/review.")
        if not suggested_tools and available_tools:
            suggested_tools.append(available_tools[0])
            reasons.append(f"{available_tools[0]}: Default fallback.")

        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT name, applies_when
            FROM skills
            WHERE enabled = 1
            ORDER BY updated_at DESC
            LIMIT 100
            """
        )
        rows = await cursor.fetchall()
        suggested_skills: list[str] = []
        for row in rows:
            applies_when = str(row["applies_when"] or "").lower()
            tokens = [token for token in _split_words(applies_when) if len(token) > 2]
            if tokens and any(token in task_lower for token in tokens):
                suggested_skills.append(str(row["name"]))
        suggested_skills = sorted(set(suggested_skills))[:20]

        await conn.execute(
            """
            INSERT INTO optimizer_runs
            (id, task_text, suggested_tools_json, suggested_skills_json, reasons_json, created_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                uuid.uuid4().hex,
                task_text,
                json.dumps(suggested_tools),
                json.dumps(suggested_skills),
                json.dumps(reasons),
            ),
        )
        await conn.commit()

        return OptimizerResult(
            suggested_tools=suggested_tools,
            suggested_skills=suggested_skills,
            reasons=reasons,
        )

    def classify_evidence_source(
        self,
        *,
        evidence_path: str | None,
        source_url: str | None,
    ) -> tuple[str, int, str]:
        from pathlib import Path

        resolved: Path | None = None
        if evidence_path:
            candidate = Path(evidence_path)
            if not candidate.is_absolute():
                candidate = self._config.workspace_path / candidate
            resolved = candidate.resolve()
        result = self._classifier.classify(evidence_path=resolved, source_url=source_url)
        return result.source_type, result.trust_level, result.extraction_method

    async def _detect_skill_conflict(
        self,
        *,
        name: str,
        rules: list[str],
        tools: list[str],
    ) -> bool:
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT content_json
            FROM skill_store_versions
            WHERE lower(name) = lower(?)
            ORDER BY created_at DESC
            LIMIT 10
            """,
            (name,),
        )
        rows = await cursor.fetchall()
        new_rules = set(rules)
        new_tools = set(tools)
        for row in rows:
            payload = json.loads(str(row["content_json"]))
            old_rules = set(payload.get("rules", []))
            old_tools = set(payload.get("tools", []))
            if old_rules != new_rules or old_tools != new_tools:
                if new_rules.intersection(old_rules) != new_rules:
                    return True
        return False

    def _hash_payload(self, payload: dict) -> str:
        normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return str(uuid.uuid5(uuid.NAMESPACE_OID, normalized))


def _split_words(text: str) -> list[str]:
    """Tokenize text into alphanumeric words."""
    token = ""
    output: list[str] = []
    for char in text:
        if char.isalnum() or char == "_":
            token += char
            continue
        if token:
            output.append(token)
            token = ""
    if token:
        output.append(token)
    return output
