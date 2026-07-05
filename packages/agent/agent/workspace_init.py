"""Workspace and global directory initializer for MemoPilot.

Ensures ~/.memopilot/ global directory exists with stub configuration files.
Called by the extension on first activation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

GLOBAL_RULES_STUB = """\
# MemoPilot Global Rules
# These rules apply to all workspaces unless overridden by project rules.
# Rule precedence: safety > task instruction > project rules > global rules > inferred > AI

rules: []
#  - id: global-001
#    text: "Always generate tests for new functions."
#    priority: 50
"""

GLOBAL_SETTINGS_STUB = """\
# MemoPilot Global Settings
# Workspace settings (.memopilot/settings.yaml) override these.

log_level: info

# model_routing:
#   default_tier: cheap_cloud
#   allow_frontier: true
#   frontier_requires_approval: true

# budget:
#   monthly_budget_usd: 20
#
# mcp:
#   iteration_caps:
#     pre_fetch: 8
#     patch_generation: 5
#     investigation: 12
#     hard_absolute_cap: 20
"""

GLOBAL_PROVIDERS_STUB = """\
# MemoPilot Model Providers
# Configure AI model providers here.
# Workspace overrides: .memopilot/providers.override.yaml

providers: []
#  - id: ollama-local
#    type: ollama
#    base_url: http://localhost:11434
#    models:
#      - qwen2.5-coder:7b

#  - id: openai
#    type: openai
#    api_key_env: OPENAI_API_KEY
#    models:
#      - gpt-4o-mini
#      - gpt-4o
"""

_MARKER_START = "<!-- MemoPilot managed block: start -->"
_MARKER_END = "<!-- MemoPilot managed block: end -->"


def ensure_global_config(global_dir: Path) -> None:
    """Ensure ~/.memopilot/ exists with default stub files.

    Does not overwrite existing files — only creates missing ones.
    """
    global_dir.mkdir(parents=True, exist_ok=True)
    (global_dir / "skills").mkdir(exist_ok=True)
    (global_dir / "context-templates").mkdir(exist_ok=True)

    stubs = {
        "global.rules.yaml": GLOBAL_RULES_STUB,
        "settings.yaml": GLOBAL_SETTINGS_STUB,
        "providers.yaml": GLOBAL_PROVIDERS_STUB,
    }

    for filename, content in stubs.items():
        file_path = global_dir / filename
        if not file_path.exists():
            file_path.write_text(content, encoding="utf-8")


def generate_workspace_bootstrap(
    *,
    workspace_path: Path,
    memopilot_dir: Path,
    profile: dict,
) -> None:
    """Generate editor integration files for retrieval-first MemoPilot usage."""
    workspace = profile.get("workspace", {}) if isinstance(profile, dict) else {}
    frameworks = workspace.get("frameworks", []) if isinstance(workspace, dict) else []
    primary_language = workspace.get("primary_language", "unknown") if isinstance(workspace, dict) else "unknown"
    workspace_name = workspace.get("name", workspace_path.name) if isinstance(workspace, dict) else workspace_path.name

    retrieval_first_body = _render_retrieval_first_instructions(
        workspace_name=workspace_name,
        primary_language=str(primary_language),
        frameworks=[str(item) for item in frameworks if isinstance(item, str)],
    )

    _write_vscode_mcp_json(workspace_path)
    _upsert_managed_markdown(workspace_path / ".github" / "copilot-instructions.md", retrieval_first_body)
    _upsert_managed_markdown(workspace_path / "CLAUDE.md", retrieval_first_body)
    _upsert_managed_markdown(workspace_path / "GEMINI.md", retrieval_first_body)
    _upsert_managed_markdown(
        workspace_path / ".cursor" / "rules" / "memopilot.mdc",
        _render_cursor_rule(
            workspace_name=workspace_name,
            primary_language=str(primary_language),
            frameworks=[str(item) for item in frameworks if isinstance(item, str)],
        ),
    )
    _ensure_memopilot_gitignore(memopilot_dir)


def _write_vscode_mcp_json(workspace_path: Path) -> None:
    mcp_path = workspace_path / ".vscode" / "mcp.json"
    mcp_path.parent.mkdir(parents=True, exist_ok=True)

    agent_parent = str(Path(__file__).resolve().parent.parent)
    server_config = {
        "type": "stdio",
        "command": sys.executable,
        "args": ["-m", "agent.mcp_server"],
        "cwd": "${workspaceFolder}",
        "env": {
            "PYTHONPATH": agent_parent,
        },
    }

    document: dict[str, object] = {"servers": {}}
    if mcp_path.exists():
        try:
            existing = json.loads(mcp_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                document = existing
        except json.JSONDecodeError:
            document = {"servers": {}}

    servers = document.get("servers")
    if not isinstance(servers, dict):
        servers = {}
    servers["memopilot"] = server_config
    document["servers"] = servers
    mcp_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _upsert_managed_markdown(path: Path, managed_body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    managed_block = f"{_MARKER_START}\n{managed_body.rstrip()}\n{_MARKER_END}\n"

    if not path.exists():
        path.write_text(managed_block, encoding="utf-8")
        return

    content = path.read_text(encoding="utf-8")
    if _MARKER_START in content and _MARKER_END in content:
        start = content.index(_MARKER_START)
        end = content.index(_MARKER_END) + len(_MARKER_END)
        # Keep the managed block replacement idempotent: collapse any leading
        # blank lines after the managed block boundary so repeated bootstrap
        # runs do not append one extra trailing blank line each time.
        tail = content[end:]
        tail = tail.lstrip("\r\n")
        updated = content[:start] + managed_block + tail
    else:
        separator = "\n\n" if content.strip() else ""
        updated = content.rstrip() + separator + managed_block
    if updated != content:
        path.write_text(updated, encoding="utf-8")


def _render_retrieval_first_instructions(
    *,
    workspace_name: str,
    primary_language: str,
    frameworks: list[str],
) -> str:
    """Shared managed-block body for plain-markdown instruction files (Copilot, Claude Code, Gemini CLI)."""
    framework_line = ", ".join(frameworks) if frameworks else "none detected"
    return f"""# MemoPilot Retrieval-First Instructions

