"""Skill store, versioning, conflict detection, and tool/skill optimizer (v1.5 capability)."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass

import yaml

from .config import Config
from .db import DatabaseManager
from .tool_selector import select_tools


@dataclass(frozen=True)
class SkillStoreItem:
    skill_id: str
    name: str
    applies_when: str
    enabled: bool
    version: int
    conflict: bool
    source: str = "skill_store"


@dataclass(frozen=True)
class SkillConflictItem:
    first_skill_id: str
    first_name: str
    second_skill_id: str
    second_name: str
    language: str
    path_contains: str
    contradictory_rules: list[str]


@dataclass(frozen=True)
class OptimizerResult:
    suggested_tools: list[str]
    excluded_tools: list[str]
    suggested_skills: list[str]
    reasons: list[str]
    reasons_map: dict[str, str]


class SkillLoaderService:
    """Manages the skill store, versioning, and task-aware tool/skill optimization."""

    def __init__(self, *, config: Config, db: DatabaseManager) -> None:
        self._config = config
        self._db = db

    async def create_or_update_skill(
        self,
        *,
        name: str,
        applies_when: str,
        rules: list[str],
        tools: list[str],
    ) -> SkillStoreItem:
        normalized = self._normalize_skill_payload(
            {
                "name": name,
                "applies_when": applies_when,
                "rules": rules,
                "tools": tools,
            }
        )

        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT id
            FROM skills
            WHERE lower(name) = lower(?)
            LIMIT 1
            """,
            (normalized["name"],),
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
                normalized["name"],
                normalized["applies_when"],
                json.dumps(normalized["rules"]),
                json.dumps(normalized["tools"]),
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
        payload_hash = self._hash_payload(normalized)
        version_conflict = await self._detect_version_conflict(
            conn=conn,
            skill_id=skill_id,
            name=normalized["name"],
            rules=normalized["rules"],
            tools=normalized["tools"],
        )
        await conn.execute(
            """
            INSERT INTO skill_store_versions
            (id, skill_id, name, version, payload_hash, content_json, conflict, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                uuid.uuid4().hex,
                skill_id,
                normalized["name"],
                next_version,
                payload_hash,
                json.dumps(normalized),
                1 if version_conflict else 0,
            ),
        )
        await self._refresh_latest_conflict_flags(conn)
        await conn.commit()

        latest_items = await self._fetch_latest_skill_items(conn=conn, limit=500)
        current = next((item for item in latest_items if item.skill_id == skill_id), None)
        if current is None:
            current = SkillStoreItem(
                skill_id=skill_id,
                name=normalized["name"],
                applies_when=normalized["applies_when"],
                enabled=True,
                version=next_version,
                conflict=version_conflict,
            )
        return current

    async def import_skill_from_yaml(self, yaml_content: str) -> SkillStoreItem:
        if not yaml_content.strip():
            raise ValueError("yaml_content is required")
        try:
            loaded = yaml.safe_load(yaml_content) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid YAML: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ValueError("Skill YAML must define a mapping/object")
        normalized = self._normalize_skill_payload(loaded)
        return await self.create_or_update_skill(
            name=normalized["name"],
            applies_when=normalized["applies_when"],
            rules=normalized["rules"],
            tools=normalized["tools"],
        )

    async def list_skills(self, *, limit: int = 100) -> list[SkillStoreItem]:
        conn = await self._db.connect()
        return await self._fetch_latest_skill_items(conn=conn, limit=limit)

    async def detect_conflicts(self) -> list[SkillConflictItem]:
        conn = await self._db.connect()
        latest_payloads = await self._fetch_latest_skill_payloads(conn=conn, limit=500)
        grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
        for payload in latest_payloads:
            criteria = self._extract_applies_criteria(str(payload["applies_when"]))
            grouped.setdefault(criteria, []).append(payload)

        conflicts: list[SkillConflictItem] = []
        seen_pairs: set[tuple[str, str]] = set()
        for (language, path_contains), items in grouped.items():
            if len(items) < 2:
                continue
            for index, left in enumerate(items):
                for right in items[index + 1 :]:
                    contradictory_rules = self._find_contradictory_rules(
                        left.get("rules", []),
                        right.get("rules", []),
                    )
                    if not contradictory_rules:
                        continue
                    left_id = str(left["skill_id"])
                    right_id = str(right["skill_id"])
                    pair_key = tuple(sorted((left_id, right_id)))
                    if pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)
                    conflicts.append(
                        SkillConflictItem(
                            first_skill_id=left_id,
                            first_name=str(left["name"]),
                            second_skill_id=right_id,
                            second_name=str(right["name"]),
                            language=language,
                            path_contains=path_contains,
                            contradictory_rules=contradictory_rules,
                        )
                    )
        conflicts.sort(
            key=lambda item: (
                item.language,
                item.path_contains,
                item.first_name,
                item.second_name,
            )
        )
        return conflicts

    async def optimize_tools_and_skills(
        self,
        *,
        task_text: str,
        available_tools: list[str],
        task_type: str | None = None,
        budget_profile: str = "balanced",
    ) -> OptimizerResult:
        task_lower = task_text.lower()
        selection = select_tools(
            task_type or "",
            available_tools,
            task_text=task_text,
            budget_profile=budget_profile,
        )
        suggested_tools = list(selection.selected_tools)
        excluded_tools = list(selection.excluded_tools)
        reasons_map = dict(selection.reasons)

        def include(tool: str, reason: str) -> None:
            if tool in available_tools and tool not in suggested_tools:
                suggested_tools.append(tool)
                reasons_map[tool] = reason
                if tool in excluded_tools:
                    excluded_tools.remove(tool)

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
            reasons_map[available_tools[0]] = "Default fallback."
            if available_tools[0] in excluded_tools:
                excluded_tools.remove(available_tools[0])

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
        reasons = [f"{tool}: {reason}" for tool, reason in sorted(reasons_map.items())]

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
                json.dumps(
                    {
                        "included": reasons_map,
                        "excluded_tools": excluded_tools,
                        "task_type": task_type,
                        "budget_profile": budget_profile,
                    }
                ),
            ),
        )
        await conn.commit()

        return OptimizerResult(
            suggested_tools=suggested_tools,
            excluded_tools=excluded_tools,
            suggested_skills=suggested_skills,
            reasons=reasons,
            reasons_map=reasons_map,
        )

    async def _fetch_latest_skill_items(
        self,
        *,
        conn,
        limit: int,
    ) -> list[SkillStoreItem]:
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

    async def _fetch_latest_skill_payloads(
        self,
        *,
        conn,
        limit: int,
    ) -> list[dict[str, object]]:
        cursor = await conn.execute(
            """
            SELECT
                s.id AS skill_id,
                s.name,
                s.applies_when,
                s.enabled,
                latest_version.version,
                latest_version.conflict,
                latest_version.content_json
            FROM skills s
            JOIN (
                SELECT sv.skill_id, sv.version, sv.conflict, sv.content_json
                FROM skill_store_versions sv
                JOIN (
                    SELECT skill_id, MAX(version) AS max_version
                    FROM skill_store_versions
                    GROUP BY skill_id
                ) latest
                ON latest.skill_id = sv.skill_id AND latest.max_version = sv.version
            ) latest_version
            ON latest_version.skill_id = s.id
            ORDER BY s.updated_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        payloads: list[dict[str, object]] = []
        for row in rows:
            payload = json.loads(str(row["content_json"]))
            if not isinstance(payload, dict):
                continue
            payloads.append(
                {
                    "skill_id": row["skill_id"],
                    "name": row["name"],
                    "applies_when": row["applies_when"],
                    "enabled": bool(row["enabled"]),
                    "version": int(row["version"] or 0),
                    "conflict": bool(row["conflict"]),
                    "rules": payload.get("rules", []),
                    "tools": payload.get("tools", []),
                }
            )
        return payloads

    async def _detect_version_conflict(
        self,
        *,
        conn,
        skill_id: str,
        name: str,
        rules: list[str],
        tools: list[str],
    ) -> bool:
        cursor = await conn.execute(
            """
            SELECT content_json
            FROM skill_store_versions
            WHERE skill_id = ? OR lower(name) = lower(?)
            ORDER BY created_at DESC
            LIMIT 20
            """,
            (skill_id, name),
        )
        rows = await cursor.fetchall()
        new_rules = set(rules)
        new_tools = set(tools)
        for row in rows:
            payload = json.loads(str(row["content_json"]))
            if not isinstance(payload, dict):
                continue
            old_rules = set(payload.get("rules", []))
            old_tools = set(payload.get("tools", []))
            if old_rules != new_rules or old_tools != new_tools:
                if new_rules.intersection(old_rules) != new_rules or old_tools != new_tools:
                    return True
        return False

    async def _refresh_latest_conflict_flags(self, conn) -> None:
        latest_payloads = await self._fetch_latest_skill_payloads(conn=conn, limit=500)
        conflict_pairs = await self.detect_conflicts()
        store_conflicts: set[str] = set()
        for pair in conflict_pairs:
            store_conflicts.add(pair.first_skill_id)
            store_conflicts.add(pair.second_skill_id)

        for payload in latest_payloads:
            skill_id = str(payload["skill_id"])
            version_cursor = await conn.execute(
                """
                SELECT MAX(version) AS max_version
                FROM skill_store_versions
                WHERE skill_id = ?
                """,
                (skill_id,),
            )
            version_row = await version_cursor.fetchone()
            latest_version = int(version_row["max_version"] or 0)
            version_conflict = await self._detect_version_conflict(
                conn=conn,
                skill_id=skill_id,
                name=str(payload["name"]),
                rules=[str(item) for item in payload.get("rules", [])],
                tools=[str(item) for item in payload.get("tools", [])],
            )
            conflict_value = 1 if (version_conflict or skill_id in store_conflicts) else 0
            await conn.execute(
                """
                UPDATE skill_store_versions
                SET conflict = ?
                WHERE skill_id = ? AND version = ?
                """,
                (conflict_value, skill_id, latest_version),
            )

    def _normalize_skill_payload(self, payload: dict[str, object]) -> dict[str, object]:
        name = str(payload.get("name", "")).strip()
        if not name:
            raise ValueError("skill name is required")

        applies_when = str(payload.get("applies_when", "")).strip()
        if not applies_when:
            raise ValueError("applies_when is required")

        rules_value = payload.get("rules", [])
        if not isinstance(rules_value, list):
            raise ValueError("rules must be a list of strings")
        if not all(isinstance(item, str) for item in rules_value):
            raise ValueError("rules must be a list of strings")
        rules = [item.strip() for item in rules_value if item.strip()]

        tools_value = payload.get("tools", [])
        if not isinstance(tools_value, list):
            raise ValueError("tools must be a list of strings")
        if not all(isinstance(item, str) for item in tools_value):
            raise ValueError("tools must be a list of strings")
        tools = [item.strip() for item in tools_value if item.strip()]

        return {
            "name": name,
            "applies_when": applies_when,
            "rules": rules,
            "tools": tools,
        }

    def _extract_applies_criteria(self, applies_when: str) -> tuple[str, str]:
        language_pattern = r"language\s*==\s*['\"]([^'\"]+)['\"]"
        path_pattern = r"path_contains\s*==\s*['\"]([^'\"]+)['\"]"
        language_match = re.search(language_pattern, applies_when, re.IGNORECASE)
        path_match = re.search(path_pattern, applies_when, re.IGNORECASE)
        language = language_match.group(1).strip().lower() if language_match else "*"
        path_contains = path_match.group(1).strip().lower() if path_match else "*"
        return language, path_contains

    def _find_contradictory_rules(
        self,
        left_rules: object,
        right_rules: object,
    ) -> list[str]:
        if not isinstance(left_rules, list) or not isinstance(right_rules, list):
            return []
        conflicts: list[str] = []
        for left_rule in left_rules:
            if not isinstance(left_rule, str):
                continue
            left_polarity, left_subject = self._normalize_rule(left_rule)
            if not left_subject:
                continue
            for right_rule in right_rules:
                if not isinstance(right_rule, str):
                    continue
                right_polarity, right_subject = self._normalize_rule(right_rule)
                if not right_subject:
                    continue
                if left_polarity == right_polarity:
                    continue
                if self._subjects_overlap(left_subject, right_subject):
                    conflicts.append(f"{left_rule} <-> {right_rule}")
        return sorted(set(conflicts))

    def _normalize_rule(self, rule_text: str) -> tuple[str, str]:
        normalized = re.sub(r"\s+", " ", rule_text.strip().lower())
        negative_patterns = (
            "never ",
            "do not ",
            "don't ",
            "must not ",
            "should not ",
            "avoid ",
            "no ",
        )
        polarity = "positive"
        for marker in negative_patterns:
            if normalized.startswith(marker):
                polarity = "negative"
                normalized = normalized[len(marker) :]
                break
        else:
            if normalized.startswith("always "):
                normalized = normalized[len("always ") :]
            if normalized.startswith("prefer "):
                normalized = normalized[len("prefer ") :]
            if normalized.startswith("must "):
                normalized = normalized[len("must ") :]
            if normalized.startswith("should "):
                normalized = normalized[len("should ") :]

        for prefix in ("use ", "keep ", "allow ", "require "):
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix) :]
                break

        normalized = re.sub(r"[^a-z0-9\[\]_ ]+", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return polarity, normalized

    def _subjects_overlap(self, left: str, right: str) -> bool:
        return left == right or left in right or right in left

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
