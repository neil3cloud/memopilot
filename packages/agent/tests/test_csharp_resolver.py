"""Regression tests for CSharpResolver.backfill_relationship_symbols().

backfill_relationship_symbols() checked relation_type == "import" /
"inheritance", but csharp_extractor.py emits "imports" / "inherits" (matching
the symbol_relationships CHECK constraint) — the string mismatch meant C#
import and inheritance backfill silently never resolved anything, regardless
of whether a matching symbol existed. No existing test caught it because
no test exercised backfill_relationship_symbols() with real extractor-shaped
relation_type values.
"""

from __future__ import annotations

import pytest

from agent.csharp_resolver import CSharpResolver
from agent.graph_retriever import SymbolRelationshipRecord, make_relationship_id
from agent.migration_runner import run_migrations


async def _seed_symbol(conn, *, id: str, name: str, kind: str = "class") -> None:
    await conn.execute(
        """INSERT OR IGNORE INTO symbols
           (id, name, kind, file_path, start_line, end_line, content_hash)
           VALUES (?, ?, ?, 'service.cs', 1, 10, 'test')""",
        (id, name, kind),
    )
    await conn.commit()


@pytest.mark.asyncio
async def test_backfill_resolves_imports_relation_type(test_db):
    conn = await test_db.connect()
    await run_migrations(conn)
    await _seed_symbol(conn, id="sym-order-service", name="OrderService")

    rel = SymbolRelationshipRecord(
        id=make_relationship_id("sym-caller", "MyApp.Services.OrderService", "imports", None),
        from_symbol_id="sym-caller",
        to_symbol_id=None,
        to_symbol_name="MyApp.Services.OrderService",
        to_file_path=None,
        relation_type="imports",
        workspace_root="",
    )

    resolver = CSharpResolver("/workspace")
    resolved = await resolver.backfill_relationship_symbols(
        [rel], conn, file_namespace="MyApp.Billing"
    )

    assert len(resolved) == 1
    assert resolved[0].to_symbol_id == "sym-order-service"


@pytest.mark.asyncio
async def test_backfill_resolves_inherits_relation_type(test_db):
    conn = await test_db.connect()
    await run_migrations(conn)
    await _seed_symbol(conn, id="sym-base-service", name="BaseService")

    rel = SymbolRelationshipRecord(
        id=make_relationship_id("sym-derived", "BaseService", "inherits", None),
        from_symbol_id="sym-derived",
        to_symbol_id=None,
        to_symbol_name="BaseService",
        to_file_path=None,
        relation_type="inherits",
        workspace_root="",
    )

    resolver = CSharpResolver("/workspace")
    resolved = await resolver.backfill_relationship_symbols(
        [rel], conn, file_namespace="MyApp.Billing"
    )

    assert len(resolved) == 1
    assert resolved[0].to_symbol_id == "sym-base-service"


@pytest.mark.asyncio
async def test_backfill_leaves_unmatched_relation_type_unresolved(test_db):
    """references (HTTP routes) has no backfill branch — should pass through untouched."""
    conn = await test_db.connect()
    await run_migrations(conn)

    rel = SymbolRelationshipRecord(
        id=make_relationship_id("sym-x", "GET /orders", "references", None),
        from_symbol_id="sym-x",
        to_symbol_id=None,
        to_symbol_name="GET /orders",
        to_file_path=None,
        relation_type="references",
        workspace_root="",
    )

    resolver = CSharpResolver("/workspace")
    resolved = await resolver.backfill_relationship_symbols([rel], conn, file_namespace="MyApp")

    assert len(resolved) == 1
    assert resolved[0].to_symbol_id is None
