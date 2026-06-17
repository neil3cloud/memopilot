"""Tests for the MCP server module (unit tests for components)."""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

from agent.mcp_server import MCPServer, _TOOL_SCHEMAS


class TestToolSchemas:
    def test_seven_tools_defined(self):
        assert len(_TOOL_SCHEMAS) == 7

    def test_all_tools_have_required_fields(self):
        for tool in _TOOL_SCHEMAS:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool
            assert tool["inputSchema"]["type"] == "object"

    def test_memopilot_patch_tool_present(self):
        names = {t["name"] for t in _TOOL_SCHEMAS}
        assert "memopilot-patch" in names

    def test_core_tools_present(self):
        names = {t["name"] for t in _TOOL_SCHEMAS}
        for expected in ("memory_search", "memory_store", "context_build",
                         "model_route", "patch_validate", "cost_check"):
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
        assert len(resp["result"]["tools"]) == 7

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


class TestPatchValidateHandler:
    def setup_method(self):
        self.server = MCPServer()

    @pytest.mark.asyncio
    async def test_valid_diff_accepted(self):
        diff = "--- a/file.py\n+++ b/file.py\n@@ -1,1 +1,1 @@\n-old\n+new\n"
        result = await self.server._handle_patch_validate({"diff": diff})
        assert "VALID" in result
        assert "1 hunk" in result

    @pytest.mark.asyncio
    async def test_invalid_diff_rejected(self):
        result = await self.server._handle_patch_validate({"diff": "not a diff"})
        assert "INVALID" in result


class TestMemopilotPatchHandler:
    def setup_method(self):
        self.server = MCPServer()

    @pytest.mark.asyncio
    async def test_returns_no_providers_message_when_backend_unavailable(self):
        with patch.object(
            self.server, "_backend_request",
            side_effect=Exception("Connection refused"),
        ):
            result = await self.server._handle_memopilot_patch(
                {"task_description": "Fix the bug"}
            )
        assert "Patch generation failed" in result

    @pytest.mark.asyncio
    async def test_returns_setup_instructions_when_no_providers(self):
        async def mock_backend(method, path, body=None):
            if path == "/v1/model/route":
                return {"recommended": {"provider": "none", "model_id": "none"}}
            return {}

        with patch.object(self.server, "_backend_request", side_effect=mock_backend):
            result = await self.server._handle_memopilot_patch(
                {"task_description": "Fix the bug"}
            )
        assert "No AI providers" in result
        assert "Ollama" in result

    @pytest.mark.asyncio
    async def test_returns_diff_on_success(self):
        async def mock_backend(method, path, body=None):
            if path == "/v1/model/route":
                return {"recommended": {"provider": "anthropic", "model_id": "claude-haiku-4-5"}}
            if path == "/v1/task/generate-patch":
                return {
                    "patches": [{"path": "src/main.py", "diff": "--- a/src/main.py\n+++ b/src/main.py\n"}],
                    "summary": "Fixed bug",
                    "model_used": "claude-haiku-4-5",
                    "estimated_risk": "low",
                }
            return {}

        with patch.object(self.server, "_backend_request", side_effect=mock_backend):
            result = await self.server._handle_memopilot_patch(
                {"task_description": "Fix the bug", "context_files": ["src/main.py"]}
            )
        assert "src/main.py" in result
        assert "Fixed bug" in result
