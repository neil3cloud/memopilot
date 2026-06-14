"""MemoPilot MCP Server — Cursor Chat integration via stdio transport.

This is a standalone process that:
- Reads from stdin and writes to stdout (MCP stdio protocol)
- Communicates with the MemoPilot backend via HTTP
- Exposes 6 MemoPilot tools for Cursor Chat

Usage:
  python -m agent.mcp_server

Environment variables:
  MEMOPILOT_WORKSPACE — path to the workspace root
  MEMOPILOT_TOKEN     — authentication token for the backend
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any


def _get_httpx() -> Any:
    """Import httpx lazily so this module stays importable without optional deps."""
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError(
            "The 'httpx' package is required to run the MemoPilot MCP server. "
            "Install it with: pip install httpx"
        ) from exc
    return httpx


class MCPBackendClient:
    """HTTP client for the MemoPilot backend used by the MCP server."""

    def __init__(self) -> None:
        self.port = self._read_port()
        self.token = os.environ.get("MEMOPILOT_TOKEN", "")
        self.base_url = f"http://127.0.0.1:{self.port}/v1"
        self.headers = {
            "X-Agent-Token": self.token,
            "Content-Type": "application/json",
        }

    def _read_port(self) -> int:
        """Read the backend port from agent.lock."""
        workspace_root = _workspace_root(fallback_to_cwd=True)
        lock_path = Path(workspace_root) / ".memopilot" / "agent.lock"

        if not lock_path.exists():
            raise RuntimeError(
                "MemoPilot backend is not running. "
                f"agent.lock not found at {lock_path}. "
                "Open the workspace in VS Code with MemoPilot installed first."
            )

        try:
            data = json.loads(lock_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"MemoPilot agent.lock is invalid JSON: {lock_path}") from exc
        except OSError as exc:
            raise RuntimeError(f"Failed to read MemoPilot agent.lock: {lock_path}") from exc

        port = data.get("port")
        if not isinstance(port, int):
            raise RuntimeError(
                f"MemoPilot agent.lock is missing an integer 'port' field: {lock_path}"
            )
        return port

    def _build_url(self, path: str) -> str:
        normalized = path if path.startswith("/") else f"/{path}"
        if normalized == "/v1":
            normalized = ""
        elif normalized.startswith("/v1/"):
            normalized = normalized[3:]
        return f"{self.base_url}{normalized}"

    async def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        httpx = _get_httpx()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                self._build_url(path),
                json=payload,
                headers=self.headers,
            )
            response.raise_for_status()
            return response.json()

    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        httpx = _get_httpx()
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                self._build_url(path),
                params=params,
                headers=self.headers,
            )
            response.raise_for_status()
            return response.json()

    async def health_check(self) -> bool:
        """Verify the backend is reachable and healthy."""
        result = await self.get("/v1/health")
        return result.get("status") == "ok"


TOOL_DEFINITIONS = [
    {
        "name": "memopilot_context",
        "description": (
            "Retrieves a governed, rule-aware, secret-redacted context pack from "
            "MemoPilot local project memory. Returns relevant files, symbols, business "
            "rules, active project rules, and active skills for the current task. "
            "Call this before generating a patch or answering a coding question. "
            "Output is plain Markdown."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_description": {
                    "type": "string",
                    "description": "The task or question being answered.",
                },
                "files_in_focus": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File paths relative to workspace root. Optional.",
                },
                "task_type_hint": {
                    "type": "string",
                    "enum": [
                        "bug_fix",
                        "feature",
                        "test_generation",
                        "refactor",
                        "security_review",
                        "investigation",
                        "documentation",
                        "general",
                    ],
                    "description": "Optional task type hint.",
                },
            },
            "required": ["task_description"],
        },
    },
    {
        "name": "memopilot_rules",
        "description": (
            "Returns active project rules, global developer rules, and matched skills "
            "for the current workspace."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "memopilot_memory_search",
        "description": (
            "Searches MemoPilot local memory using hybrid FTS and vector search. "
            "Returns confirmed, non-stale memory items matching the query."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Symbol name, concept, or phrase to search for.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "memopilot_workspace_profile",
        "description": (
            "Returns the MemoPilot workspace profile: language, frameworks, "
            "test commands, lint commands, active rules, and memory health."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "memopilot_patch_review",
        "description": (
            "Reviews an already-applied patch by reading the current git diff. "
            "Returns risk level, compliance score, and recommended actions. "
            "Call this after applying a patch to check rule compliance."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "git_diff": {
                    "type": "string",
                    "description": (
                        "Optional: the diff text. If omitted, reads git diff automatically."
                    ),
                },
            },
        },
    },
    {
        "name": "memopilot_writeback",
        "description": (
            "IMPORTANT: Call this after successfully applying any patch that used "
            "memopilot_context. Provide outcome_summary and outcome_status. MemoPilot "
            "extracts memory proposals and queues them for developer review."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "outcome_summary": {
                    "type": "string",
                    "description": "Brief description of what was accomplished.",
                },
                "outcome_status": {
                    "type": "string",
                    "enum": ["success", "partial", "reverted"],
                    "description": "success | partial | reverted",
                },
                "context_pack_hash": {
                    "type": "string",
                    "description": "Optional. Hash from the preceding memopilot_context call.",
                },
                "git_diff": {
                    "type": "string",
                    "description": "Optional. If omitted, reads git diff automatically.",
                },
            },
            "required": ["outcome_summary", "outcome_status"],
        },
    },
]


def _workspace_root(*, fallback_to_cwd: bool = False) -> str:
    workspace = os.environ.get("MEMOPILOT_WORKSPACE")
    if workspace:
        return workspace
    if fallback_to_cwd:
        return os.getcwd()
    raise RuntimeError("MEMOPILOT_WORKSPACE environment variable is not set.")


def _code_fence_language(path: str) -> str:
    suffix = Path(path).suffix.lower().lstrip(".")
    return suffix or "text"


def _json_markdown(payload: Any) -> str:
    return f"```json\n{json.dumps(payload, indent=2, sort_keys=True)}\n```"


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2, sort_keys=True)


def _extract_changed_files(diff_text: str, *, limit: int = 20) -> list[str]:
    changed_files: list[str] = []
    for line in diff_text.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) >= 4 and parts[3].startswith("b/"):
            candidate = parts[3][2:]
            if candidate not in changed_files:
                changed_files.append(candidate)
        if len(changed_files) >= limit:
            break
    return changed_files


def _render_context_markdown(result: dict[str, Any]) -> str:
    lines = ["## MemoPilot Context", ""]

    context_pack_hash = result.get("context_pack_hash")
    if context_pack_hash:
        lines.extend([f"Context pack hash: `{context_pack_hash}`", ""])

    rules = result.get("rules") or []
    if rules:
        lines.append("### Active Rules")
        lines.extend(f"- {rule}" for rule in rules)
        lines.append("")

    skills = result.get("skills") or []
    if skills:
        lines.append("### Active Skills")
        lines.extend(f"- {skill}" for skill in skills)
        lines.append("")

    stale_exclusions = result.get("stale_exclusions") or {}
    if stale_exclusions.get("count"):
        lines.append("### Stale Exclusions")
        lines.append(
            f"- Excluded {stale_exclusions['count']} stale items from the context pack."
        )
        for module_name in stale_exclusions.get("affected_modules") or []:
            lines.append(f"- {module_name}")
        rebuild_command = stale_exclusions.get("rebuild_command")
        if rebuild_command:
            lines.append(f"- Rebuild command: `{rebuild_command}`")
        lines.append("")

    files = result.get("files") or []
    if not files:
        lines.append("No files were included in the generated context pack.")
        return "\n".join(lines)

    lines.append("### Files")
    lines.append("")
    for entry in files:
        path = entry.get("path", "unknown")
        tokens = entry.get("tokens", 0)
        lines.append(f"#### `{path}` ({tokens} tokens)")
        content = _normalize_text(entry.get("content"))
        if content:
            language = _code_fence_language(path)
            lines.append(f"```{language}")
            lines.append(content.rstrip())
            lines.append("```")
        lines.append("")

    return "\n".join(lines).strip()


def _render_rules_markdown(result: dict[str, Any]) -> str:
    lines = ["## MemoPilot Rules", ""]

    global_rules = result.get("global_rules") or []
    project_rules = result.get("project_rules") or []
    skills = result.get("detected_skills") or []

    lines.append("### Global Rules")
    if global_rules:
        lines.extend(f"- {item.get('text', '')}" for item in global_rules)
    else:
        lines.append("- None")
    lines.append("")

    lines.append("### Project Rules")
    if project_rules:
        lines.extend(f"- {item.get('text', '')}" for item in project_rules)
    else:
        lines.append("- None")
    lines.append("")

    lines.append("### Active Skills")
    if skills:
        for item in skills:
            framework = item.get("framework")
            label = item.get("name", "unknown")
            if framework:
                label = f"{label} ({framework})"
            lines.append(f"- {label}")
    else:
        lines.append("- None")

    return "\n".join(lines)


def _render_memory_search_markdown(result: dict[str, Any]) -> str:
    lines = ["## MemoPilot Memory Search", ""]
    items = result.get("items") or []

    if not items:
        lines.append("No matching memory items found.")
    else:
        for item in items:
            title = item.get("title") or "Untitled memory"
            memory_id = item.get("memory_id") or "unknown"
            lines.append(f"### {title}")
            lines.append(f"- Memory ID: `{memory_id}`")
            lines.append(f"- Trust level: {item.get('trust_level', 0)}")
            lines.append(f"- Status: {item.get('memory_status', 'unknown')}")
            lines.append(f"- Class: {item.get('memory_class', 'unknown')}")
            lines.append("")
            lines.append(item.get("body", ""))
            provenance = item.get("provenance") or []
            if provenance:
                lines.append("")
                lines.append("Provenance:")
                for entry in provenance:
                    source_ref = entry.get("source_ref", "unknown")
                    source_path = entry.get("source_path")
                    if source_path:
                        lines.append(f"- {source_ref} — {source_path}")
                    else:
                        lines.append(f"- {source_ref}")
            lines.append("")

    context_pack_hash = result.get("context_pack_hash")
    if context_pack_hash:
        lines.append(f"Context pack hash: `{context_pack_hash}`")

    return "\n".join(lines).strip()


def _render_workspace_profile_markdown(result: dict[str, Any]) -> str:
    profile_yaml = result.get("profile_yaml")
    if isinstance(profile_yaml, str) and profile_yaml.strip():
        return f"## MemoPilot Workspace Profile\n\n```yaml\n{profile_yaml.rstrip()}\n```"
    return "## MemoPilot Workspace Profile\n\nNo workspace profile is available yet."


def _render_writeback_markdown(result: dict[str, Any]) -> str:
    memory_item_id = result.get("memory_item_id")
    pending_approval = bool(result.get("pending_approval"))
    blocked_reason = result.get("blocked_reason")
    artifact_id = result.get("artifact_id")

    lines = ["## MemoPilot Writeback", ""]
    if blocked_reason:
        lines.append(f"Writeback was not recorded: {blocked_reason}")
    else:
        lines.append("Writeback recorded.")

    if memory_item_id:
        lines.append(f"- Memory item ID: `{memory_item_id}`")
    if artifact_id:
        lines.append(f"- Artifact ID: `{artifact_id}`")
    lines.append(f"- Pending approval: {'yes' if pending_approval else 'no'}")
    return "\n".join(lines)


def _build_writeback_body(args: dict[str, Any], git_diff: str) -> str:
    changed_files = _extract_changed_files(git_diff)
    diff_digest = hashlib.sha256(git_diff.encode("utf-8")).hexdigest() if git_diff else ""

    lines = [
        f"Outcome status: {args['outcome_status']}",
        "",
        args["outcome_summary"].strip(),
    ]

    context_pack_hash = args.get("context_pack_hash")
    if context_pack_hash:
        lines.extend(["", f"Context pack hash: {context_pack_hash}"])

    if diff_digest:
        lines.extend(["", f"Git diff SHA256: {diff_digest}"])

    if changed_files:
        lines.extend(["", "Changed files:"])
        lines.extend(f"- {path}" for path in changed_files)

    return "\n".join(lines).strip()


def _http_status_code(error: Exception) -> int | None:
    response = getattr(error, "response", None)
    return getattr(response, "status_code", None)


def _http_error_text(error: Exception) -> str:
    response = getattr(error, "response", None)
    if response is None:
        return str(error)
    try:
        detail = response.text
    except Exception:  # pragma: no cover - defensive
        detail = str(error)
    if detail:
        return detail
    return str(error)


async def _get_git_diff(workspace_root: str) -> str:
    """Read the current git diff from the workspace."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "--no-pager",
            "diff",
            "--no-ext-diff",
            "HEAD",
            cwd=workspace_root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        return stdout.decode("utf-8", errors="replace")
    except Exception:
        return ""


