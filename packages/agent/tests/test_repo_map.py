"""Tests for RepoMapGenerator — compact structural repo overview."""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from agent.repo_map_generator import RepoMapGenerator


async def _seed_symbol(conn, sid: str, name: str, kind: str, file_path: str, sig: str = "") -> None:
    await conn.execute(
        """INSERT OR IGNORE INTO symbols
           (id, name, kind, file_path, signature, start_line, end_line, content_hash)
           VALUES (?, ?, ?, ?, ?, 1, 10, 'test')""",
        (sid, name, kind, file_path, sig or None),
    )


@pytest.mark.asyncio
async def test_repo_map_includes_top_level_symbols(client: AsyncClient, test_token: str, test_db):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    conn = await test_db.connect()
    await _seed_symbol(conn, "s1", "UserService", "class", "app/services/user.py", "class UserService")
    await _seed_symbol(conn, "s2", "create_user", "function", "app/services/user.py", "def create_user(name: str)")
    await conn.commit()

    gen = RepoMapGenerator(db=test_db)
    result = await gen.generate(workspace_root="", max_tokens=2000)

    assert "app/services/user.py" in result
    assert "UserService" in result
    assert "create_user" in result


@pytest.mark.asyncio
async def test_repo_map_excludes_test_files(client: AsyncClient, test_token: str, test_db):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    conn = await test_db.connect()
    await _seed_symbol(conn, "t1", "test_fn", "function", "tests/test_user.py", "def test_fn()")
    await _seed_symbol(conn, "s3", "real_fn",  "function", "app/core.py", "def real_fn()")
    await conn.commit()

    gen = RepoMapGenerator(db=test_db)
    result = await gen.generate(workspace_root="", max_tokens=2000)

    assert "test_fn" not in result
    assert "real_fn" in result


@pytest.mark.asyncio
async def test_repo_map_respects_token_budget(client: AsyncClient, test_token: str, test_db):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    conn = await test_db.connect()
    for i in range(50):
        await _seed_symbol(
            conn, f"sym-{i}", f"func_{i}", "function",
            f"app/module_{i}.py", f"def func_{i}(x: int) -> str"
        )
    await conn.commit()

    gen = RepoMapGenerator(db=test_db)
    result = await gen.generate(workspace_root="", max_tokens=100)

    assert len(result) > 0
    assert len(result) <= 1500


@pytest.mark.asyncio
async def test_repo_map_empty_db_returns_empty_string(client: AsyncClient, test_token: str, test_db):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    gen = RepoMapGenerator(db=test_db)
    result = await gen.generate(workspace_root="no-such-workspace", max_tokens=500)
    assert isinstance(result, str)

