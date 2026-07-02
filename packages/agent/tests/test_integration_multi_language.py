"""Integration test: Multi-language indexer wiring."""

import pytest
from pathlib import Path

from agent.config import Config
from agent.db import DatabaseManager
from agent.workspace_indexer import WorkspaceIndexer
from agent.extractor_registry import ExtractorRegistry
from agent.migration_runner import run_migrations
from agent.python_extractor import PythonExtractor
from agent.typescript_extractor import TypeScriptExtractor
from agent.project_scanner import WorkspaceScanner


def test_scanner_multi_language():
    """Scanner should detect multiple language types."""
    registry = ExtractorRegistry()
    registry.register(PythonExtractor())
    registry.register(TypeScriptExtractor())

    extensions = registry.all_extensions()

    assert ".py" in extensions
    assert ".ts" in extensions
    assert ".tsx" in extensions
    assert ".js" in extensions
    assert ".jsx" in extensions


def test_scanner_accepts_multi_extensions(tmp_path):
    """Scanner should accept extensions list from registry."""
    (tmp_path / "main.py").write_text("print('hello')")
    (tmp_path / "app.ts").write_text("console.log('hello')")
    (tmp_path / "component.tsx").write_text("<div></div>")

    registry = ExtractorRegistry()
    registry.register(PythonExtractor())
    registry.register(TypeScriptExtractor())

    scanner = WorkspaceScanner(tmp_path, file_extensions=registry.all_extensions())
    result = scanner.scan()

    file_names = {p.name for p in result.python_files}
    assert "main.py" in file_names
    assert "app.ts" in file_names
    assert "component.tsx" in file_names


def test_registry_getters():
    """Registry should provide both extension and language lookups."""
    registry = ExtractorRegistry()
    registry.register(PythonExtractor())
    registry.register(TypeScriptExtractor())

    # By extension
    py_ext = registry.get(".py")
    assert py_ext is not None
    assert py_ext.language == "python"

    ts_ext = registry.get(".ts")
    assert ts_ext is not None
    assert ts_ext.language == "typescript"

    # By language
    py_lang = registry.get_by_language("python")
    assert py_lang is not None
    assert ".py" in py_lang.extensions

    ts_lang = registry.get_by_language("typescript")
    assert ts_lang is not None
    assert ".ts" in ts_lang.extensions


@pytest.mark.asyncio
async def test_workspace_indexer_persists_typescript_and_csharp_symbols(tmp_path: Path):
    """Full indexing run should persist multi-language symbols into SQLite."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    (workspace / "app.ts").write_text(
        """class OrderService {
    getOrder(id: number) {
        return id;
    }
}
""",
        encoding="utf-8",
    )
    (workspace / "service.cs").write_text(
        """namespace Billing;
