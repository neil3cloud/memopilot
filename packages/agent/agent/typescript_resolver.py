"""TypeScript/JavaScript cross-module import resolution."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .graph_retriever import SymbolRelationshipRecord

logger = logging.getLogger(__name__)


class TypeScriptResolver:
    """Resolves TypeScript/JavaScript imports to target files."""

    def __init__(self, workspace_root: str) -> None:
        self.workspace_root = Path(workspace_root)
        self._tsconfig_cache: dict[str, dict] | None = None
        self._alias_map: dict[str, str] = {}
        self._load_tsconfig()

    def _load_tsconfig(self) -> None:
        """Load tsconfig.json and extract path aliases."""
        tsconfig_path = self.workspace_root / "tsconfig.json"
        if not tsconfig_path.exists():
            return

        try:
            config = json.loads(tsconfig_path.read_text(encoding="utf-8"))
            self._tsconfig_cache = config

            # Extract path aliases from compilerOptions
            compiler_opts = config.get("compilerOptions", {})
            paths = compiler_opts.get("paths", {})

            for alias_pattern, target_paths in paths.items():
                if target_paths and len(target_paths) > 0:
                    # Simplify: use first target path, strip ** and /*.ts
                    target = target_paths[0]
                    target = target.replace("**", "").replace("/*", "").rstrip("/")
                    # Convert @app/* -> src (e.g., @app/services -> src)
                    alias_base = alias_pattern.replace("*", "").rstrip("/")
                    self._alias_map[alias_base] = target

            if self._alias_map:
                logger.debug(f"Loaded tsconfig aliases: {self._alias_map}")
        except Exception as e:
            logger.warning(f"Failed to load tsconfig.json: {e}")

    def resolve_import(
        self, import_path: str, source_file: str
    ) -> str | None:
        """Resolve import path to relative file path in workspace.

        Args:
            import_path: e.g. "./services/order", "@app/services/order", "react"
            source_file: Absolute file path of the importing file

        Returns:
            Relative path in workspace (e.g. "src/services/order.ts"), or None if unresolved.
        """
        # External module (stdlib, npm package)
        if not import_path.startswith(".") and not import_path.startswith("@"):
            return None

        # Absolute path alias: @app/services/order
        if import_path.startswith("@"):
            for alias_base, target in self._alias_map.items():
                if import_path.startswith(alias_base):
                    # Replace alias with target
                    rest = import_path[len(alias_base) :].lstrip("/")
                    resolved = f"{target}/{rest}" if rest else target
                    return self._resolve_to_files(resolved, source_file)
            # Alias not found in tsconfig
            return None

        # Relative import: ./services/order, ../utils/helper
        source_dir = Path(source_file).parent
        target_dir = (source_dir / import_path).resolve()

        # Try multiple extensions
        for ext in [".ts", ".tsx", ".js", ".jsx"]:
            candidate = target_dir.with_suffix(ext)
            if candidate.exists():
                try:
                    rel = candidate.relative_to(self.workspace_root)
                    return rel.as_posix()
                except ValueError:
                    pass

        # Try index file
        for ext in [".ts", ".tsx", ".js", ".jsx"]:
            candidate = target_dir / f"index{ext}"
            if candidate.exists():
                try:
                    rel = candidate.relative_to(self.workspace_root)
                    return rel.as_posix()
                except ValueError:
                    pass

        return None

    def _resolve_to_files(self, module_path: str, source_file: str) -> str | None:
        """Try to resolve a module path to actual file."""
        source_dir = Path(source_file).parent

        # Make path relative to workspace
        candidate = self.workspace_root / module_path

        for ext in [".ts", ".tsx", ".js", ".jsx"]:
            file_candidate = candidate.with_suffix(ext)
            if file_candidate.exists():
                try:
                    rel = file_candidate.relative_to(self.workspace_root)
                    return rel.as_posix()
                except ValueError:
                    pass

        # Try index
        for ext in [".ts", ".tsx", ".js", ".jsx"]:
            index_candidate = candidate / f"index{ext}"
            if index_candidate.exists():
                try:
                    rel = index_candidate.relative_to(self.workspace_root)
                    return rel.as_posix()
                except ValueError:
                    pass

        return None

    def resolve_import_target(
        self, source_file: str, import_path: str
    ) -> tuple[str, str] | None:
        """Resolve import to (target_file, exported_name).

        Returns (target_file, exported_name) or None if unresolved.
        """
        # Extract exported name from import: "import { OrderService } from './services'"
        # For now, just return the resolved file path
        resolved = self.resolve_import(import_path, source_file)
        if resolved:
            # Simple heuristic: export name might be in the import statement
            # This is handled by the caller (extract_relationships)
            return (resolved, "")
        return None
