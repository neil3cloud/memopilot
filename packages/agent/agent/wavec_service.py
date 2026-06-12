"""Wave C services for v1.5 capability expansion."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

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
class BackupResult:
    backup_id: str
    backup_path: str
    item_count: int
    created_at: str


@dataclass(frozen=True)
class OptimizerResult:
    suggested_tools: list[str]
    suggested_skills: list[str]
    reasons: list[str]


@dataclass(frozen=True)
class BudgetProfileResult:
    active_profile: str
    monthly_budget_usd: float
    effective_budget_usd: float
    multiplier: float
    profiles: dict[str, float]


class WaveCService:
    """Implements Phase 17 wave-C capabilities."""

    def __init__(self, *, config: Config, db: DatabaseManager) -> None:
        self._config = config
        self._db = db
        self._classifier = EvidenceSourceClassifier()
        self._profiles: dict[str, float] = {
            "cost_saver": 0.7,
            "balanced": 1.0,
            "frontier": 1.5,
        }
        self._budget_settings_path = self._config.memopilot_dir / "settings.yaml"

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

    async def backup_memory(self) -> BackupResult:
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT
                id, type, title, body, source, source_path,
                trust_level, tags_json, stale, created_at, updated_at
            FROM memory_items
            ORDER BY updated_at DESC
            """
        )
        rows = await cursor.fetchall()
        payload = [
            {
                "id": row["id"],
                "type": row["type"],
                "title": row["title"],
                "body": row["body"],
                "source": row["source"],
                "source_path": row["source_path"],
                "trust_level": row["trust_level"],
                "tags_json": row["tags_json"],
                "stale": row["stale"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

        backup_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        backup_dir = self._config.memopilot_dir / "snapshots" / "memory-backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"{backup_id}.json"
        backup_path.write_text(
            json.dumps(
                {
                    "backup_id": backup_id,
                    "created_at": datetime.now(UTC).isoformat(),
                    "item_count": len(payload),
                    "items": payload,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return BackupResult(
            backup_id=backup_id,
            backup_path=str(backup_path),
            item_count=len(payload),
            created_at=datetime.now(UTC).isoformat(),
        )

    async def restore_memory(self, *, backup_path: str) -> int:
        source = Path(backup_path)
        if not source.is_absolute():
            source = (self._config.workspace_path / source).resolve()
        if not source.exists():
            raise ValueError(f"backup file not found: {source}")

        payload = json.loads(source.read_text(encoding="utf-8"))
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValueError("invalid backup payload")

        conn = await self._db.connect()
        await conn.execute("DELETE FROM memory_items")
        for item in items:
            await conn.execute(
                """
                INSERT INTO memory_items
                (
                    id, type, title, body, source, source_path, trust_level,
                    tags_json, stale, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("id") or uuid.uuid4().hex,
                    item.get("type", "note"),
                    item.get("title", "untitled"),
                    item.get("body", ""),
                    item.get("source", "backup"),
                    item.get("source_path"),
                    int(item.get("trust_level", 3)),
                    item.get("tags_json"),
                    int(item.get("stale", 0)),
                    item.get("created_at", datetime.now(UTC).isoformat()),
                    item.get("updated_at", datetime.now(UTC).isoformat()),
                ),
            )
        await conn.commit()
        return len(items)

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
            tokens = [token for token in re_split_words(applies_when) if len(token) > 2]
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

    async def get_budget_profiles(self) -> BudgetProfileResult:
        active = self._active_budget_profile()
        multiplier = self._profiles.get(active, 1.0)
        monthly = max(self._config.monthly_budget_usd, 0.0)
        return BudgetProfileResult(
            active_profile=active,
            monthly_budget_usd=monthly,
            effective_budget_usd=round(monthly * multiplier, 4),
            multiplier=multiplier,
            profiles=self._profiles.copy(),
        )

    async def set_budget_profile(self, profile: str) -> BudgetProfileResult:
        if profile not in self._profiles:
            raise ValueError(f"Unknown profile: {profile}")
        settings = self._read_workspace_settings()
        budget = settings.get("budget")
        if not isinstance(budget, dict):
            budget = {}
        budget["profile"] = profile
        settings["budget"] = budget
        self._budget_settings_path.parent.mkdir(parents=True, exist_ok=True)
        self._budget_settings_path.write_text(
            yaml.safe_dump(settings, sort_keys=False),
            encoding="utf-8",
        )
        self._config.budget_profile = profile
        return await self.get_budget_profiles()

    def classify_evidence_source(
        self,
        *,
        evidence_path: str | None,
        source_url: str | None,
    ) -> tuple[str, int, str]:
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

    def _active_budget_profile(self) -> str:
        settings = self._read_workspace_settings()
        budget = settings.get("budget")
        if isinstance(budget, dict):
            profile = budget.get("profile")
            if isinstance(profile, str) and profile in self._profiles:
                return profile
        if self._config.budget_profile in self._profiles:
            return self._config.budget_profile
        return "balanced"

    def _read_workspace_settings(self) -> dict:
        if not self._budget_settings_path.exists():
            return {}
        loaded = yaml.safe_load(self._budget_settings_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return loaded
        return {}


def re_split_words(text: str) -> list[str]:
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
