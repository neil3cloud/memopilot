"""Tests for Phase T5 — Tool Mode Writeback pipeline."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from agent.api import app, configure
from agent.config import Config
from agent.db import DatabaseManager
from agent.migration_runner import run_migrations
from agent.tool_mode_writeback import (
    compute_diff_hash,
    detect_test_files,
    dismiss_writeback,
    execute_writeback,
    extract_modified_symbols,
    parse_diff_changed_files,
    sanitize_for_proposal_body,
)

FIXTURE_DIFF = """\
diff --git a/app/services/inventory.py b/app/services/inventory.py
index abc1234..def5678 100644
--- a/app/services/inventory.py
+++ b/app/services/inventory.py
@@ -10,6 +10,12 @@ class InventoryService:
+    def sell_item(self, item_id: str, quantity: int) -> bool:
+        item = self.get_item(item_id)
+        if item.is_expired():
+            raise ValueError(\"Cannot sell expired item\")
+        item.quantity -= quantity
+        return True

diff --git a/tests/test_inventory_service.py b/tests/test_inventory_service.py
index abc1234..def5678 100644
--- a/tests/test_inventory_service.py
+++ b/tests/test_inventory_service.py
@@ -1,3 +1,8 @@
+def test_sell_expired_item():
+    service = InventoryService()
+    with pytest.raises(ValueError):
+        service.sell_item(\"expired-1\", 1)
"""

SECRET_DIFF = """\
diff --git a/config.py b/config.py
--- a/config.py
+++ b/config.py
@@ -1,2 +1,3 @@
+API_KEY = \"sk-proj-abcdef1234567890abcdef1234567890\"
"""


@pytest_asyncio.fixture
async def wb_db(tmp_path: Path):
    """Database with all migrations for writeback tests."""

    db = DatabaseManager(Path(":memory:"))
    conn = await db.connect()
    await run_migrations(conn)
    yield db
    await db.close()


@pytest_asyncio.fixture
async def wb_client(tmp_path: Path, wb_db: DatabaseManager):
    """HTTP client with migrated DB for writeback endpoint tests."""

    token = "test-token-" + "a" * 48
    config = Config(
        workspace_path=tmp_path,
        memopilot_dir=tmp_path / ".memopilot",
        global_dir=tmp_path / ".memopilot-global",
    )
    with patch.dict(os.environ, {"MEMOPILOT_TOKEN": token}):
        configure(config, wb_db)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac, token


class TestWritebackPipeline:
    """Unit tests for the writeback pipeline functions."""

    def test_sanitize_blocks_diff_markers(self):
        text = "+++ a/file.py\n--- b/file.py\n@@ -1,3 +1,5 @@\nNormal line"
        result = sanitize_for_proposal_body(text)
        assert "+++ " not in result
        assert "--- " not in result
        assert "@@ " not in result
        assert "Normal line" in result

    def test_sanitize_blocks_secrets(self):
        text = 'api_key = "sk-proj-abcdefghij1234567890"'
        result = sanitize_for_proposal_body(text)
        assert "sk-proj" not in result
        assert "[REDACTED]" in result

    def test_compute_diff_hash_deterministic(self):
        h1 = compute_diff_hash("diff --git a/test.py")
        h2 = compute_diff_hash("diff --git a/test.py")
        assert h1 == h2
        assert len(h1) == 64

    def test_parse_diff_changed_files(self):
        files = parse_diff_changed_files(FIXTURE_DIFF)
        assert "app/services/inventory.py" in files
        assert "tests/test_inventory_service.py" in files

    def test_extract_modified_symbols(self):
        symbols = extract_modified_symbols(FIXTURE_DIFF)
        names = [symbol["name"] for symbol in symbols]
        assert "sell_item" in names

    def test_detect_test_files(self):
        files = ["app/service.py", "tests/test_service.py", "spec/helper.spec.ts"]
        tests = detect_test_files(files)
        assert "tests/test_service.py" in tests
        assert "spec/helper.spec.ts" in tests
        assert "app/service.py" not in tests


@pytest.mark.asyncio
class TestWritebackExecution:
    """Integration tests for execute_writeback."""

    async def test_success_outcome_creates_proposals(self, wb_db):
        conn = await wb_db.connect()
        result = await execute_writeback(
            conn,
            outcome_summary="Fixed expired item sale bug",
            outcome_status="success",
            git_diff=FIXTURE_DIFF,
            workspace_root="/workspace",
            caller="copilot_lm_tool",
        )
        assert result.proposals_count >= 1
        assert not result.already_processed
        classes = [proposal.memory_class for proposal in result.proposals]
        assert "fact" in classes

    async def test_symbol_proposals_for_modified_symbols(self, wb_db):
        conn = await wb_db.connect()
        result = await execute_writeback(
            conn,
            outcome_summary="Added sell_item method",
            outcome_status="success",
            git_diff=FIXTURE_DIFF,
            workspace_root="/workspace",
            caller="copilot_lm_tool",
        )
        titles = [proposal.title for proposal in result.proposals]
        assert any("sell_item" in title for title in titles)

    async def test_reverted_sets_reusable_false(self, wb_db):
        conn = await wb_db.connect()
        result = await execute_writeback(
            conn,
            outcome_summary="Reverted change",
            outcome_status="reverted",
            git_diff=FIXTURE_DIFF,
            workspace_root="/workspace",
            caller="copilot_lm_tool",
        )
        for proposal in result.proposals:
            assert proposal.reusable is False

    async def test_duplicate_writeback_rejected(self, wb_db):
        conn = await wb_db.connect()
        result1 = await execute_writeback(
            conn,
            outcome_summary="First writeback",
            outcome_status="success",
            git_diff=FIXTURE_DIFF,
            workspace_root="/workspace",
            caller="copilot_lm_tool",
        )
        assert not result1.already_processed

        result2 = await execute_writeback(
            conn,
            outcome_summary="Duplicate attempt",
            outcome_status="success",
            git_diff=FIXTURE_DIFF,
            workspace_root="/workspace",
            caller="copilot_lm_tool",
        )
        assert result2.already_processed

    async def test_secret_in_diff_increments_blocked_count(self, wb_db):
        conn = await wb_db.connect()
        result = await execute_writeback(
            conn,
            outcome_summary="Added config",
            outcome_status="success",
            git_diff=SECRET_DIFF,
            workspace_root="/workspace",
            caller="copilot_lm_tool",
        )
        assert result.blocked_content_count >= 1

    async def test_task_run_transitions_to_completed(self, wb_db):
        conn = await wb_db.connect()
        result = await execute_writeback(
            conn,
            outcome_summary="Test task",
            outcome_status="success",
            git_diff=FIXTURE_DIFF,
            workspace_root="/workspace",
            caller="copilot_lm_tool",
        )
        cursor = await conn.execute(
            "SELECT status FROM task_runs WHERE id = ?", [result.task_run_id]
        )
        row = await cursor.fetchone()
        assert row[0] == "completed_via_writeback"

    async def test_dismiss_writeback_sets_status(self, wb_db):
        conn = await wb_db.connect()
        import uuid
        from datetime import UTC, datetime

        task_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        await conn.execute(
            """INSERT INTO task_runs (id, user_request, status, workspace_root, source, patch_governance_available, created_at, updated_at)
               VALUES (?, 'test', 'awaiting_writeback', '/workspace', 'copilot_lm_tool', 0, ?, ?)""",
            [task_id, now, now],
        )
        await conn.commit()

        await dismiss_writeback(conn, task_id)

        cursor = await conn.execute(
            "SELECT status, writeback_dismissed FROM task_runs WHERE id = ?", [task_id]
        )
        row = await cursor.fetchone()
        assert row[0] == "writeback_dismissed"
        assert row[1] == 1

    async def test_rendered_summary_contains_proposals(self, wb_db):
        conn = await wb_db.connect()
        result = await execute_writeback(
            conn,
            outcome_summary="Fixed the bug",
            outcome_status="success",
            git_diff=FIXTURE_DIFF,
            workspace_root="/workspace",
            caller="copilot_lm_tool",
        )
        assert "Memory Update" in result.rendered_summary
        assert "Fixed the bug" in result.rendered_summary
        assert "pending_review" in result.rendered_summary


@pytest.mark.asyncio
class TestWritebackEndpoints:
    """HTTP endpoint tests for writeback."""

    async def test_writeback_endpoint_success(self, wb_client):
        client, token = wb_client
        resp = await client.post(
            "/v1/tool-mode/writeback",
            json={
                "outcome_summary": "Fixed expired item bug",
                "outcome_status": "success",
                "git_diff": FIXTURE_DIFF,
                "workspace_root": "/workspace",
                "caller": "copilot_lm_tool",
            },
            headers={"X-Agent-Token": token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["proposals_count"] >= 1
        assert data["already_processed"] is False

    async def test_writeback_endpoint_duplicate(self, wb_client):
        client, token = wb_client
        body = {
            "outcome_summary": "Test duplicate",
            "outcome_status": "success",
            "git_diff": FIXTURE_DIFF,
            "workspace_root": "/workspace",
            "caller": "copilot_lm_tool",
        }
        await client.post("/v1/tool-mode/writeback", json=body, headers={"X-Agent-Token": token})
        resp2 = await client.post("/v1/tool-mode/writeback", json=body, headers={"X-Agent-Token": token})
        assert resp2.status_code == 200
        assert resp2.json()["already_processed"] is True

    async def test_dismiss_endpoint(self, wb_client):
        client, token = wb_client
        resp = await client.post(
            "/v1/tool-mode/writeback",
            json={
                "outcome_summary": "To dismiss",
                "outcome_status": "success",
                "git_diff": FIXTURE_DIFF,
                "workspace_root": "/workspace",
                "caller": "copilot_lm_tool",
            },
            headers={"X-Agent-Token": token},
        )
        task_run_id = resp.json()["task_run_id"]

        resp2 = await client.post(
            "/v1/tool-mode/dismiss-writeback",
            json={"task_run_id": task_run_id},
            headers={"X-Agent-Token": token},
        )
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "dismissed"
