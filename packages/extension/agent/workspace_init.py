"""Workspace and global directory initializer for MemoPilot.

Ensures ~/.memopilot/ global directory exists with stub configuration files.
Called by the extension on first activation.
"""

from __future__ import annotations

from pathlib import Path

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
