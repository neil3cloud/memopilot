"""Tests for the Patcher module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch as mock_patch

import pytest

from agent.patcher import Patcher, _encode_file_path, _parse_patch_files


@pytest.fixture
def patcher_env(tmp_path: Path):
    """Create a temporary workspace and memopilot dir for testing."""

    workspace = tmp_path / "repo"
    workspace.mkdir()
    memopilot = tmp_path / ".memopilot"
    memopilot.mkdir()
    (workspace / "hello.py").write_text("print('hello')\n", encoding="utf-8")
    return workspace, memopilot


class TestParsePatckFiles:
    def test_extracts_file_paths(self):
        patch = """diff --git a/hello.py b/hello.py
--- a/hello.py
+++ b/hello.py
@@ -1 +1 @@
-print('hello')
+print('world')
"""
        assert _parse_patch_files(patch) == ["hello.py"]

    def test_multiple_files(self):
        patch = """+++ b/src/a.py
+++ b/src/b.py
"""
        assert _parse_patch_files(patch) == ["src/a.py", "src/b.py"]


class TestEncodeFilePath:
    def test_simple_path(self):
        result = _encode_file_path("src/main.py")
        assert "/" not in result
        assert ".py" not in result or "_" in result


class TestPatcher:
    def test_pre_check_success(self, patcher_env):
        workspace, memopilot = patcher_env
        patcher = Patcher(workspace, memopilot)

        with mock_patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            ok, err = patcher.pre_check(Path("test.patch"))
            assert ok is True
            assert err == ""

    def test_pre_check_failure(self, patcher_env):
        workspace, memopilot = patcher_env
        patcher = Patcher(workspace, memopilot)

        with mock_patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="error: patch does not apply",
            )
            ok, err = patcher.pre_check(Path("test.patch"))
            assert ok is False
            assert "does not apply" in err

    def test_snapshot_files(self, patcher_env):
        workspace, memopilot = patcher_env
        patcher = Patcher(workspace, memopilot)

        manifest = patcher.snapshot_files("attempt-1", ["hello.py"], "abc123")
        assert manifest.patch_attempt_id == "attempt-1"
        assert "hello.py" in manifest.files
        manifest_path = Path(manifest.snapshot_dir) / "manifest.json"
        assert manifest_path.exists()
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert data["patch_attempt_id"] == "attempt-1"

    def test_rollback_restores_files(self, patcher_env):
        workspace, memopilot = patcher_env
        patcher = Patcher(workspace, memopilot)

        manifest = patcher.snapshot_files("attempt-2", ["hello.py"], "abc")
        (workspace / "hello.py").write_text("print('patched')\n", encoding="utf-8")
        success = patcher.rollback(manifest)
        assert success
        assert (workspace / "hello.py").read_text(encoding="utf-8") == "print('hello')\n"

    def test_apply_with_safety_pre_check_fail(self, patcher_env):
        workspace, memopilot = patcher_env
        patcher = Patcher(workspace, memopilot)

        patch_file = workspace / "bad.patch"
        patch_file.write_text("+++ b/hello.py\n", encoding="utf-8")

        with mock_patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="patch fails")
            result = patcher.apply_with_safety(patch_file, "attempt-3")
            assert result.success is False
            assert "cannot apply cleanly" in result.error_message.lower()

    def test_apply_with_safety_success(self, patcher_env):
        workspace, memopilot = patcher_env
        patcher = Patcher(workspace, memopilot)

        patch_file = workspace / "good.patch"
        patch_file.write_text(
            "+++ b/hello.py\n@@ -1 +1 @@\n-print('hello')\n+print('world')\n",
            encoding="utf-8",
        )

        with mock_patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = patcher.apply_with_safety(patch_file, "attempt-4")
            assert result.success is True
            assert result.snapshot_manifest_path is not None

    def test_load_manifest(self, patcher_env):
        workspace, memopilot = patcher_env
        patcher = Patcher(workspace, memopilot)

        patcher.snapshot_files("attempt-5", ["hello.py"], "xyz")
        manifest = patcher.load_manifest("attempt-5")
        assert manifest is not None
        assert manifest.patch_attempt_id == "attempt-5"
