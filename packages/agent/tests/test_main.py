"""Tests for lockfile helpers in agent.main."""

from __future__ import annotations

import json
from pathlib import Path

from agent.main import read_lockfile, write_lockfile


def test_write_lockfile_includes_metadata(tmp_path: Path):
    """Lockfile writes the expanded backend metadata payload."""
    lock_path = tmp_path / "agent.lock"
    started_at = "2025-01-01T00:00:00Z"

    write_lockfile(
        lock_path,
        port=8765,
        pid=4321,
        started_at=started_at,
        schema_version=8,
        api_version=1,
    )

    data = json.loads(lock_path.read_text(encoding="utf-8"))
    assert data == {
        "port": 8765,
        "pid": 4321,
        "started_at": started_at,
        "schema_version": 8,
        "api_version": 1,
    }


def test_read_lockfile_returns_none_for_missing_or_invalid_lockfile(tmp_path: Path):
    """Missing or invalid lockfiles are treated as absent."""
    missing_lock = tmp_path / "missing.lock"
    invalid_lock = tmp_path / "invalid.lock"
    invalid_lock.write_text("{not-json}", encoding="utf-8")

    assert read_lockfile(missing_lock) is None
    assert read_lockfile(invalid_lock) is None


def test_read_lockfile_returns_dict_for_valid_lockfile(tmp_path: Path):
    """Valid lockfiles are parsed into dictionaries."""
    lock_path = tmp_path / "agent.lock"
    lock_path.write_text('{"port": 8765, "pid": 4321}', encoding="utf-8")

    assert read_lockfile(lock_path) == {"port": 8765, "pid": 4321}
