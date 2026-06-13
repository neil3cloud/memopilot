"""Workspace profile generation and persistence."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import Config
from .db import DatabaseManager

_PROFILE_ROW_ID = "default"
_USER_EDITED_MARKER = "# user-edited"
_DEFAULT_USER_EDITED_PATHS = {
    ("workspace", "test_commands"),
    ("workspace", "lint_commands"),
    ("workspace", "typecheck_commands"),
    ("workspace", "model_policy"),
    ("workspace", "privacy_policy"),
    ("workspace", "mcp"),
}


@dataclass(frozen=True)
class WorkspaceProfileResult:
    profile: dict[str, Any]
    profile_yaml: str


class WorkspaceProfileService:
    """Maintains YAML as source-of-truth and SQLite as a read cache."""

    def __init__(self, *, config: Config, db: DatabaseManager) -> None:
        self._config = config
        self._db = db
        self._profile_path = self._config.memopilot_dir / "workspace.profile.yaml"

    async def ensure_profile(self) -> WorkspaceProfileResult:
        if self._profile_path.exists():
            return await self.sync_from_yaml()
        return await self.rebuild_profile()

    async def sync_from_yaml(self) -> WorkspaceProfileResult:
        if not self._profile_path.exists():
            raise FileNotFoundError(f"workspace profile not found: {self._profile_path}")

        profile_yaml = self._profile_path.read_text(encoding="utf-8")
        parsed = self._parse_profile_yaml(profile_yaml)
        valid, issues = self._validate_profile_dict(parsed)
        if not valid:
            raise ValueError("invalid workspace profile: " + "; ".join(issues))

        conn = await self._db.connect()
        await conn.execute(
            """
            INSERT INTO workspace_profile (
                id, profile_yaml, detected_at, updated_at, is_cache, synced_from_yaml_at
            )
            VALUES (?, ?, datetime('now'), datetime('now'), 1, datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                profile_yaml = excluded.profile_yaml,
                updated_at = datetime('now'),
                is_cache = 1,
                synced_from_yaml_at = excluded.synced_from_yaml_at
            """,
            (_PROFILE_ROW_ID, profile_yaml),
        )
        await conn.commit()
        return WorkspaceProfileResult(profile=parsed, profile_yaml=profile_yaml)

    async def get_profile(self) -> WorkspaceProfileResult | None:
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT profile_yaml
            FROM workspace_profile
            WHERE id = ?
            """,
            (_PROFILE_ROW_ID,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        profile_yaml = str(row["profile_yaml"])
        parsed = self._parse_profile_yaml(profile_yaml)
        return WorkspaceProfileResult(profile=parsed, profile_yaml=profile_yaml)

    async def rebuild_profile(self) -> WorkspaceProfileResult:
        existing_profile: dict[str, Any] = {}
        user_edited_paths = set(_DEFAULT_USER_EDITED_PATHS)
        if self._profile_path.exists():
            existing_yaml = self._profile_path.read_text(encoding="utf-8")
            existing_profile = self._parse_profile_yaml(existing_yaml)
            user_edited_paths.update(self._extract_user_edited_paths(existing_yaml))

        detected = await self._detect_profile()
        merged = self._merge_preserving_user_edited(
            existing=existing_profile,
            detected=detected,
            user_edited_paths=user_edited_paths,
        )
        rendered_yaml = yaml.safe_dump(merged, sort_keys=False)
        profile_yaml = self._annotate_user_edited_fields(rendered_yaml, user_edited_paths)

        self._profile_path.parent.mkdir(parents=True, exist_ok=True)
        self._profile_path.write_text(profile_yaml, encoding="utf-8")
        return await self.sync_from_yaml()

    async def validate_profile(self) -> tuple[bool, list[str]]:
        if self._profile_path.exists():
            try:
                current = await self.sync_from_yaml()
            except (ValueError, yaml.YAMLError) as exc:
                return False, [str(exc)]
        else:
            current = await self.get_profile()
            if current is None:
                return False, ["workspace profile not found"]

        return self._validate_profile_dict(current.profile)

    async def export_profile(self, export_path: Path) -> str:
        profile = await self.ensure_profile()
        export_path.parent.mkdir(parents=True, exist_ok=True)
        export_path.write_text(profile.profile_yaml, encoding="utf-8")
        return str(export_path)

    async def _detect_profile(self) -> dict[str, Any]:
        conn = await self._db.connect()

        language = await self._detect_primary_language()
        frameworks = self._detect_frameworks()
        active_rules = self._detect_active_rules()
        active_skills = await self._detect_active_skills(conn)
        mcp_enabled = await self._detect_mcp_enabled(conn)

        workspace_name = self._config.workspace_path.name
        return {
            "workspace": {
                "name": workspace_name,
                "primary_language": language,
                "frameworks": frameworks,
                "test_commands": self._default_test_commands(language),
                "lint_commands": self._default_lint_commands(language),
                "typecheck_commands": self._default_typecheck_commands(language),
                "active_rules": active_rules,
                "active_skills": active_skills,
                "model_policy": {
                    "budget_profile": "cost_saver",
                    "allow_frontier": True,
                    "frontier_requires_approval": True,
                },
                "privacy_policy": {
                    "cloud_context_preview_required": True,
                    "redact_secrets": True,
                },
                "mcp": {
                    "azure_devops_enabled": mcp_enabled,
                    "database_enabled": mcp_enabled,
                },
            }
        }

    async def _detect_primary_language(self) -> str:
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT language, COUNT(*) AS total
            FROM file_index
            WHERE language IS NOT NULL
            GROUP BY language
            ORDER BY total DESC
            LIMIT 1
            """
        )
        row = await cursor.fetchone()
        if row is not None and row["language"]:
            return str(row["language"])
        return "python"

    def _detect_frameworks(self) -> list[str]:
        frameworks: list[str] = []
        pyproject = self._config.workspace_path / "pyproject.toml"
        if pyproject.exists():
            content = pyproject.read_text(encoding="utf-8", errors="replace").lower()
            for framework in ("fastapi", "sqlalchemy", "django", "flask"):
                if framework in content:
                    frameworks.append(framework)
        return frameworks

    def _detect_active_rules(self) -> list[str]:
        candidates = [
            self._config.memopilot_dir / "rules" / "project.rules.yaml",
            self._config.workspace_path / ".github" / "copilot-instructions.md",
            self._config.workspace_path / ".cursor" / "rules",
        ]
        return [
            str(path.relative_to(self._config.workspace_path))
            for path in candidates
            if path.exists()
        ]

    async def _detect_active_skills(self, conn) -> list[str]:
        cursor = await conn.execute(
            """
            SELECT name
            FROM skills
            WHERE enabled = 1
            ORDER BY updated_at DESC
            LIMIT 20
            """
        )
        rows = await cursor.fetchall()
        return [str(row["name"]) for row in rows]

    async def _detect_mcp_enabled(self, conn) -> bool:
        cursor = await conn.execute("SELECT COUNT(*) AS total FROM mcp_calls")
        row = await cursor.fetchone()
        return int(row["total"] or 0) > 0

    def _parse_profile_yaml(self, profile_yaml: str) -> dict[str, Any]:
        parsed = yaml.safe_load(profile_yaml) or {}
        if not isinstance(parsed, dict):
            raise ValueError("workspace profile must be a YAML mapping")
        return parsed

    def _validate_profile_dict(self, profile: dict[str, Any]) -> tuple[bool, list[str]]:
        workspace = profile.get("workspace")
        if not isinstance(workspace, dict):
            return False, ["workspace section missing"]

        issues: list[str] = []
        required_fields = ("name", "primary_language", "model_policy", "privacy_policy")
        for field in required_fields:
            if field not in workspace:
                issues.append(f"workspace.{field} missing")
        return len(issues) == 0, issues

    def _merge_preserving_user_edited(
        self,
        *,
        existing: dict[str, Any],
        detected: dict[str, Any],
        user_edited_paths: set[tuple[str, ...]],
    ) -> dict[str, Any]:
        merged = deepcopy(detected)
        for path in user_edited_paths:
            existing_value = self._get_nested_value(existing, path)
            if existing_value is not None:
                self._set_nested_value(merged, path, deepcopy(existing_value))
        return merged

    def _extract_user_edited_paths(self, profile_yaml: str) -> set[tuple[str, ...]]:
        paths: set[tuple[str, ...]] = set()
        key_stack: list[str] = []
        pending_user_edit = False

        for raw_line in profile_yaml.splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                pending_user_edit = _USER_EDITED_MARKER in stripped
                continue
            if stripped.startswith("- ") or ":" not in stripped:
                pending_user_edit = False
                continue

            indent = len(raw_line) - len(raw_line.lstrip(" "))
            level = indent // 2
            while len(key_stack) > level:
                key_stack.pop()

            key, _, remainder = stripped.partition(":")
            current_path = tuple(key_stack + [key.strip().strip("\"'")])
            if pending_user_edit or _USER_EDITED_MARKER in remainder:
                paths.add(current_path)

            value_without_comment = remainder.split("#", 1)[0].strip()
            if value_without_comment == "":
                if len(key_stack) == level:
                    key_stack.append(key.strip().strip("\"'"))
                else:
                    key_stack = key_stack[:level] + [key.strip().strip("\"'")]
            pending_user_edit = False

        return paths

    def _annotate_user_edited_fields(
        self,
        profile_yaml: str,
        user_edited_paths: set[tuple[str, ...]],
    ) -> str:
        if not user_edited_paths:
            return profile_yaml

        annotated_lines: list[str] = []
        key_stack: list[str] = []
        for raw_line in profile_yaml.splitlines():
            stripped = raw_line.strip()
            if (
                not stripped
                or stripped.startswith("#")
                or stripped.startswith("- ")
                or ":" not in stripped
            ):
                annotated_lines.append(raw_line)
                continue

            indent = len(raw_line) - len(raw_line.lstrip(" "))
            level = indent // 2
            while len(key_stack) > level:
                key_stack.pop()

            key, _, remainder = stripped.partition(":")
            normalized_key = key.strip().strip("\"'")
            current_path = tuple(key_stack + [normalized_key])
            line = raw_line
            if current_path in user_edited_paths and _USER_EDITED_MARKER not in raw_line:
                suffix = (
                    f"  {_USER_EDITED_MARKER}"
                    if raw_line.rstrip().endswith(":")
                    else f" {_USER_EDITED_MARKER}"
                )
                line = raw_line.rstrip() + suffix
            annotated_lines.append(line)

            value_without_comment = remainder.split("#", 1)[0].strip()
            if value_without_comment == "":
                if len(key_stack) == level:
                    key_stack.append(normalized_key)
                else:
                    key_stack = key_stack[:level] + [normalized_key]

        return "\n".join(annotated_lines) + "\n"

    def _get_nested_value(self, payload: dict[str, Any], path: tuple[str, ...]) -> Any | None:
        current: Any = payload
        for key in path:
            if not isinstance(current, dict) or key not in current:
                return None
            current = current[key]
        return current

    def _set_nested_value(self, payload: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
        current = payload
        for key in path[:-1]:
            next_value = current.get(key)
            if not isinstance(next_value, dict):
                next_value = {}
                current[key] = next_value
            current = next_value
        current[path[-1]] = value

    def _default_test_commands(self, language: str) -> list[str]:
        if language == "python":
            return ["pytest"]
        return []

    def _default_lint_commands(self, language: str) -> list[str]:
        if language == "python":
            return ["ruff check"]
        return []

    def _default_typecheck_commands(self, language: str) -> list[str]:
        if language == "python":
            return ["mypy ."]
        return []
