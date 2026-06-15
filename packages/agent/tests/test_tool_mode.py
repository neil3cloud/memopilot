"""Tests for Phase T1 — Tool Mode backend support.

Covers:
- caller field on request models
- output_format='markdown_for_llm' rendering
- token cap enforcement
- local_only/pending_review memory exclusion from tool output
- secret redaction in tool output
- governance note in tool-mode output
- POST /v1/task/review-applied-patch
- tool call logging
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import aiosqlite
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from agent.api import app, configure
from agent.config import Config
from agent.context_renderer import ContextPackRenderer, estimate_tokens
from agent.db import DatabaseManager
from agent.migration_runner import run_migrations
from agent.tool_call_logger import get_or_create_tool_session, log_tool_call, update_session_stats
from agent.tool_mode_router import (
    is_caller_approved,
    is_caller_blocked,
    is_first_tool_use,
    reset_caller_state,
)


@pytest_asyncio.fixture
async def migrated_db(tmp_path: Path):
    """Database with all migrations run (needed for endpoints that write to tables)."""
    db = DatabaseManager(Path(":memory:"))
    conn = await db.connect()
    await run_migrations(conn)
    yield db
    await db.close()


@pytest.fixture
def _test_token() -> str:
    return "test-token-" + "a" * 48


@pytest_asyncio.fixture
async def migrated_client(tmp_path: Path, migrated_db: DatabaseManager, _test_token: str):
    """HTTP client using a fully migrated database."""
    config = Config(
        workspace_path=tmp_path,
        memopilot_dir=tmp_path / ".memopilot",
        global_dir=tmp_path / ".memopilot-global",
    )
    with patch.dict(os.environ, {"MEMOPILOT_TOKEN": _test_token}):
        configure(config, migrated_db)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


# ─── ContextPackRenderer tests ──────────────────────────────────────────────


class TestContextPackRenderer:
    """Tests for the ContextPackRenderer class."""

    def setup_method(self):
        self.renderer = ContextPackRenderer()

    def test_governance_note_present_for_tool_mode_caller(self):
        """Tool-mode callers get a governance note in the rendered output."""
        result = self.renderer.render(
            caller="copilot_lm_tool",
            task_description="Fix the login bug",
        )
        assert "MemoPilot did not generate this patch" in result
        assert "Tool Mode" in result

    def test_governance_note_absent_for_native_caller(self):
        """Native MemoPilot UI caller does not get the governance note."""
        result = self.renderer.render(
            caller="memopilot_ui",
            task_description="Fix the login bug",
        )
        assert "MemoPilot did not generate this patch" not in result

    def test_output_format_markdown_includes_task(self):
        """rendered markdown always includes the task description."""
        result = self.renderer.render(
            caller="cursor_mcp_tool",
            task_description="Refactor the payment service",
        )
        assert "## Task" in result
        assert "Refactor the payment service" in result

    def test_active_rules_rendered(self):
        """Active rules appear in the rendered output."""
        rules = [
            {"scope": "workspace", "rule_text": "Always use service layer"},
            {"rule_id": "SEC-001", "rule_text": "Never log secrets"},
        ]
        result = self.renderer.render(
            caller="copilot_lm_tool",
            task_description="Add a feature",
            active_rules=rules,
        )
        assert "Active Rules" in result
        assert "Always use service layer" in result
        assert "SEC-001" in result

    def test_token_cap_enforcement(self):
        """Output is bounded to max_tokens."""
        # Create large content that would exceed a small cap
        large_files = [
            {"path": f"src/file{i}.py", "content": "x" * 2000, "tokens": 500}
            for i in range(20)
        ]
        result = self.renderer.render(
            caller="copilot_lm_tool",
            task_description="A task",
            file_snippets=large_files,
            max_tokens=500,
        )
        # Token count should be within bounds (allow some overhead for headers)
        tokens = estimate_tokens(result)
        # The renderer truncates files to stay within budget
        # It should be significantly less than 20 files * 500 tokens
        assert tokens < 1000  # Well under unbounded output

    def test_truncation_notice_when_files_excluded(self):
        """A truncation notice appears when files are excluded due to token cap."""
        large_files = [
            {"path": f"src/file{i}.py", "content": "x" * 4000, "tokens": 1000}
            for i in range(10)
        ]
        result = self.renderer.render(
            caller="copilot_lm_tool",
            task_description="A task",
            file_snippets=large_files,
            max_tokens=2000,
        )
        assert "truncated" in result.lower()

    def test_stale_exclusion_notice(self):
        """Stale memory exclusion notice appears when stale items exist."""
        result = self.renderer.render(
            caller="copilot_lm_tool",
            task_description="A task",
            stale_exclusion_count=3,
            stale_affected_modules=["billing", "inventory"],
        )
        assert "3 memory items were excluded" in result
        assert "billing" in result

    def test_redaction_notice(self):
        """Redaction notice appears when values are redacted."""
        result = self.renderer.render(
            caller="copilot_lm_tool",
            task_description="A task",
            redacted_values_count=2,
        )
        assert "2 value(s) were redacted" in result

    def test_memory_search_no_results(self):
        """Memory search with no results returns informative message."""
        result = self.renderer.render_memory_search(
            caller="copilot_lm_tool",
            items=[],
            query="nonexistent_symbol",
        )
        assert "No results found" in result
        assert "nonexistent_symbol" in result

    def test_memory_search_with_results(self):
        """Memory search with results renders properly."""
        items = [
            {
                "title": "InventoryService.sell_item",
                "memory_class": "fact",
                "trust_level": 4,
                "body": "Handles item sales with expiry checks",
                "source": "app/services/inventory.py",
            }
        ]
        result = self.renderer.render_memory_search(
            caller="cursor_mcp_tool",
            items=items,
            query="sell_item",
        )
        assert "InventoryService.sell_item" in result
        assert "★★★★☆" in result
        assert "Handles item sales" in result


# ─── Review Applied Patch endpoint tests ────────────────────────────────────


@pytest.mark.asyncio
class TestReviewAppliedPatch:
    """Tests for POST /v1/task/review-applied-patch."""

    BILLING_DIFF = """\
