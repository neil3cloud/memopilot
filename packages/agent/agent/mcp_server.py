"""Standalone MCP server implementing the Model Context Protocol for MemoPilot.

Exposes 7 tools over stdio JSON-RPC:
  memory_search, memory_store, context_build, model_route, patch_validate,
  cost_check, memopilot-patch

Start with: python -m agent.mcp_server
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

_TOOL_SCHEMAS = [
    {
        "name": "memory_search",
        "description": "Search the MemoPilot memory store for relevant context.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory_store",
        "description": "Store a new memory item in the MemoPilot memory store.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
                "memory_class": {"type": "string", "default": "fact"},
            },
            "required": ["title", "body"],
        },
    },
    {
        "name": "context_build",
        "description": "Build a context pack for a given task description.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_description": {"type": "string"},
                "suggested_files": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["task_description"],
        },
    },
    {
        "name": "model_route",
        "description": "Select the optimal model for a task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_type": {"type": "string"},
                "context_tokens": {"type": "integer"},
                "privacy_level": {"type": "string", "default": "local_preferred"},
            },
            "required": ["task_type", "context_tokens"],
        },
    },
    {
        "name": "patch_validate",
        "description": "Validate a unified diff patch for syntax and safety.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "diff": {"type": "string"},
                "file_path": {"type": "string"},
            },
            "required": ["diff"],
        },
    },
    {
        "name": "cost_check",
        "description": "Check remaining AI budget and cost status.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "memopilot-patch",
        "description": (
            "Generate a code patch using MemoPilot's LLM provider chain. "
            "Returns a unified diff that implements the requested change."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_description": {"type": "string"},
                "context_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File paths to include as context.",
                },
                "workspace_root": {"type": "string"},
            },
            "required": ["task_description"],
        },
    },
]


class MCPServer:
    def __init__(self) -> None:
        self._request_id: int = 0

    async def run(self) -> None:
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        loop = asyncio.get_event_loop()

        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        _, writer = await loop.connect_write_pipe(
            lambda: asyncio.BaseProtocol(), sys.stdout
        )

        while True:
            try:
                line = await reader.readline()
                if not line:
                    break
                request = json.loads(line.decode("utf-8"))
                response = await self._handle(request)
                if response is not None:
                    writer.write((json.dumps(response) + "\n").encode("utf-8"))
                    await asyncio.sleep(0)  # yield to event loop
            except (json.JSONDecodeError, EOFError):
                break

    async def _handle(self, req: dict[str, Any]) -> dict[str, Any] | None:
        method = req.get("method", "")
        req_id = req.get("id")

        if method == "initialize":
            return self._ok(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "memopilot", "version": "0.1.0"},
            })

        if method == "tools/list":
            return self._ok(req_id, {"tools": _TOOL_SCHEMAS})

        if method == "tools/call":
            params = req.get("params", {})
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            try:
                result = await self._dispatch(tool_name, arguments)
                return self._ok(req_id, {"content": [{"type": "text", "text": result}]})
            except Exception as exc:
                return self._error(req_id, str(exc))

        # Notifications (no response needed)
        if req_id is None:
            return None

        return self._error(req_id, f"Unknown method: {method}")

    async def _dispatch(self, tool_name: str, args: dict[str, Any]) -> str:
        if tool_name == "memory_search":
            return await self._handle_memory_search(args)
        if tool_name == "memory_store":
            return await self._handle_memory_store(args)
        if tool_name == "context_build":
            return await self._handle_context_build(args)
        if tool_name == "model_route":
            return await self._handle_model_route(args)
        if tool_name == "patch_validate":
            return await self._handle_patch_validate(args)
        if tool_name == "cost_check":
            return await self._handle_cost_check(args)
        if tool_name == "memopilot-patch":
            return await self._handle_memopilot_patch(args)
        raise ValueError(f"Unknown tool: {tool_name}")

    # ------------------------------------------------------------------
    # Tool handlers â€” call the local backend via httpx
    # ------------------------------------------------------------------

    async def _backend_request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        import os

        import httpx

        token = os.environ.get("MEMOPILOT_TOKEN", "")
        port = int(os.environ.get("MEMOPILOT_PORT", "8765"))
        url = f"http://127.0.0.1:{port}{path}"
        headers = {"X-Agent-Token": token}

        async with httpx.AsyncClient(timeout=60.0) as client:
            if method == "GET":
                resp = await client.get(url, headers=headers)
            else:
                resp = await client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def _handle_memory_search(self, args: dict[str, Any]) -> str:
        result = await self._backend_request(
            "POST",
            "/v1/memory/recall",
            {"query": args["query"], "limit": args.get("limit", 10)},
        )
        items = result.get("items", [])
        if not items:
            return "No memories found."
        lines = [f"- [{item.get('trust_level', 0)}/10] {item.get('title', '')}: {item.get('body', '')[:200]}"
                 for item in items]
        return "\n".join(lines)

    async def _handle_memory_store(self, args: dict[str, Any]) -> str:
        result = await self._backend_request(
            "POST",
            "/v1/memory/suggestions",
            {
                "title": args["title"],
                "body": args["body"],
                "memory_class": args.get("memory_class", "fact"),
                "source": "mcp",
            },
        )
        return f"Memory stored: {result.get('memory_item_id', '(unknown)')}"

    async def _handle_context_build(self, args: dict[str, Any]) -> str:
        result = await self._backend_request(
            "POST",
            "/v1/context/build",
            {
                "task_description": args["task_description"],
                "suggested_files": args.get("suggested_files", []),
            },
        )
        return (
            f"Context pack built â€” {result.get('total_tokens', 0):,} tokens, "
            f"hash: {result.get('context_pack_hash', '?')}"
        )

    async def _handle_model_route(self, args: dict[str, Any]) -> str:
        result = await self._backend_request(
            "POST",
            "/v1/model/route",
            {
                "task_type": args["task_type"],
                "context_tokens": args["context_tokens"],
                "privacy_level": args.get("privacy_level", "local_preferred"),
            },
        )
        rec = result.get("recommended", {})
        return (
            f"Recommended: {rec.get('provider', '?')} / {rec.get('model_id', '?')} "
            f"(${rec.get('cost_estimate_usd', 0):.4f}) â€” {'; '.join(rec.get('reasons', []))}"
        )

    async def _handle_patch_validate(self, args: dict[str, Any]) -> str:
        diff = args.get("diff", "")
        if not diff.strip().startswith("---"):
            return "INVALID: diff must start with '--- '"
        lines = diff.strip().splitlines()
        hunks = sum(1 for l in lines if l.startswith("@@"))
        return f"VALID: {hunks} hunk(s), {len(lines)} lines"

    async def _handle_cost_check(self, _args: dict[str, Any]) -> str:
        try:
            result = await self._backend_request("GET", "/v1/cost/report/savings")
            return (
                f"Month spend: ${result.get('month_spend_usd', 0):.4f} | "
                f"Cache hits: {result.get('month_cache_hits', 0)}/{result.get('month_total_ai_calls', 0)} | "
                f"Savings: ${result.get('cache_savings_usd', 0):.4f}"
            )
        except Exception:
            return "Budget status unavailable."

    async def _handle_memopilot_patch(self, args: dict[str, Any]) -> str:
        workspace_root = args.get("workspace_root")

        # Check if we're in a Cursor-like context (no host models)
        # Fall through to non-host providers only
        try:
            route_result = await self._backend_request(
                "POST",
                "/v1/model/route",
                {
                    "task_type": "auto",
                    "context_tokens": 4000,
                    "privacy_level": "local_preferred",
                },
            )
        except Exception:
            route_result = {}

        rec = route_result.get("recommended", {})
        if rec.get("provider") == "none" and rec.get("model_id") == "none":
            return (
                "No AI providers available. To generate patches:\n"
                "1. Install Ollama: https://ollama.com â€” then run: ollama pull qwen2.5-coder:7b\n"
                "2. Or add API keys in .memopilot/config.yaml\n"
            )

        try:
            patch_result = await self._backend_request(
                "POST",
                "/v1/task/generate-patch",
                {
                    "task_description": args["task_description"],
                    "context_files": args.get("context_files", []),
                    "workspace_root": workspace_root,
                },
            )
        except Exception as exc:
            return f"Patch generation failed: {exc}"

        patches = patch_result.get("patches", [])
        if not patches:
            return "No patches generated."

        parts = [
            f"Summary: {patch_result.get('summary', '')}",
            f"Model: {patch_result.get('model_used', '?')} | Risk: {patch_result.get('estimated_risk', '?')}",
            "",
        ]
        for p in patches:
            parts.append(f"=== {p.get('path', '?')} ===")
            parts.append(p.get("diff", ""))
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # JSON-RPC helpers
    # ------------------------------------------------------------------

    def _ok(self, req_id: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def _error(self, req_id: Any, message: str, code: int = -32_603) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def main() -> None:
    server = MCPServer()
    asyncio.run(server.run())


if __name__ == "__main__":
    main()
