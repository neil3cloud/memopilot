"""Tests for Group 3 production hardening features."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from agent.db import DatabaseManager


@pytest.mark.asyncio
async def test_workspace_rebuild_memory_reindexes_files(
    client: AsyncClient,
    test_token: str,
    tmp_workspace: Path,
):
    headers = {"X-Agent-Token": test_token}
    (tmp_workspace / "module.py").write_text(
        "def hello() -> str:\n    return 'hi'\n",
        encoding="utf-8",
    )

    await client.post("/v1/workspace/init", headers=headers)
    indexed = await client.post("/v1/workspace/index", headers=headers)
    assert indexed.status_code == 200
    assert indexed.json()["indexed_files"] == 1

    rebuilt = await client.post("/v1/workspace/rebuild-memory", headers=headers)
    assert rebuilt.status_code == 200
    rebuilt_data = rebuilt.json()
    assert rebuilt_data["rebuilt"] is True
    assert rebuilt_data["indexed_files"] == 1
    assert rebuilt_data["symbols_extracted"] >= 1


@pytest.mark.asyncio
async def test_provider_failure_handling_surfaces_error_without_crashing(
    client: AsyncClient,
    test_token: str,
):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    failed = await client.post(
        "/v1/provider/test-call",
        headers=headers,
        json={
            "provider": "test-provider",
            "model": "test-model",
            "prompt": "hello",
            "force_failure": True,
        },
    )
    assert failed.status_code == 502

    health = await client.get("/v1/health", headers=headers)
    assert health.status_code == 200
    assert health.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_detect_secrets_redaction_adds_typed_marker(
    client: AsyncClient,
    test_token: str,
):
    headers = {"X-Agent-Token": test_token}
    response = await client.post(
        "/v1/security/redact",
        headers=headers,
        json={"text": "api_key = super-secret-key-value"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["redacted_count"] >= 1
    assert "[REDACTED:" in payload["redacted_text"]


@pytest.mark.asyncio
async def test_corrupted_db_file_recovers_on_connect(tmp_path: Path):
    corrupt_path = tmp_path / "broken.db"
    corrupt_path.write_bytes(b"not-a-sqlite-database")

    db = DatabaseManager(corrupt_path)
    conn = await db.connect()
    cursor = await conn.execute("SELECT 1 AS ok")
    row = await cursor.fetchone()
    assert row["ok"] == 1
    assert db.recovery_backup_path is not None
    assert db.recovery_backup_path.exists()
    await db.close()