async def handle_context(backend: MCPBackendClient, args: dict[str, Any]) -> str:
    workspace_root = _workspace_root(fallback_to_cwd=True)
    result = await backend.post(
        "/v1/context-pack/generate",
        {
            "task_description": args["task_description"],
            "suggested_files": args.get("files_in_focus", []),
            "task_type": args.get("task_type_hint", "general"),
            "workspace_root": workspace_root,
            "caller": "cursor_mcp_tool",
            "output_format": "full",
            "max_output_tokens": 8000,
        },
    )
    return _render_context_markdown(result)


async def handle_rules(backend: MCPBackendClient) -> str:
    workspace_root = _workspace_root(fallback_to_cwd=True)
    result = await backend.get(
        "/v1/rules/active",
        {
            "workspace_root": workspace_root,
        },
    )
    return _render_rules_markdown(result)


async def handle_memory_search(backend: MCPBackendClient, args: dict[str, Any]) -> str:
    workspace_root = _workspace_root(fallback_to_cwd=True)
    result = await backend.post(
        "/v1/memory/recall",
        {
            "query": args["query"],
            "limit": args.get("limit", 10),
            "workspace_root": workspace_root,
            "caller": "cursor_mcp_tool",
            "output_format": "full",
            "max_output_tokens": 2000,
        },
    )
    return _render_memory_search_markdown(result)


