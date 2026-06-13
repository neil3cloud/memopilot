"""Patch application engine for MemoPilot.

Implements the full patch apply lifecycle:
  1. Pre-check: git apply --check
  2. Snapshot: save original file contents before patch
  3. Apply: git apply (working tree only)
  4. Rollback: restore from snapshots on failure

Never uses shell=True. All subprocess calls use explicit argument lists.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import urllib.parse
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class PatchResult:
    """Result of a patch apply operation."""

    success: bool
    patch_attempt_id: str
    files_affected: list[str] = field(default_factory=list)
    error_message: str | None = None
    snapshot_manifest_path: str | None = None
    rolled_back: bool = False


@dataclass
class SnapshotManifest:
    """Manifest for a patch snapshot."""

    patch_attempt_id: str
    files: list[str]
    patch_hash: str
    created_at: str
    snapshot_dir: str


def _encode_file_path(file_path: str) -> str:
    """Encode a file path for use as a snapshot filename."""

    return urllib.parse.quote(file_path, safe="").replace("%", "_")


def _parse_patch_files(patch_content: str) -> list[str]:
    """Extract file paths from a unified diff patch."""

    files = []
    for line in patch_content.splitlines():
        if line.startswith("+++ b/"):
            files.append(line[6:])
        elif line.startswith("--- a/"):
            # Track source files too for renames/deletes
            pass
    return files


class Patcher:
    """Manages patch application with snapshot-based rollback."""

    def __init__(self, workspace_root: Path, memopilot_dir: Path) -> None:
        self.workspace_root = workspace_root
        self.snapshots_dir = memopilot_dir / "memory" / "snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

    def pre_check(self, patch_path: Path) -> tuple[bool, str]:
        """Run git apply --check to verify patch can apply cleanly.

        Returns (success, error_message).
        """

        result = subprocess.run(
            ["git", "apply", "--check", str(patch_path)],
            cwd=str(self.workspace_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return True, ""
        error = (result.stderr or result.stdout).strip()
        return False, error

    def snapshot_files(
        self, patch_attempt_id: str, files: list[str], patch_hash: str
    ) -> SnapshotManifest:
        """Save current content of files that will be affected by the patch."""

        snapshot_dir = self.snapshots_dir / f"patch-{patch_attempt_id}"
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        snapshotted_files = []
        for file_rel in files:
            file_abs = self.workspace_root / file_rel
            if file_abs.exists():
                encoded = _encode_file_path(file_rel)
                snap_path = snapshot_dir / f"{encoded}.snap"
                snap_path.write_bytes(file_abs.read_bytes())
                snapshotted_files.append(file_rel)

        manifest = SnapshotManifest(
            patch_attempt_id=patch_attempt_id,
            files=snapshotted_files,
            patch_hash=patch_hash,
            created_at=datetime.now(UTC).isoformat(),
            snapshot_dir=str(snapshot_dir),
        )

        manifest_path = snapshot_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "patch_attempt_id": manifest.patch_attempt_id,
                    "files": manifest.files,
                    "patch_hash": manifest.patch_hash,
                    "created_at": manifest.created_at,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        manifest.snapshot_dir = str(snapshot_dir)
        return manifest

    def apply_patch(self, patch_path: Path) -> tuple[bool, str]:
        """Apply the patch to the working tree (not staged).

        Returns (success, error_message).
        """

        result = subprocess.run(
            ["git", "apply", str(patch_path)],
            cwd=str(self.workspace_root),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            return True, ""
        error = (result.stderr or result.stdout).strip()
        return False, error

    def rollback(self, manifest: SnapshotManifest) -> bool:
        """Restore all files from a snapshot manifest."""

        snapshot_dir = Path(manifest.snapshot_dir)
        if not snapshot_dir.exists():
            logger.error("Snapshot directory not found: %s", snapshot_dir)
            return False

        for file_rel in manifest.files:
            encoded = _encode_file_path(file_rel)
            snap_path = snapshot_dir / f"{encoded}.snap"
            if snap_path.exists():
                target = self.workspace_root / file_rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(snap_path.read_bytes())
            else:
                logger.warning("Snapshot file missing for %s", file_rel)
        return True

    def apply_with_safety(self, patch_path: Path, patch_attempt_id: str) -> PatchResult:
        """Full patch apply lifecycle: pre-check → snapshot → apply → rollback on failure."""

        patch_content = patch_path.read_text(encoding="utf-8")
        files = _parse_patch_files(patch_content)
        patch_hash = hashlib.sha256(patch_content.encode()).hexdigest()

        ok, error = self.pre_check(patch_path)
        if not ok:
            return PatchResult(
                success=False,
                patch_attempt_id=patch_attempt_id,
                files_affected=files,
                error_message=f"Patch cannot apply cleanly: {error}",
            )

        manifest = self.snapshot_files(patch_attempt_id, files, patch_hash)

        ok, error = self.apply_patch(patch_path)
        if not ok:
            self.rollback(manifest)
            return PatchResult(
                success=False,
                patch_attempt_id=patch_attempt_id,
                files_affected=files,
                error_message=(f"Patch partially failed. All changes rolled back. Error: {error}"),
                snapshot_manifest_path=str(Path(manifest.snapshot_dir) / "manifest.json"),
                rolled_back=True,
            )

        return PatchResult(
            success=True,
            patch_attempt_id=patch_attempt_id,
            files_affected=files,
            snapshot_manifest_path=str(Path(manifest.snapshot_dir) / "manifest.json"),
        )

    def load_manifest(self, patch_attempt_id: str) -> SnapshotManifest | None:
        """Load a snapshot manifest by patch attempt ID."""

        manifest_path = self.snapshots_dir / f"patch-{patch_attempt_id}" / "manifest.json"
        if not manifest_path.exists():
            return None
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return SnapshotManifest(
            patch_attempt_id=data["patch_attempt_id"],
            files=data["files"],
            patch_hash=data["patch_hash"],
            created_at=data["created_at"],
            snapshot_dir=str(manifest_path.parent),
        )

    def rollback_last(self) -> PatchResult | None:
        """Rollback the most recent patch using its snapshot."""

        if not self.snapshots_dir.exists():
            return None
        snapshot_dirs = sorted(
            self.snapshots_dir.iterdir(),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for directory in snapshot_dirs:
            manifest_path = directory / "manifest.json"
            if manifest_path.exists():
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest = SnapshotManifest(
                    patch_attempt_id=data["patch_attempt_id"],
                    files=data["files"],
                    patch_hash=data["patch_hash"],
                    created_at=data["created_at"],
                    snapshot_dir=str(directory),
                )
                success = self.rollback(manifest)
                return PatchResult(
                    success=success,
                    patch_attempt_id=manifest.patch_attempt_id,
                    files_affected=manifest.files,
                    rolled_back=True,
                )
        return None