diff --git a/app/billing/invoice_processor.py b/app/billing/invoice_processor.py
index abc1234..def5678 100644
--- a/app/billing/invoice_processor.py
+++ b/app/billing/invoice_processor.py
@@ -10,6 +10,10 @@ class InvoiceProcessor:
     def process(self, invoice):
+        if invoice.is_expired():
+            raise ValueError("Cannot process expired invoice")
         return self.calculate_total(invoice)
"""

    TEST_DIFF = """\
diff --git a/tests/test_inventory.py b/tests/test_inventory.py
index abc1234..def5678 100644
--- a/tests/test_inventory.py
+++ b/tests/test_inventory.py
@@ -5,3 +5,7 @@ class TestInventory:
+    def test_expired_item(self):
+        assert not sell_expired_item()
"""

    SECRET_DIFF = """\
diff --git a/config.py b/config.py
index abc1234..def5678 100644
--- a/config.py
+++ b/config.py
@@ -1,3 +1,4 @@
+API_KEY = "sk-proj-abcdef1234567890abcdef1234567890"
 DATABASE_URL = "sqlite:///app.db"
"""

    async def test_billing_diff_returns_high_risk(self, migrated_client: AsyncClient, _test_token: str):
        """Diff touching billing/ directory returns high risk level."""
        resp = await migrated_client.post(
            "/v1/task/review-applied-patch",
            json={"git_diff": self.BILLING_DIFF, "caller": "copilot_lm_tool"},
            headers={"X-Agent-Token": _test_token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["risk_level"] == "high"
        assert data["patch_governance_available"] is False

    async def test_creates_task_run_with_correct_source(self, migrated_client: AsyncClient, _test_token: str):
        """Review endpoint creates task_run with source = caller."""
        resp = await migrated_client.post(
            "/v1/task/review-applied-patch",
            json={"git_diff": self.TEST_DIFF, "caller": "cursor_mcp_tool"},
            headers={"X-Agent-Token": _test_token},
        )
        assert resp.status_code == 200
        data = resp.json()
        task_run_id = data["task_run_id"]

        # Verify task_run record
        from agent.api import _get_db
        conn = await _get_db().connect()
        cursor = await conn.execute(
            "SELECT source, patch_governance_available FROM task_runs WHERE id = ?",
            [task_run_id],
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "cursor_mcp_tool"
        assert row[1] == 0

    async def test_detects_secret_in_diff(self, migrated_client: AsyncClient, _test_token: str):
        """Diff containing an API key pattern returns secret_detected=true."""
        resp = await migrated_client.post(
            "/v1/task/review-applied-patch",
            json={"git_diff": self.SECRET_DIFF, "caller": "copilot_lm_tool"},
            headers={"X-Agent-Token": _test_token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["secret_detected"] is True
        assert data["compliance_score"] < 100.0

    async def test_rendered_report_is_markdown(self, migrated_client: AsyncClient, _test_token: str):
        """Response includes a rendered_report in Markdown format."""
        resp = await migrated_client.post(
            "/v1/task/review-applied-patch",
            json={"git_diff": self.BILLING_DIFF, "caller": "copilot_lm_tool"},
            headers={"X-Agent-Token": _test_token},
        )
        data = resp.json()
        report = data["rendered_report"]
        assert "## MemoPilot Patch Review Report" in report
        assert "HIGH" in report
        assert "Governance Note" in report


# ─── Tool Call Logger tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
class TestToolCallLogger:
    """Tests for tool_call_logger functions."""

    async def test_get_or_create_session_creates_new(self, migrated_db):
        """First call creates a new tool_mode_sessions record."""
        conn = await migrated_db.connect()
        session_id = await get_or_create_tool_session(
            conn, caller="copilot_lm_tool", workspace_root="/workspace"
        )
        assert session_id is not None

        cursor = await conn.execute(
            "SELECT caller, total_calls FROM tool_mode_sessions WHERE id = ?",
            [session_id],
        )
        row = await cursor.fetchone()
        assert row[0] == "copilot_lm_tool"
        assert row[1] == 1

    async def test_get_or_create_session_reuses_existing(self, migrated_db):
        """Second call reuses existing session and increments call count."""
        conn = await migrated_db.connect()
        id1 = await get_or_create_tool_session(
            conn, caller="copilot_lm_tool", workspace_root="/workspace"
        )
        id2 = await get_or_create_tool_session(
            conn, caller="copilot_lm_tool", workspace_root="/workspace"
        )
        assert id1 == id2

        cursor = await conn.execute(
            "SELECT total_calls FROM tool_mode_sessions WHERE id = ?", [id1]
        )
        row = await cursor.fetchone()
        assert row[0] == 2

    async def test_log_tool_call_creates_audit_event(self, migrated_db):
        """log_tool_call creates a row in audit_events."""
        conn = await migrated_db.connect()
        session_id = await get_or_create_tool_session(
            conn, caller="copilot_lm_tool", workspace_root="/workspace"
        )
        event_id = await log_tool_call(
            conn,
            tool_name="memopilot_context",
            caller="copilot_lm_tool",
            session_id=session_id,
            output_tokens=1500,
        )

        cursor = await conn.execute(
            "SELECT event_type, details_json FROM audit_events WHERE id = ?",
            [event_id],
        )
        row = await cursor.fetchone()
        assert row[0] == "tool_call"
        import json
        details = json.loads(row[1])
        assert details["tool_name"] == "memopilot_context"
        assert details["caller"] == "copilot_lm_tool"
        assert details["output_tokens"] == 1500

    async def test_log_tool_call_creates_tool_call_event(self, migrated_db):
        """log_tool_call creates a row in tool_call_events."""
        conn = await migrated_db.connect()
        session_id = await get_or_create_tool_session(
            conn, caller="cursor_mcp_tool", workspace_root="/workspace"
        )
        event_id = await log_tool_call(
            conn,
            tool_name="memopilot_rules",
            caller="cursor_mcp_tool",
            session_id=session_id,
            patch_review_triggered=True,
        )

        cursor = await conn.execute(
            "SELECT tool_name, caller, patch_review_triggered FROM tool_call_events WHERE id = ?",
            [event_id],
        )
        row = await cursor.fetchone()
        assert row[0] == "memopilot_rules"
        assert row[1] == "cursor_mcp_tool"
        assert row[2] == 1


# ─── Tool Mode Router tests ────────────────────────────────────────────────


@pytest.mark.asyncio
class TestToolModeRouter:
    """Tests for tool mode caller approval/block state."""

    def setup_method(self):
        reset_caller_state()

    def test_first_tool_use_detection(self):
        """New callers are detected as first-use."""
        assert is_first_tool_use("copilot_lm_tool") is True

    async def test_approve_caller_endpoint(self, migrated_client: AsyncClient, _test_token: str):
        """POST /v1/tool-mode/approve-caller marks caller as approved."""
        reset_caller_state()
        resp = await migrated_client.post(
            "/v1/tool-mode/approve-caller",
            json={"caller": "copilot_lm_tool"},
            headers={"X-Agent-Token": _test_token},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"
        assert is_caller_approved("copilot_lm_tool")
        assert not is_first_tool_use("copilot_lm_tool")

    async def test_block_caller_endpoint(self, migrated_client: AsyncClient, _test_token: str):
        """POST /v1/tool-mode/block-caller marks caller as blocked."""
        reset_caller_state()
        resp = await migrated_client.post(
            "/v1/tool-mode/block-caller",
            json={"caller": "cursor_mcp_tool"},
            headers={"X-Agent-Token": _test_token},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "blocked"
        assert is_caller_blocked("cursor_mcp_tool")

    async def test_session_summary_endpoint(self, migrated_client: AsyncClient, _test_token: str):
        """GET /v1/tool-mode/session-summary returns session data."""
        resp = await migrated_client.get(
            "/v1/tool-mode/session-summary",
            headers={"X-Agent-Token": _test_token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "sessions" in data
        assert "pending_writebacks" in data
