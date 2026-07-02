"""Optional Jedi-based cross-module call resolution.

Resolves call targets whose receiver type comes from an imported module —
the gap that pure AST analysis cannot fill.

If jedi is not installed the resolver silently returns no results and the
indexer falls back to AST-only relationships (same behaviour as before).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import jedi

    _JEDI_AVAILABLE = True
except ImportError:  # pragma: no cover
    _JEDI_AVAILABLE = False


@dataclass(frozen=True)
class ResolvedCall:
    rel_id: str
    module_path: str   # absolute path to the file that defines the callee
    bare_name: str     # simple name of the callee (last component)


class JediResolver:
    """Resolve cross-module call targets using Jedi type inference.

    One instance is created per indexing run and reused across files so the
    Jedi project index is built once and cached for subsequent calls.
    """

    def __init__(self, workspace_root: str) -> None:
        self._workspace_root = workspace_root
        self._project: object | None = None
        if _JEDI_AVAILABLE:
            try:
                self._project = jedi.Project(workspace_root, added_sys_path=[])
            except Exception:
                logger.debug("JediResolver: failed to create project, disabling", exc_info=True)

    @property
    def available(self) -> bool:
        return _JEDI_AVAILABLE and self._project is not None

    def resolve(
        self,
        *,
        source: str,
        abs_file_path: str,
        call_sites: list[tuple[int, int, str]],
    ) -> list[ResolvedCall]:
        """Return resolved call targets for the given call sites.

        Args:
            source: Full source text of the file being analysed.
            abs_file_path: Absolute path to the file (required by Jedi).
            call_sites: List of (line, col, rel_id) tuples — one per unresolved
                        call relationship. line/col point to the last character
                        of the callable expression (e.g. the 's' of 'store_relationships').

        Returns:
            List of ResolvedCall. Entries are only included when Jedi produces a
            definitive result with a known module path inside the workspace.
        """
        if not self.available or not call_sites:
            return []

        try:
            # Jedi >=0.18 renamed Script's source kwarg to `code`.
            script = jedi.Script(code=source, path=abs_file_path, project=self._project)
        except Exception:
            logger.debug("JediResolver: Script() failed for %s", abs_file_path, exc_info=True)
            return []

        results: list[ResolvedCall] = []
        for line, col, rel_id in call_sites:
            try:
                defs = script.goto(line=line, column=col, follow_imports=True)
            except Exception:
                continue

            if not defs:
                continue

            d = defs[0]
            if not d.module_path or not d.name:
                continue

            module_path = str(d.module_path)

            # Only include targets that live inside the workspace — ignore
            # stdlib and site-packages, which have no symbol records in the DB.
            try:
                Path(module_path).relative_to(self._workspace_root)
            except ValueError:
                continue

            results.append(ResolvedCall(
                rel_id=rel_id,
                module_path=module_path,
                bare_name=d.name,
            ))

        return results
