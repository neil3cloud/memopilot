"""Tests for GraphRetriever — Layer 3 structural call graph."""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from agent.graph_retriever import (
    GraphRetriever,
    SymbolRelationshipRecord,
    make_relationship_id,
)


async def _seed_symbols(conn, entries: list[dict]) -> None:
    for e in entries:
        await conn.execute(
            """INSERT OR IGNORE INTO symbols
               (id, name, kind, file_path, start_line, end_line, content_hash)
               VALUES (?, ?, ?, ?, 1, 10, 'test')""",
            (e["id"], e["name"], e.get("kind", "function"), e["file_path"]),
        )
    await conn.commit()


async def _seed_relationship(conn, from_id: str, to_id: str, to_name: str, rel: str, to_file: str = "b.py") -> str:
    rid = make_relationship_id(from_id, to_name, rel, to_file)
    await conn.execute(
        """INSERT OR IGNORE INTO symbol_relationships
           (id, from_symbol_id, to_symbol_id, to_symbol_name, to_file_path, relation_type, workspace_root)
           VALUES (?, ?, ?, ?, ?, ?, '')""",
        (rid, from_id, to_id, to_name, to_file, rel),
    )
    await conn.commit()
    return rid


@pytest.mark.asyncio
async def test_get_callers_returns_direct_caller(client: AsyncClient, test_token: str, test_db):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    conn = await test_db.connect()
    await _seed_symbols(conn, [
        {"id": "sym-a", "name": "helper", "file_path": "a.py"},
        {"id": "sym-b", "name": "main_func", "file_path": "b.py"},
    ])
    await _seed_relationship(conn, "sym-b", "sym-a", "helper", "calls", "a.py")

    graph = GraphRetriever(db=test_db)
    callers = await graph.get_callers("sym-a")

    assert len(callers) == 1
    assert callers[0].id == "sym-b"
    assert callers[0].name == "main_func"
    assert callers[0].depth == 1


@pytest.mark.asyncio
async def test_get_callees_returns_direct_callee(client: AsyncClient, test_token: str, test_db):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    conn = await test_db.connect()
    await _seed_symbols(conn, [
        {"id": "sym-c", "name": "caller_fn", "file_path": "c.py"},
        {"id": "sym-d", "name": "util_fn", "file_path": "d.py"},
    ])
    await _seed_relationship(conn, "sym-c", "sym-d", "util_fn", "calls", "d.py")

    graph = GraphRetriever(db=test_db)
    callees = await graph.get_callees("sym-c")

    assert len(callees) == 1
    assert callees[0].id == "sym-d"
    assert callees[0].depth == 1


@pytest.mark.asyncio
async def test_find_callers_not_in_context_filters_correctly(client: AsyncClient, test_token: str, test_db):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    conn = await test_db.connect()
    await _seed_symbols(conn, [
        {"id": "sym-target", "name": "target_fn", "file_path": "target.py"},
        {"id": "sym-in",     "name": "in_ctx",    "file_path": "in_ctx.py"},
        {"id": "sym-out",    "name": "out_ctx",   "file_path": "out_ctx.py"},
    ])
    await _seed_relationship(conn, "sym-in",  "sym-target", "target_fn", "calls", "target.py")
    await _seed_relationship(conn, "sym-out", "sym-target", "target_fn", "calls", "target.py")

    graph = GraphRetriever(db=test_db)
    missing = await graph.find_callers_not_in_context("sym-target", {"in_ctx.py", "target.py"})

    assert len(missing) == 1
    assert missing[0].file_path == "out_ctx.py"


@pytest.mark.asyncio
async def test_get_import_dependents(client: AsyncClient, test_token: str, test_db):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    conn = await test_db.connect()
    await _seed_symbols(conn, [
        {"id": "sym-importer", "name": "importer", "file_path": "importer.py"},
        {"id": "sym-lib",      "name": "lib_fn",   "file_path": "lib.py"},
    ])
    rid = make_relationship_id("sym-importer", "lib_fn", "imports", "lib.py")
    await conn.execute(
        """INSERT OR IGNORE INTO symbol_relationships
           (id, from_symbol_id, to_symbol_id, to_symbol_name, to_file_path, relation_type, workspace_root)
           VALUES (?, ?, ?, ?, ?, 'imports', '')""",
        (rid, "sym-importer", "sym-lib", "lib_fn", "lib.py"),
    )
    await conn.commit()

    graph = GraphRetriever(db=test_db)
    dependents = await graph.get_import_dependents("lib.py")

    assert "importer.py" in dependents


@pytest.mark.asyncio
async def test_store_relationships_upserts_dedup(client: AsyncClient, test_token: str, test_db):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    conn = await test_db.connect()
    await _seed_symbols(conn, [
        {"id": "sym-x", "name": "fn_x", "file_path": "x.py"},
    ])

    graph = GraphRetriever(db=test_db)
    rel = SymbolRelationshipRecord(
        id=make_relationship_id("sym-x", "fn_y", "calls", "y.py"),
        from_symbol_id="sym-x",
        to_symbol_id=None,
        to_symbol_name="fn_y",
        to_file_path="y.py",
        relation_type="calls",
        workspace_root="",
    )
    # Insert twice — should not raise or duplicate
    await graph.store_relationships(conn, [rel, rel])

    cursor = await conn.execute("SELECT COUNT(*) FROM symbol_relationships WHERE from_symbol_id='sym-x'")
    row = await cursor.fetchone()
    assert row[0] == 1