async def handle_workspace_profile(backend: MCPBackendClient) -> str:
    result = await backend.get("/v1/workspace/profile")
    return _render_workspace_profile_markdown(result)


async def handle_patch_review(backend: MCPBackendClient, args: dict[str, Any]) -> str:
    workspace_root = _workspace_root(fallback_to_cwd=True)
    git_diff = args.get("git_diff") or await _get_git_diff(workspace_root)

    if not git_diff or not git_diff.strip():
        return "## MemoPilot Patch Review\n\nNo uncommitted changes detected. Apply a patch first."

    result = await backend.post(
        "/v1/task/review-applied-patch",
        {
            "git_diff": git_diff,
            "workspace_root": workspace_root,
            "caller": "cursor_mcp_tool",
        },
    )
    rendered_report = result.get("rendered_report")
    if isinstance(rendered_report, str) and rendered_report.strip():
        return rendered_report
    return _json_markdown(result)


async def handle_writeback(backend: MCPBackendClient, args: dict[str, Any]) -> str:
    workspace_root = _workspace_root(fallback_to_cwd=True)
    git_diff = args.get("git_diff") or await _get_git_diff(workspace_root)

    try:
        result = await backend.post(
            "/v1/tool-mode/writeback",
            {
                "outcome_summary": args["outcome_summary"],
                "outcome_status": args["outcome_status"],
                "context_pack_hash": args.get("context_pack_hash"),
                "git_diff": git_diff or None,
                "workspace_root": workspace_root,
                "caller": "cursor_mcp_tool",
            },
        )
        rendered_summary = result.get("rendered_summary")
        if isinstance(rendered_summary, str) and rendered_summary.strip():
            return rendered_summary
        if result.get("already_processed"):
            writeback_id = result.get("writeback_id", "unknown")
            return (
                "## MemoPilot Writeback — Already Processed\n\n"
                "This diff has already been recorded.\n\n"
                f"Writeback ID: {writeback_id}"
            )
        return _json_markdown(result)
    except Exception as exc:
        if _http_status_code(exc) != 404:
            raise

    result = await backend.post(
        "/v1/memory/writeback",
        {
            "title": f"Cursor MCP writeback ({args['outcome_status']})",
            "body": _build_writeback_body(args, git_diff),
            "source": "cursor_mcp_tool",
            "tags": {
                "caller": "cursor_mcp_tool",
                "outcome_status": args["outcome_status"],
                "context_pack_hash": args.get("context_pack_hash"),
            },
            "workspace_root": workspace_root,
        },
    )
    return _render_writeback_markdown(result)


