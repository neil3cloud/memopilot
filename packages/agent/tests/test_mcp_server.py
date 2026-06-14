"""Tests for the MCP server module (unit tests for components)."""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

from agent.mcp_server import (
    TOOL_DEFINITIONS,
    TOOL_HANDLERS,
    MCPBackendClient,
    handle_patch_review,
)


class TestMCPBackendClient:
    """Tests for the MCPBackendClient class."""

    def test_raises_when_agent_lock_missing(self, tmp_path):
        """BackendClient raises RuntimeError when agent.lock is missing."""
        with patch.dict(os.environ, {"MEMOPILOT_WORKSPACE": str(tmp_path)}, clear=False):
            with pytest.raises(RuntimeError, match="agent.lock not found"):
                MCPBackendClient()

    def test_reads_port_from_agent_lock(self, tmp_path):
        """BackendClient reads port from agent.lock JSON file."""
        memopilot_dir = tmp_path / ".memopilot"
        memopilot_dir.mkdir()
        lock_file = memopilot_dir / "agent.lock"
        lock_file.write_text(json.dumps({"port": 9876}), encoding="utf-8")

        with patch.dict(
            os.environ,
            {
                "MEMOPILOT_WORKSPACE": str(tmp_path),
                "MEMOPILOT_TOKEN": "test-token",
            },
            clear=False,
        ):
            client = MCPBackendClient()
            assert client.port == 9876
            assert client.base_url == "http://127.0.0.1:9876/v1"
            assert client.headers["X-Agent-Token"] == "test-token"


class TestToolDefinitions:
    """Tests for tool definitions."""

    def test_six_tools_defined(self):
        """All 6 tools are defined."""
        assert len(TOOL_DEFINITIONS) == 6

    def test_all_tools_have_handlers(self):
        """Every defined tool has a corresponding handler."""
        tool_names = {tool_definition["name"] for tool_definition in TOOL_DEFINITIONS}
        handler_names = set(TOOL_HANDLERS)
        assert tool_names == handler_names

    def test_tool_schemas_valid(self):
        """All tool schemas have required fields."""
        for tool_definition in TOOL_DEFINITIONS:
            assert "name" in tool_definition
            assert "description" in tool_definition
            assert "inputSchema" in tool_definition
            assert tool_definition["inputSchema"]["type"] == "object"


@pytest.mark.asyncio
class TestPatchReviewHandler:
    """Tests for the patch review handler."""

    async def test_returns_no_changes_message_when_no_diff(self):
        """Returns informative message when no git diff exists."""
        backend = AsyncMock()
        with patch.dict(os.environ, {"MEMOPILOT_WORKSPACE": "C:\\workspace"}, clear=False):
            with patch("agent.mcp_server._get_git_diff", return_value=""):
                result = await handle_patch_review(backend, {})
                assert "No uncommitted changes" in result

    async def test_calls_backend_with_provided_diff(self):
        """When git_diff is provided, it's sent to the backend."""
        backend = AsyncMock()
        backend.post = AsyncMock(return_value={"rendered_report": "## Report\nAll good."})

        with patch.dict(os.environ, {"MEMOPILOT_WORKSPACE": "C:\\workspace"}, clear=False):
            result = await handle_patch_review(backend, {"git_diff": "diff --git a/test.py..."})
            assert "Report" in result
            backend.post.assert_called_once()
            call_args = backend.post.call_args[0]
            assert call_args[0] == "/v1/task/review-applied-patch"
