"""Context pack templates, versioning, and diffing (v1/v1.5 capability)."""

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
class ContextPackDiffResult:
    left_version_id: str
    right_version_id: str
    diff_text: str


class ContextBuilderService:
    """Manages context pack templates, versioning, and diff computation."""

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
