"""Workspace file scanner with basic .gitignore support."""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path

PYTHON_PROJECT_MARKERS = ("pyproject.toml", "setup.py", "requirements.txt")
DEFAULT_EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".memopilot",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
}


@dataclass(frozen=True)
class GitIgnorePattern:
    pattern: str
    negated: bool
    dir_only: bool
    anchored: bool


@dataclass(frozen=True)
class ScanResult:
    python_files: list[Path]
    skipped_files: int
    python_project: bool


def _normalize_rel_path(path: Path) -> str:
    return path.as_posix().lstrip("./")


class GitIgnoreMatcher:
    """Very small subset of gitignore behavior used for workspace scanning."""

    def __init__(self, patterns: list[GitIgnorePattern]) -> None:
        self._patterns = patterns

    @classmethod
    def from_workspace(cls, workspace_path: Path) -> GitIgnoreMatcher:
        gitignore_path = workspace_path / ".gitignore"
        if not gitignore_path.exists():
            return cls(patterns=[])

        patterns: list[GitIgnorePattern] = []
        for line in gitignore_path.read_text(encoding="utf-8").splitlines():
            candidate = line.strip()
            if not candidate or candidate.startswith("#"):
                continue

            negated = candidate.startswith("!")
            if negated:
                candidate = candidate[1:]
            dir_only = candidate.endswith("/")
            if dir_only:
                candidate = candidate[:-1]
            anchored = candidate.startswith("/")
            if anchored:
                candidate = candidate[1:]
            if candidate:
                patterns.append(
                    GitIgnorePattern(
                        pattern=candidate,
                        negated=negated,
                        dir_only=dir_only,
                        anchored=anchored,
                    )
                )

        return cls(patterns=patterns)

    def is_ignored(self, rel_path: Path, is_dir: bool) -> bool:
        path_value = _normalize_rel_path(rel_path)
        parts = rel_path.parts
        if any(part in DEFAULT_EXCLUDED_DIRS for part in parts):
            return True

        ignored = False
        for pattern in self._patterns:
            if self._matches(pattern, path_value=path_value, rel_path=rel_path, is_dir=is_dir):
                ignored = not pattern.negated
        return ignored

    def _matches(
        self,
        pattern: GitIgnorePattern,
        path_value: str,
        rel_path: Path,
        is_dir: bool,
    ) -> bool:
        if pattern.dir_only and not is_dir:
            return False

        pattern_value = pattern.pattern
        basename = rel_path.name

        if "/" not in pattern_value:
            return fnmatch.fnmatch(basename, pattern_value)

        if pattern.anchored:
            return fnmatch.fnmatch(path_value, pattern_value)

        return fnmatch.fnmatch(path_value, pattern_value) or path_value.endswith(
            f"/{pattern_value}"
        )


class WorkspaceScanner:
    """Scans workspace files and returns Python file candidates."""

    def __init__(self, workspace_path: Path) -> None:
        self.workspace_path = workspace_path
        self._gitignore = GitIgnoreMatcher.from_workspace(workspace_path)

    def scan(self) -> ScanResult:
        python_files: list[Path] = []
        skipped_files = 0

        for root, dirs, files in os.walk(self.workspace_path):
            root_path = Path(root)
            rel_root = root_path.relative_to(self.workspace_path)

            retained_dirs: list[str] = []
            for dir_name in dirs:
                rel_dir = rel_root / dir_name if rel_root != Path(".") else Path(dir_name)
                if self._gitignore.is_ignored(rel_dir, is_dir=True):
                    skipped_files += 1
                    continue
                retained_dirs.append(dir_name)
            dirs[:] = retained_dirs

            for file_name in files:
                rel_file = rel_root / file_name if rel_root != Path(".") else Path(file_name)
                if self._gitignore.is_ignored(rel_file, is_dir=False):
                    skipped_files += 1
                    continue
                if rel_file.suffix == ".py":
                    python_files.append(rel_file)

        python_files.sort(key=lambda path: path.as_posix())
        return ScanResult(
            python_files=python_files,
            skipped_files=skipped_files,
            python_project=self._is_python_project(python_files),
        )

    def _is_python_project(self, python_files: list[Path]) -> bool:
        for marker in PYTHON_PROJECT_MARKERS:
            if (self.workspace_path / marker).exists():
                return True
        return len(python_files) > 0