public class PaymentService
{
    public void Charge() { }
}
""",
        encoding="utf-8",
    )

    config = Config(
        workspace_path=workspace,
        memopilot_dir=workspace / ".memopilot",
        global_dir=workspace / ".memopilot-global",
    )
    db = DatabaseManager(Path(":memory:"))
    conn = await db.connect()
    await run_migrations(conn)

    try:
        indexer = WorkspaceIndexer(config=config, db=db)
        result = await indexer.index_workspace()

        assert result.total_files_scanned == 2
        assert result.indexed_files == 2

        cursor = await conn.execute(
            """
            SELECT file_path, name, kind, start_line, end_line
            FROM symbols
            WHERE file_path IN ('app.ts', 'service.cs')
            ORDER BY file_path, kind, name
            """
        )
        rows = await cursor.fetchall()

        assert any(r["file_path"] == "app.ts" and r["name"] == "OrderService" and r["kind"] == "class" for r in rows)
        assert any(r["file_path"] == "app.ts" and r["name"] == "OrderService.getOrder" and r["kind"] == "method" for r in rows)
        assert any(r["file_path"] == "service.cs" and r["name"] == "PaymentService" and r["kind"] == "class" for r in rows)
        assert any(r["file_path"] == "service.cs" and r["name"] == "Charge" and r["kind"] == "method" for r in rows)
        assert all(r["start_line"] >= 1 for r in rows)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_typescript_cross_file_calls_resolve_after_full_indexing_run(tmp_path: Path):
    """A call to an imported function resolves to_symbol_id once the full
    workspace is indexed — the target file's symbols aren't necessarily
    indexed yet at the moment the calling file is processed, so this needs
    the deferred resolution pass in _resolve_cross_module_calls, not just
    same-file resolution done during extraction."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    (workspace / "orders.ts").write_text(
        """import { chargeCustomer } from './billing';

export function validatePayment(order) {
    chargeCustomer(order);
    return true;
}
""",
        encoding="utf-8",
    )
    (workspace / "billing.ts").write_text(
        """export function chargeCustomer(order) {
    return true;
}
""",
        encoding="utf-8",
    )

    config = Config(
        workspace_path=workspace,
        memopilot_dir=workspace / ".memopilot",
        global_dir=workspace / ".memopilot-global",
    )
    db = DatabaseManager(Path(":memory:"))
    conn = await db.connect()
    await run_migrations(conn)

    try:
        indexer = WorkspaceIndexer(config=config, db=db)
        result = await indexer.index_workspace()
        assert result.indexed_files == 2

        cursor = await conn.execute(
            """
            SELECT sr.to_symbol_id, sr.to_file_path, s.name AS callee_name
            FROM symbol_relationships sr
            JOIN symbols s ON s.id = sr.to_symbol_id
            WHERE sr.relation_type = 'calls' AND sr.to_symbol_name = 'chargeCustomer'
            """
        )
        rows = await cursor.fetchall()
        assert len(rows) == 1, "expected the cross-file call to chargeCustomer to resolve"
        assert rows[0]["to_file_path"] == "billing.ts"
        assert rows[0]["callee_name"] == "chargeCustomer"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_csharp_cross_file_calls_resolve_after_full_indexing_run(tmp_path: Path):
    """Same principle as the TS test above, for C#: a call to a method
    defined in another file resolves once the full workspace is indexed,
    via CSharpResolver's namespace-scoped backfill pass."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    (workspace / "billing.cs").write_text(
        """namespace Billing;
public class BillingService
{
    public bool ChargeCustomer(Order order)
    {
        return true;
    }
}
""",
        encoding="utf-8",
    )
    (workspace / "orders.cs").write_text(
        """namespace Orders;
using Billing;
public class OrderService
{
    public bool ValidatePayment(Order order)
    {
        var billing = new BillingService();
        billing.ChargeCustomer(order);
        return true;
    }
}
""",
        encoding="utf-8",
    )

    config = Config(
        workspace_path=workspace,
        memopilot_dir=workspace / ".memopilot",
        global_dir=workspace / ".memopilot-global",
    )
    db = DatabaseManager(Path(":memory:"))
    conn = await db.connect()
    await run_migrations(conn)

    try:
        indexer = WorkspaceIndexer(config=config, db=db)
        result = await indexer.index_workspace()
        assert result.indexed_files == 2

        cursor = await conn.execute(
            """
            SELECT sr.to_symbol_id, sr.to_file_path, s.name AS callee_name, s.file_path
            FROM symbol_relationships sr
            JOIN symbols s ON s.id = sr.to_symbol_id
            WHERE sr.relation_type = 'calls' AND sr.to_symbol_name = 'ChargeCustomer'
            """
        )
        rows = await cursor.fetchall()
        assert len(rows) == 1, "expected the cross-file call to ChargeCustomer to resolve"
        assert rows[0]["file_path"] == "billing.cs"
        assert rows[0]["callee_name"] == "ChargeCustomer"
    finally:
        await db.close()

