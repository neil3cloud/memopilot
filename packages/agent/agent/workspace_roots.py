"""Multi-workspace root management (v2 capability)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .db import DatabaseManager


@dataclass(frozen=True)
class WorkspaceRootItem:
    workspace_id: str
    root_path: str
    label: str
    active: bool


class WorkspaceRootsService:
    """Tracks known workspace roots and active workspace selection."""

    def __init__(self, *, config: Config, db: DatabaseManager) -> None:
        self._config = config
        self._db = db

    async def ensure_default_workspace_root(self) -> None:
        await self.add_workspace_root(
            root_path=str(self._config.workspace_path.resolve()),
            label=self._config.workspace_path.name or "workspace",
            activate=True,
        )

    async def list_roots(
        self,
        *,
        limit: int = 100,
        workspace_root: str | None = None,
    ) -> list[WorkspaceRootItem]:
        return await self.list_workspace_roots(limit=limit, workspace_root=workspace_root)

    async def list_workspace_roots(
        self,
        *,
        limit: int = 100,
        workspace_root: str | None = None,
    ) -> list[WorkspaceRootItem]:
        del workspace_root
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT id, root_path, label, active
            FROM workspace_roots
            ORDER BY active DESC, updated_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            WorkspaceRootItem(
                workspace_id=row["id"],
                root_path=row["root_path"],
                label=row["label"],
                active=bool(row["active"]),
            )
            for row in rows
        ]

    async def add_workspace_root(
        self,
        *,
        root_path: str,
        label: str | None,
        activate: bool,
        workspace_root: str | None = None,
    ) -> WorkspaceRootItem:
        resolved = self._resolve_candidate_root(root_path, workspace_root=workspace_root)
        if not resolved.exists() or not resolved.is_dir():
            raise ValueError(f"Workspace root not found: {resolved}")

        clean_label = (label or resolved.name or "workspace").strip()
        if not clean_label:
            clean_label = "workspace"

        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT id, active
            FROM workspace_roots
            WHERE root_path = ?
            LIMIT 1
            """,
            (str(resolved),),
        )
        existing = await cursor.fetchone()
        workspace_id = existing["id"] if existing is not None else uuid.uuid4().hex
        active_flag = bool(existing["active"]) if existing is not None else False
        if activate:
            active_flag = True

        await conn.execute(
            """
            INSERT INTO workspace_roots (id, root_path, label, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
            ON CONFLICT(root_path) DO UPDATE SET
                label = excluded.label,
                active = excluded.active,
                updated_at = datetime('now')
            """,
            (
                workspace_id,
                str(resolved),
                clean_label,
                1 if active_flag else 0,
            ),
        )
        if activate:
            await conn.execute(
                "UPDATE workspace_roots SET active = 0 WHERE root_path != ?", (str(resolved),)
            )
            await conn.execute(
                """
                UPDATE workspace_roots
                SET active = 1, updated_at = datetime('now')
                WHERE root_path = ?
                """,
                (str(resolved),),
            )
        await conn.commit()

        return WorkspaceRootItem(
            workspace_id=workspace_id,
            root_path=str(resolved),
            label=clean_label,
            active=active_flag,
        )

    async def set_active_root(
        self,
        root_path: str,
        *,
        workspace_root: str | None = None,
    ) -> WorkspaceRootItem:
        resolved = self._resolve_candidate_root(root_path, workspace_root=workspace_root)
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT id
            FROM workspace_roots
            WHERE root_path = ?
            LIMIT 1
            """,
            (str(resolved),),
        )
        row = await cursor.fetchone()
        if row is None:
            return await self.add_workspace_root(
                root_path=str(resolved),
                label=resolved.name or "workspace",
                activate=True,
                workspace_root=workspace_root,
            )
        return await self.activate_workspace_root(
            workspace_id=str(row["id"]),
            workspace_root=workspace_root,
        )

    async def activate_workspace_root(
        self,
        *,
        workspace_id: str,
        workspace_root: str | None = None,
    ) -> WorkspaceRootItem:
        del workspace_root
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT id, root_path, label
            FROM workspace_roots
            WHERE id = ?
            LIMIT 1
            """,
            (workspace_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError(f"Workspace root not found: {workspace_id}")

        await conn.execute("UPDATE workspace_roots SET active = 0")
        await conn.execute(
            "UPDATE workspace_roots SET active = 1, updated_at = datetime('now') WHERE id = ?",
            (workspace_id,),
        )
        await conn.commit()
        return WorkspaceRootItem(
            workspace_id=row["id"],
            root_path=row["root_path"],
            label=row["label"],
            active=True,
        )

    async def get_active_root(self, *, workspace_root: str | None = None) -> str:
        return str(await self.active_workspace_path(workspace_root=workspace_root))

    async def active_workspace_path(self, *, workspace_root: str | None = None) -> Path:
        explicit = self._normalize_explicit_workspace_root(workspace_root)
        if explicit is not None:
            return explicit

        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT root_path
            FROM workspace_roots
            WHERE active = 1
            ORDER BY updated_at DESC
            LIMIT 1
            """
        )
        row = await cursor.fetchone()
        if row is None:
            return self._config.workspace_path.resolve()
        return Path(str(row["root_path"])).resolve()

    async def resolve_workspace_root(self, workspace_root: str | None) -> Path:
        explicit = self._normalize_explicit_workspace_root(workspace_root)
        if explicit is not None:
            return explicit
        return await self.active_workspace_path()

    async def allowed_workspace_paths(self, *, workspace_root: str | None = None) -> list[Path]:
        del workspace_root
        roots = await self.list_workspace_roots(limit=1000)
        items = [Path(item.root_path).resolve() for item in roots]
        default_root = self._config.workspace_path.resolve()
        if default_root not in items:
            items.insert(0, default_root)
        return items

    def _resolve_candidate_root(self, root_path: str, *, workspace_root: str | None = None) -> Path:
        candidate = Path(root_path)
        if candidate.is_absolute():
            return candidate.resolve()
        base_root = (
            self._normalize_explicit_workspace_root(workspace_root)
            or self._config.workspace_path.resolve()
        )
        resolved = (base_root / candidate).resolve()
        if not resolved.is_relative_to(base_root):
            raise ValueError(f"Path traversal denied: {resolved} is outside workspace {base_root}")
        return resolved

    def _normalize_explicit_workspace_root(self, workspace_root: str | None) -> Path | None:
        if workspace_root is None or not workspace_root.strip():
            return None
        candidate = Path(workspace_root)
        if not candidate.is_absolute():
            candidate = (self._config.workspace_path / candidate).resolve()
        else:
            candidate = candidate.resolve()
        if not candidate.exists() or not candidate.is_dir():
            raise ValueError(f"Workspace root not found: {candidate}")
        return candidate