Workspace: {workspace_name}
Primary language: {primary_language}
Detected frameworks: {framework_line}

Use MemoPilot as the primary source of workspace context before answering codebase questions.

Required tool order for codebase questions:
1. Call `memopilot-search` first to assemble bounded workspace context.
2. Call `memopilot-symbols` when you need exact or partial symbol lookup.
3. Call `memopilot-memory` when you need project facts, conventions, or prior decisions.
4. Call `memopilot-profile` when framework, language, or workspace-wide policy is relevant.

Behavioral rules:
- Prefer MemoPilot-retrieved context over broad repository guessing.
- Do not assume MemoPilot applies patches or owns file mutation in default mode.
- If MemoPilot context is insufficient, say what is missing instead of inventing details.
"""


def _render_cursor_rule(
    *,
    workspace_name: str,
    primary_language: str,
    frameworks: list[str],
) -> str:
    framework_line = ", ".join(frameworks) if frameworks else "none detected"
    return f"""---
alwaysApply: true
---

# MemoPilot Retrieval-First Rule

Workspace: {workspace_name}
Primary language: {primary_language}
Detected frameworks: {framework_line}

Before answering repository questions, call MemoPilot MCP tools first.

Preferred tool order:
1. `memopilot-search` for bounded code context.
2. `memopilot-symbols` for exact or partial symbol lookup.
3. `memopilot-memory` for durable project memory.
4. `memopilot-profile` for workspace-level constraints.

Do not treat MemoPilot as a patch executor in default mode. Use it as the primary context source.
"""


def _ensure_memopilot_gitignore(memopilot_dir: Path) -> None:
    gitignore_path = memopilot_dir / ".gitignore"
    entries = [".cursor-mcp-env"]
    existing = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""
    lines = existing.splitlines()
    changed = False
    for entry in entries:
        if entry not in lines:
            lines.append(entry)
            changed = True
    if changed or not gitignore_path.exists():
        gitignore_path.write_text("\n".join(line for line in lines if line).rstrip() + "\n", encoding="utf-8")
