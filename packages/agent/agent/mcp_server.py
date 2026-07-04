"""Standalone MCP server implementing the Model Context Protocol for MemoPilot.

Exposes retrieval-first tools over stdio JSON-RPC:
    memopilot-search, memopilot-symbols, memopilot-memory, memopilot-profile

Works with any MCP-compatible client:
    - Claude Code (VS Code extension)
    - Claude CLI
    - Gemini CLI
    - Cursor

Auto-starts the MemoPilot backend if not already running.

Start with: python -m agent.mcp_server
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .context_renderer import ContextPackRenderer

logger = logging.getLogger(__name__)

_WORKSPACE_PROP = {
    "workspace": {
        "type": "string",
        "description": "Absolute path to the project root. Auto-detected if omitted.",
    },
}

_TOOL_SCHEMAS = [
    {
        "name": "memopilot-search",
        "description": "Assemble bounded code context for a developer query.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_output_tokens": {"type": "integer", "default": 4000},
                **_WORKSPACE_PROP,
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
                **_WORKSPACE_PROP,
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
                **_WORKSPACE_PROP,
            },
            "required": ["query"],
        },
    },
    {
        "name": "memopilot-profile",
        "description": "Return the current workspace profile and inferred project signals.",
        "inputSchema": {
            "type": "object",
            "properties": {
                **_WORKSPACE_PROP,
            },
        },
    },
]

_BACKEND_STARTUP_TIMEOUT = 60
_HEALTH_CHECK_INTERVAL = 0.5


def _resolve_workspace() -> Path:
    for var in ("MEMOPILOT_WORKSPACE", "CLAUDE_PROJECT_DIR", "GEMINI_PROJECT_DIR"):
        val = os.environ.get(var)
        if val:
            return Path(val)
    return Path(os.getcwd())


def _memopilot_dir(workspace: Path) -> Path:
    return workspace / ".memopilot"


def _lock_path(workspace: Path) -> Path:
    return _memopilot_dir(workspace) / "agent.lock"


def _mcp_env_path(workspace: Path) -> Path:
    return _memopilot_dir(workspace) / ".mcp-env"


def _read_lock(workspace: Path) -> dict[str, Any] | None:
    path = _lock_path(workspace)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "port" in data and "pid" in data:
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _read_mcp_env(workspace: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    path = _mcp_env_path(workspace)
    if not path.exists():
        cursor_path = _memopilot_dir(workspace) / ".cursor-mcp-env"
        if cursor_path.exists():
            path = cursor_path
        else:
            return env
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    except OSError:
        pass
    return env


def _write_mcp_env(workspace: Path, token: str, port: int) -> None:
    mcp_dir = _memopilot_dir(workspace)
    mcp_dir.mkdir(parents=True, exist_ok=True)
    env_path = _mcp_env_path(workspace)
    content = (
        f"MEMOPILOT_TOKEN={token}\n"
        f"MEMOPILOT_PORT={port}\n"
        f"MEMOPILOT_WORKSPACE={workspace}\n"
    )
    env_path.write_text(content, encoding="utf-8")

    gitignore = mcp_dir / ".gitignore"
    try:
        existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
        if ".mcp-env" not in existing:
            sep = "" if existing.endswith("\n") or not existing else "\n"
            gitignore.write_text(existing + sep + ".mcp-env\n", encoding="utf-8")
    except OSError:
        pass


def _is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


async def _health_check(port: int, token: str) -> bool:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                f"http://127.0.0.1:{port}/v1/health",
                headers={"X-Agent-Token": token},
            )
            return resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


def _resolve_python() -> str:
    return sys.executable


def _resolve_agent_parent() -> str:
    this_file = Path(__file__).resolve()
    agent_pkg = this_file.parent
    return str(agent_pkg.parent)


async def _start_backend(workspace: Path) -> tuple[int, str]:
    """Start the backend process and return (port, token).

    Spawns `python -m agent.main` as a detached background process,
    then polls the lockfile until the port appears.
    """
    token = secrets.token_hex(32)
    agent_parent = _resolve_agent_parent()
    python = _resolve_python()

    _memopilot_dir(workspace).mkdir(parents=True, exist_ok=True)

    stale_lock = _lock_path(workspace)
    if stale_lock.exists():
        stale_lock.unlink(missing_ok=True)

    env = {**os.environ}
    env["MEMOPILOT_TOKEN"] = token
    env["MEMOPILOT_WORKSPACE"] = str(workspace)
    env["PYTHONPATH"] = agent_parent

    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS

    _log_stderr(f"Starting backend: {python} -m agent.main (cwd={agent_parent})")
    proc = subprocess.Popen(
        [python, "-m", "agent.main"],
        cwd=agent_parent,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
        start_new_session=True,
    )

    start = time.monotonic()
    while time.monotonic() - start < _BACKEND_STARTUP_TIMEOUT:
        if proc.poll() is not None:
            raise RuntimeError(f"Backend process exited immediately with code {proc.returncode}")

        lock = _read_lock(workspace)
        if lock and "port" in lock:
            port = int(lock["port"])
            if await _health_check(port, token):
                _write_mcp_env(workspace, token, port)
                _log_stderr(f"Backend started on port {port} (pid={proc.pid})")
                return port, token

        await asyncio.sleep(_HEALTH_CHECK_INTERVAL)

    proc.kill()
    raise RuntimeError(f"Backend failed to start within {_BACKEND_STARTUP_TIMEOUT}s")


async def _ensure_backend(workspace: Path) -> tuple[int, str]:
    """Return (port, token) for a running backend, starting one if needed."""

    port_env = os.environ.get("MEMOPILOT_PORT")
    token_env = os.environ.get("MEMOPILOT_TOKEN")
    if port_env and token_env:
        port = int(port_env)
        if await _health_check(port, token_env):
            return port, token_env

    mcp_env = _read_mcp_env(workspace)
    if mcp_env.get("MEMOPILOT_PORT") and mcp_env.get("MEMOPILOT_TOKEN"):
        port = int(mcp_env["MEMOPILOT_PORT"])
        token = mcp_env["MEMOPILOT_TOKEN"]
        if await _health_check(port, token):
            return port, token

    lock = _read_lock(workspace)
    if lock:
        port = int(lock["port"])
        pid = int(lock["pid"])
        if _is_process_alive(pid):
            if token_env and await _health_check(port, token_env):
                return port, token_env
            for candidate_token in [mcp_env.get("MEMOPILOT_TOKEN", ""), "memopilot-local"]:
                if candidate_token and await _health_check(port, candidate_token):
                    return port, candidate_token

    _log_stderr("No running backend found — starting one...")
    return await _start_backend(workspace)


def _log_stderr(msg: str) -> None:
    sys.stderr.write(f"[memopilot-mcp] {msg}\n")
    sys.stderr.flush()


class MCPServer:
    def __init__(self) -> None:
        self._request_id: int = 0
        self._renderer = ContextPackRenderer()
        self._port: int | None = None
        self._token: str | None = None
        self._workspace: Path = _resolve_workspace()

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

        if req_id is None:
            return None

        return self._error(req_id, f"Unknown method: {method}")

    async def _dispatch(self, tool_name: str, args: dict[str, Any]) -> str:
        workspace_override = args.pop("workspace", None)
        if workspace_override:
            self._set_workspace(Path(workspace_override))

        if tool_name == "memopilot-search":
            return await self._handle_memopilot_search(args)
        if tool_name == "memopilot-symbols":
            return await self._handle_memopilot_symbols(args)
        if tool_name == "memopilot-memory":
            return await self._handle_memopilot_memory(args)
        if tool_name == "memopilot-profile":
            return await self._handle_memopilot_profile()
        raise ValueError(f"Unknown tool: {tool_name}")

    def _set_workspace(self, workspace: Path) -> None:
        if workspace != self._workspace:
            self._workspace = workspace
            self._port = None
            self._token = None

    # ------------------------------------------------------------------
    # Backend connection — lazy init with auto-start
    # ------------------------------------------------------------------

    async def _ensure_connection(self) -> tuple[int, str]:
        if self._port and self._token:
            import httpx
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    resp = await client.get(f"http://127.0.0.1:{self._port}/v1/health")
                    if resp.status_code == 200:
                        return self._port, self._token
            except (httpx.ConnectError, httpx.TimeoutException):
                pass
            self._port = None
            self._token = None

        port, token = await _ensure_backend(self._workspace)
        self._port = port
        self._token = token
        return port, token

    async def _backend_request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        import httpx

        port, token = await self._ensure_connection()
        url = f"http://127.0.0.1:{port}{path}"
        headers = {"X-Agent-Token": token}

        async with httpx.AsyncClient(timeout=60.0) as client:
            if method == "GET":
                resp = await client.get(url, headers=headers)
            else:
                resp = await client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # Tool handlers
    # ------------------------------------------------------------------

    async def _handle_memopilot_search(self, args: dict[str, Any]) -> str:
        result = await self._backend_request(
            "POST",
            "/v1/context/assemble",
            {
                "task_description": args["query"],
                "files_in_focus": args.get("files_in_focus", []),
                "task_type_hint": args.get("task_type_hint", "general"),
                "caller": "mcp_tool",
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
            caller="mcp_tool",
            items=result.get("items", []),
            query=args["query"],
        )

    async def _handle_memopilot_profile(self) -> str:
        result = await self._backend_request("GET", "/v1/workspace/profile")
        profile_yaml = result.get("profile_yaml", "")
        if not profile_yaml:
            return "## MemoPilot Workspace Profile\n\nNo workspace profile is available.\n"
        return f"## MemoPilot Workspace Profile\n\n```yaml\n{profile_yaml.strip()}\n```"

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
