"""Tests for workspace indexing endpoint."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from agent.db import DatabaseManager


@pytest.mark.asyncio
async def test_workspace_index_extracts_symbols_and_skips_gitignored(
    client: AsyncClient,
    test_token: str,
    tmp_workspace,
    test_db: DatabaseManager,
):
    (tmp_workspace / ".gitignore").write_text("ignored_dir/\n", encoding="utf-8")
    (tmp_workspace / "app.py").write_text(
        "import os\n\nclass Demo:\n    def run(self) -> None:\n        pass\n",
        encoding="utf-8",
    )
    (tmp_workspace / "ignored_dir").mkdir()
    (tmp_workspace / "ignored_dir" / "ignored.py").write_text(
        "def hidden() -> None:\n    pass\n",
        encoding="utf-8",
    )
    (tmp_workspace / "notes.txt").write_text("ignore me", encoding="utf-8")

    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)
    response = await client.post("/v1/workspace/index", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["python_project"] is True
    assert body["total_files_scanned"] == 1
    assert body["indexed_files"] == 1
    assert body["skipped_files"] >= 1
    assert body["symbols_extracted"] >= 3

    conn = test_db.connection
    assert conn is not None

    cursor = await conn.execute(
        "SELECT file_path, stale FROM file_index WHERE language = 'python'"
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1
    assert rows[0]["file_path"] == "app.py"
    assert rows[0]["stale"] == 0

    cursor = await conn.execute(
        "SELECT name, kind FROM symbols WHERE file_path = 'app.py' ORDER BY kind, name"
    )
    symbols = {(row["kind"], row["name"]) for row in await cursor.fetchall()}
    assert ("import", "os") in symbols
    assert ("class", "Demo") in symbols
    assert ("method", "Demo.run") in symbols


@pytest.mark.asyncio
async def test_workspace_index_marks_removed_files_as_stale(
    client: AsyncClient,
    test_token: str,
    tmp_workspace,
    test_db: DatabaseManager,
):
    (tmp_workspace / "keep.py").write_text(
        "def before() -> int:\n    return 1\n",
        encoding="utf-8",
    )
    (tmp_workspace / "remove.py").write_text(
        "def remove_me() -> None:\n    pass\n",
        encoding="utf-8",
    )

    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)
    first_response = await client.post("/v1/workspace/index", headers=headers)
    assert first_response.status_code == 200
    assert first_response.json()["indexed_files"] == 2

    (tmp_workspace / "keep.py").write_text(
        "def before() -> int:\n    return 2\n",
        encoding="utf-8",
    )
    (tmp_workspace / "remove.py").unlink()

    second_response = await client.post("/v1/workspace/index", headers=headers)
    assert second_response.status_code == 200
    second_body = second_response.json()
    assert second_body["indexed_files"] == 1
    assert second_body["stale_files"] == 1

    conn = test_db.connection
    assert conn is not None

    cursor = await conn.execute("SELECT stale FROM file_index WHERE file_path = 'remove.py'")
    stale_row = await cursor.fetchone()
    assert stale_row is not None
    assert stale_row["stale"] == 1
