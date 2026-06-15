"""Tests for GitHistoryIndexer — Layer 4 commit history."""
from __future__ import annotations

import json
import uuid
from unittest.mock import patch, MagicMock

import pytest
from httpx import AsyncClient

from agent.git_history_indexer import (
    GitHistoryIndexer,
    CommitRecord,
    _parse_git_log,
)


_SAMPLE_GIT_LOG = (
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa|Alice|2024-01-10T12:00:00+00:00|fix: update user validator\n"
    "10\t2\tapp/users/validator.py\n"
    "3\t0\ttests/test_validator.py\n"
    "\n"
    "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb|Bob|2024-01-05T09:00:00+00:00|refactor: extract helpers\n"
    "5\t1\tapp/core/helpers.py\n"
)


def test_parse_git_log_basic():
    commits = _parse_git_log(_SAMPLE_GIT_LOG)
    assert len(commits) == 2
    assert commits[0].commit_sha == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert commits[0].author_name == "Alice"
    assert "app/users/validator.py" in commits[0].files
    assert "tests/test_validator.py" in commits[0].files


def test_parse_git_log_empty():
    assert _parse_git_log("") == []


@pytest.mark.asyncio
async def test_index_git_history_stores_commits(client: AsyncClient, test_token: str, test_db):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    indexer = GitHistoryIndexer(db=test_db)

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = _SAMPLE_GIT_LOG

    with patch("subprocess.run", return_value=mock_result):
        count = await indexer.index_git_history("/fake/workspace")

    assert count == 2

    conn = await test_db.connect()
    cursor = await conn.execute("SELECT COUNT(*) FROM commit_history")
    row = await cursor.fetchone()
    assert row[0] == 2


@pytest.mark.asyncio
async def test_index_git_history_idempotent(client: AsyncClient, test_token: str, test_db):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    indexer = GitHistoryIndexer(db=test_db)

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = _SAMPLE_GIT_LOG

    with patch("subprocess.run", return_value=mock_result):
        count1 = await indexer.index_git_history("/fake/workspace")
        count2 = await indexer.index_git_history("/fake/workspace")

    assert count1 == 2
    assert count2 == 0


@pytest.mark.asyncio
async def test_get_relevant_commits_returns_matching(client: AsyncClient, test_token: str, test_db):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    conn = await test_db.connect()
    commit_id = uuid.uuid4().hex
    await conn.execute(
        """INSERT INTO commit_history
           (id, commit_sha, commit_message, author_name, committed_at, files_changed_json, workspace_root)
           VALUES (?, 'sha1', 'fix validator', 'Alice', datetime('now', '-2 days'), ?, '')""",
        (commit_id, json.dumps(["app/validator.py"])),
    )
    await conn.execute(
        """INSERT INTO commit_file_changes (id, commit_id, file_path, change_type)
           VALUES (?, ?, 'app/validator.py', 'modified')""",
        (uuid.uuid4().hex, commit_id),
    )
    await conn.commit()

    indexer = GitHistoryIndexer(db=test_db)
    commits = await indexer.get_relevant_commits(["app/validator.py"])

    assert len(commits) == 1
    assert commits[0].commit_message == "fix validator"


@pytest.mark.asyncio
async def test_get_relevant_commits_empty_files(client: AsyncClient, test_token: str, test_db):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    indexer = GitHistoryIndexer(db=test_db)
    commits = await indexer.get_relevant_commits([])
    assert commits == []


def test_format_commit_history_for_context():
    indexer = GitHistoryIndexer.__new__(GitHistoryIndexer)
    indexer._db = None  # type: ignore
    commits = [
        CommitRecord(
            id="cid1",
            commit_sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            commit_message="feat: add user endpoint",
            commit_summary=None,
            author_name="Dev",
            committed_at="2024-01-10T12:00:00",
            files=["app/api.py"],
        )
    ]
    text = indexer.format_commit_history_for_context(commits, ["app/api.py"])
    assert "feat: add user endpoint" in text or "aaaaaaaa" in text
    assert isinstance(text, str)
    assert len(text) > 0

