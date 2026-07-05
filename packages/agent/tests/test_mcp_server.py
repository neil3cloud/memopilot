"""Tests for the MCP server module (unit tests for components)."""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

from agent.mcp_server import MCPServer, _TOOL_SCHEMAS


class TestToolSchemas:
    def test_five_tools_defined(self):
        assert len(_TOOL_SCHEMAS) == 5

    def test_all_tools_have_required_fields(self):
        for tool in _TOOL_SCHEMAS:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool
            assert tool["inputSchema"]["type"] == "object"

    def test_memopilot_patch_tool_removed(self):
        names = {t["name"] for t in _TOOL_SCHEMAS}
        assert "memopilot-patch" not in names

    def test_retrieval_tools_present(self):
        names = {t["name"] for t in _TOOL_SCHEMAS}
        for expected in (
            "memopilot-search",
            "memopilot-symbols",
            "memopilot-memory",
            "memopilot-profile",
            "memopilot-ingest-session",
        ):
            assert expected in names


class TestMCPServerDispatch:
    def setup_method(self):
        self.server = MCPServer()

    @pytest.mark.asyncio
    async def test_initialize_response(self):
        req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        resp = await self.server._handle(req)
        assert resp["result"]["serverInfo"]["name"] == "memopilot"
        assert "tools" in resp["result"]["capabilities"]

    @pytest.mark.asyncio
    async def test_tools_list_response(self):
        req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        resp = await self.server._handle(req)
        assert len(resp["result"]["tools"]) == 5

    @pytest.mark.asyncio
    async def test_unknown_method_returns_error(self):
        req = {"jsonrpc": "2.0", "id": 3, "method": "unknown/method", "params": {}}
        resp = await self.server._handle(req)
        assert "error" in resp

    @pytest.mark.asyncio
    async def test_notification_returns_none(self):
        req = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        resp = await self.server._handle(req)
        assert resp is None

    @pytest.mark.asyncio
    async def test_unknown_tool_call_returns_error(self):
        req = {
            "jsonrpc": "2.0", "id": 4,
            "method": "tools/call",
            "params": {"name": "nonexistent_tool", "arguments": {}},
        }
        resp = await self.server._handle(req)
        assert "error" in resp


class TestMemopilotMemoryHandler:
    def setup_method(self):
        self.server = MCPServer()

    @pytest.mark.asyncio
    async def test_renders_memory_search_results(self):
        async def mock_backend(method, path, body=None):
            assert path == "/v1/memory/recall"
            return {
                "items": [
                    {
                        "title": "Settlement locking",
                        "body": "Optimistic locking avoids deadlocks.",
                        "memory_class": "fact",
                        "trust_level": 4,
                        "source": "review",
                    }
                ]
            }

        with patch.object(self.server, "_backend_request", side_effect=mock_backend):
            result = await self.server._handle_memopilot_memory({"query": "locking"})

        assert "MemoPilot Memory Search" in result
        assert "Settlement locking" in result

    @pytest.mark.asyncio
    async def test_empty_memory_search_results(self):
        async def mock_backend(method, path, body=None):
            return {"items": []}

        with patch.object(self.server, "_backend_request", side_effect=mock_backend):
            result = await self.server._handle_memopilot_memory({"query": "missing"})

        assert "No results found" in result


class TestMemopilotSearchHandler:
    def setup_method(self):
        self.server = MCPServer()

    @pytest.mark.asyncio
    async def test_search_uses_context_pack_endpoint(self):
        async def mock_backend(method, path, body=None):
            assert path == "/v1/context/assemble"
            return {
                "rendered_markdown": "## MemoPilot Context\n\n### `src/main.py`\n```\ndef run():\n    return True\n```",
                "context_pack_hash": "hash-123",
                "total_tokens": 100,
                "quality_verdict": "good",
            }

        with patch.object(self.server, "_backend_request", side_effect=mock_backend):
            result = await self.server._handle_memopilot_search({"query": "find billing flow"})

        assert "MemoPilot Context" in result
        assert "src/main.py" in result

    @pytest.mark.asyncio
    async def test_search_propagates_backend_errors(self):
        with patch.object(
            self.server, "_backend_request",
            side_effect=Exception("Connection refused"),
        ):
            with pytest.raises(Exception, match="Connection refused"):
                await self.server._handle_memopilot_search({"query": "Fix the bug"})

    @pytest.mark.asyncio
    async def test_symbols_render_results(self):
        async def mock_backend(method, path, body=None):
            assert path == "/v1/symbols/search"
            return {
                "symbols": [
                    {
                        "name": "TaskFlowController",
                        "kind": "class",
                        "file_path": "packages/extension/src/controllers/TaskFlowController.ts",
                        "start_line": 1,
                        "signature": "class TaskFlowController",
                        "summary": "Orchestrates task execution.",
                    }
                ]
            }

        with patch.object(self.server, "_backend_request", side_effect=mock_backend):
            result = await self.server._handle_memopilot_symbols({"query": "TaskFlowController"})

        assert "TaskFlowController" in result
        assert "Orchestrates task execution." in result

    @pytest.mark.asyncio
    async def test_profile_renders_yaml(self):
        async def mock_backend(method, path, body=None):
            assert path == "/v1/workspace/profile"
            return {"profile_yaml": "workspace:\n  name: demo\n  primary_language: python\n"}

        with patch.object(self.server, "_backend_request", side_effect=mock_backend):
            result = await self.server._handle_memopilot_profile()

        assert "MemoPilot Workspace Profile" in result
        assert "primary_language: python" in result

    @pytest.mark.asyncio
    async def test_ingest_session_renders_summary(self):
        async def mock_backend(method, path, body=None):
            assert method == "POST"
            assert path == "/v1/session/ingest"
            assert body == {"source": "auto", "session_id": "latest"}
            return {
                "session_id": "abc",
                "source": "copilot",
                "facts_written": 2,
                "already_ingested": False,
                "outcome": "ingested",
                "reason": "",
                "memory_item_ids": ["m1", "m2"],
            }

        with patch.object(self.server, "_backend_request", side_effect=mock_backend):
            result = await self.server._handle_memopilot_ingest_session({})

        assert "Session Ingest" in result
        assert "Source: copilot" in result
        assert "Facts written: 2" in result
        assert "Outcome: ingested" in result

    @pytest.mark.asyncio
    async def test_ingest_session_renders_reason_when_present(self):
        async def mock_backend(method, path, body=None):
            return {
                "session_id": "",
                "source": "auto",
                "facts_written": 0,
                "already_ingested": False,
                "outcome": "no_affinity",
                "reason": "No sessions matched this workspace (no file path overlap)",
                "memory_item_ids": [],
            }

        with patch.object(self.server, "_backend_request", side_effect=mock_backend):
            result = await self.server._handle_memopilot_ingest_session({})

        assert "Outcome: no_affinity" in result
        assert "Reason: No sessions matched this workspace" in result
