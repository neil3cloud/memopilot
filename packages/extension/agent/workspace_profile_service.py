"""Workspace profile generation and persistence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import Config
from .db import DatabaseManager


@dataclass(frozen=True)
class WorkspaceProfileResult:
    profile: dict[str, Any]
    profile_yaml: str


class WorkspaceProfileService:
    """Builds and persists workspace profile YAML from project introspection."""

    def __init__(self, *, config: Config, db: DatabaseManager) -> None:
        self._config = config
        self._db = db

    async def ensure_profile(self) -> WorkspaceProfileResult:
        current = await self.get_profile()
        if current is not None:
            return current
        return await self.rebuild_profile()

    async def get_profile(self) -> WorkspaceProfileResult | None:
        conn = await self._db.connect()
        cursor = await conn.execute(
            "SELECT profile_yaml FROM workspace_profile WHERE id = ?",
            ("default",),
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        profile_yaml = row["profile_yaml"]
        parsed = yaml.safe_load(profile_yaml) or {}
        return WorkspaceProfileResult(profile=parsed, profile_yaml=profile_yaml)

    async def rebuild_profile(self) -> WorkspaceProfileResult:
        existing = await self.get_profile()
        detected = await self._detect_profile()
        merged = self._merge_with_existing(existing.profile if existing else {}, detected)
        profile_yaml = yaml.safe_dump(merged, sort_keys=False)

        conn = await self._db.connect()
        await conn.execute(
            """
            INSERT INTO workspace_profile (id, profile_yaml, detected_at, updated_at)
            VALUES ('default', ?, datetime('now'), datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                profile_yaml = excluded.profile_yaml,
                updated_at = datetime('now')
            """,
            (profile_yaml,),
        )
        await conn.commit()
        return WorkspaceProfileResult(profile=merged, profile_yaml=profile_yaml)

    async def validate_profile(self) -> tuple[bool, list[str]]:
        current = await self.get_profile()
        if current is None:
            return False, ["workspace profile not found"]

        workspace = current.profile.get("workspace")
        if not isinstance(workspace, dict):
            return False, ["workspace section missing"]

        issues: list[str] = []
        required_fields = ("name", "primary_language", "model_policy", "privacy_policy")
        for field in required_fields:
            if field not in workspace:
                issues.append(f"workspace.{field} missing")
        return len(issues) == 0, issues

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

    def _merge_with_existing(
        self,
        existing: dict[str, Any],
        detected: dict[str, Any],
    ) -> dict[str, Any]:
        existing_workspace = existing.get("workspace")
        if not isinstance(existing_workspace, dict):
            return detected

        merged = detected.copy()
        detected_workspace = dict(detected["workspace"])
        user_preserve_fields = (
            "test_commands",
            "lint_commands",
            "typecheck_commands",
            "model_policy",
            "privacy_policy",
            "mcp",
        )
        for field in user_preserve_fields:
            value = existing_workspace.get(field)
            if value is not None:
                detected_workspace[field] = value

        for key, value in existing_workspace.items():
            if key not in detected_workspace:
                detected_workspace[key] = value

        merged["workspace"] = detected_workspace
        return merged

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