TOOL_HANDLERS = {
    "memopilot_context": lambda backend, arguments: handle_context(backend, arguments),
    "memopilot_rules": lambda backend, arguments: handle_rules(backend),
    "memopilot_memory_search": lambda backend, arguments: handle_memory_search(
        backend, arguments
    ),
    "memopilot_workspace_profile": lambda backend, arguments: handle_workspace_profile(
        backend
    ),
    "memopilot_patch_review": lambda backend, arguments: handle_patch_review(
        backend, arguments
    ),
    "memopilot_writeback": lambda backend, arguments: handle_writeback(backend, arguments),
}


async def validate_environment() -> MCPBackendClient:
    """Validate environment before starting the MCP server."""
    workspace = os.environ.get("MEMOPILOT_WORKSPACE")
    if not workspace:
        print("Error: MEMOPILOT_WORKSPACE environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    lock_path = Path(workspace) / ".memopilot" / "agent.lock"
    if not lock_path.exists():
        print(
            "Error: MemoPilot backend is not running. "
            f"agent.lock not found at {lock_path}. "
            f"Open {workspace} in VS Code with MemoPilot installed and indexed.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not os.environ.get("MEMOPILOT_TOKEN"):
        print(
            "Error: MEMOPILOT_TOKEN is not set. "
            "This variable is set automatically when MemoPilot starts the backend.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        client = MCPBackendClient()
        healthy = await client.health_check()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(
            "Error: Failed to connect to MemoPilot backend: "
            f"{_http_error_text(exc)}",
            file=sys.stderr,
        )
        sys.exit(1)

    if not healthy:
        print(
            "Error: MemoPilot backend health check failed. "
            f"Backend at port {client.port} is not responding.",
            file=sys.stderr,
        )
        sys.exit(1)

    return client


async def run_mcp_server() -> None:
    """Run the MCP server using the mcp Python SDK."""
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool
    except ImportError:
        print(
            "Error: The 'mcp' package is not installed. Install it with: pip install mcp",
            file=sys.stderr,
        )
        sys.exit(1)

    httpx = _get_httpx()
    backend = await validate_environment()
    server = Server("memopilot")

    @server.list_tools()
    async def list_tools() -> ListToolsResult:
        tools = [
            Tool(
                name=tool_definition["name"],
                description=tool_definition["description"],
                inputSchema=tool_definition["inputSchema"],
            )
            for tool_definition in TOOL_DEFINITIONS
        ]
        return ListToolsResult(tools=tools)

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
        nonlocal backend

        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Unknown tool: {name}")],
                isError=True,
            )

        try:
            result_text = await handler(backend, arguments or {})
            return CallToolResult(content=[TextContent(type="text", text=result_text)])
        except httpx.ConnectError:
            try:
                backend = MCPBackendClient()
                result_text = await handler(backend, arguments or {})
                return CallToolResult(content=[TextContent(type="text", text=result_text)])
            except Exception as retry_error:
                return CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=(
                                "MemoPilot backend is unreachable: "
                                f"{_http_error_text(retry_error)}"
                            ),
                        )
                    ],
                    isError=True,
                )
        except httpx.HTTPStatusError as exc:
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=(
                            "MemoPilot backend error "
                            f"(HTTP {exc.response.status_code}): {_http_error_text(exc)}"
                        ),
                    )
                ],
                isError=True,
            )
        except Exception as exc:
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=f"MemoPilot error: {_http_error_text(exc)}",
                    )
                ],
                isError=True,
            )

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    """Entry point for the MCP server."""
    asyncio.run(run_mcp_server())


if __name__ == "__main__":
    main()
