"""Standalone MCP server implementing the Model Context Protocol for MemoPilot.

Exposes retrieval-first tools over stdio JSON-RPC:
    memopilot-search, memopilot-symbols, memopilot-memory, memopilot-profile

Start with: python -m agent.mcp_server
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from .context_renderer import ContextPackRenderer

_TOOL_SCHEMAS = [
    {
        "name": "memopilot-search",
        "description": "Assemble bounded code context for a developer query.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_output_tokens": {"type": "integer", "default": 4000},
            },
            "required": ["query"],
        },
    },
    {
        "name": "memopilot-symbols",
        "description": "Look up symbols in the indexed workspace by exact or partial name.",
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
        "name": "memopilot-memory",
        "description": "Search the MemoPilot memory store for durable project facts.",
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
        "name": "memopilot-profile",
        "description": "Return the current workspace profile and inferred project signals.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


class MCPServer:
    def __init__(self) -> None:
        self._request_id: int = 0
        self._renderer = ContextPackRenderer()

    async def run(self) -> None:
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        def _read_stdin() -> None:
            try:
                for line in sys.stdin:
                    loop.call_soon_threadsafe(queue.put_nowait, line)
            except Exception:
                pass
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        import threading
        threading.Thread(target=_read_stdin, daemon=True).start()

        while True:
            try:
                line = await queue.get()
                if line is None:
                    break
                request = json.loads(line)
                response = await self._handle(request)
                if response is not None:
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()
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
        if tool_name == "memopilot-search":
            return await self._handle_memopilot_search(args)
        if tool_name == "memopilot-symbols":
            return await self._handle_memopilot_symbols(args)
        if tool_name == "memopilot-memory":
            return await self._handle_memopilot_memory(args)
        if tool_name == "memopilot-profile":
            return await self._handle_memopilot_profile()
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
        import httpx

        env = self._load_runtime_env()
        token = env.get("MEMOPILOT_TOKEN", "")
        port = int(env.get("MEMOPILOT_PORT", "8765"))
        url = f"http://127.0.0.1:{port}{path}"
        headers = {"X-Agent-Token": token}

        async with httpx.AsyncClient(timeout=60.0) as client:
            if method == "GET":
                resp = await client.get(url, headers=headers)
            else:
                resp = await client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def _handle_memopilot_search(self, args: dict[str, Any]) -> str:
        result = await self._backend_request(
            "POST",
            "/v1/context/assemble",
            {
                "task_description": args["query"],
                "files_in_focus": args.get("files_in_focus", []),
                "task_type_hint": args.get("task_type_hint", "general"),
                "caller": "cursor_mcp_tool",
                "max_output_tokens": args.get("max_output_tokens", 4000),
            },
        )
        return str(result.get("rendered_markdown", "## MemoPilot Context\n\nNo content available."))

    async def _handle_memopilot_symbols(self, args: dict[str, Any]) -> str:
        result = await self._backend_request(
            "POST",
            "/v1/symbols/search",
            {
                "query": args["query"],
                "limit": args.get("limit", 10),
            },
        )
        items = result.get("symbols", [])
        if not items:
            return f"## MemoPilot Symbols\n\nNo symbols found for: \"{args['query']}\"\n"

        lines = [f"## MemoPilot Symbols — \"{args['query']}\"\n", f"_{len(items)} result(s)_\n"]
        for item in items:
            location = f"{item.get('file_path', '?')}:{item.get('start_line', '?')}"
            lines.append(f"### {item.get('name', 'unknown')} [{item.get('kind', 'unknown')}]")
            lines.append(f"- Location: {location}")
            if item.get("signature"):
                lines.append(f"- Signature: `{item['signature']}`")
            if item.get("summary"):
                lines.append(f"- Summary: {item['summary']}")
            lines.append("")
        return "\n".join(lines).strip()

    async def _handle_memopilot_memory(self, args: dict[str, Any]) -> str:
        result = await self._backend_request(
            "POST",
            "/v1/memory/recall",
            {"query": args["query"], "limit": args.get("limit", 10)},
        )
        return self._renderer.render_memory_search(
            caller="cursor_mcp_tool",
            items=result.get("items", []),
            query=args["query"],
        )

    async def _handle_memopilot_profile(self) -> str:
        result = await self._backend_request("GET", "/v1/workspace/profile")
        profile_yaml = result.get("profile_yaml", "")
        if not profile_yaml:
            return "## MemoPilot Workspace Profile\n\nNo workspace profile is available.\n"
        return f"## MemoPilot Workspace Profile\n\n```yaml\n{profile_yaml.strip()}\n```"

    def _load_runtime_env(self) -> dict[str, str]:
        env = dict(os.environ)
        if env.get("MEMOPILOT_TOKEN") and env.get("MEMOPILOT_PORT"):
            return env

        workspace = env.get("MEMOPILOT_WORKSPACE") or os.getcwd()
        env_file = os.path.join(workspace, ".memopilot", ".cursor-mcp-env")
        if not os.path.exists(env_file):
            return env

        try:
            for raw_line in Path(env_file).read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                env.setdefault(key.strip(), value.strip())
        except OSError:
            return env
        return env

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
