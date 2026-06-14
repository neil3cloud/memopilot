"""Configuration loader for MemoPilot agent.

Loads settings from:
  1. ~/.memopilot/settings.yaml (global defaults)
  2. <workspace>/.memopilot/settings.yaml (workspace overrides)

Workspace settings override global settings (shallow merge).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Config:
    """Resolved MemoPilot configuration."""

    workspace_path: Path
    memopilot_dir: Path
    global_dir: Path
    log_level: str = "info"
    api_version: int = 1
    schema_version: int = 15
    monthly_budget_usd: float = 20.0
    budget_profile: str = "balanced"
    validation_default_timeout: int = 60
    validation_max_timeout: int = 300
    mcp_cap_pre_fetch: int = 8
    mcp_cap_patch_generation: int = 5
    mcp_cap_investigation: int = 12
    mcp_hard_absolute_cap: int = 20

    # Derived paths
    db_path: Path = field(init=False)
    logs_dir: Path = field(init=False)
    rules_dir: Path = field(init=False)
    context_packs_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        self.db_path = self.memopilot_dir / "memory" / "memopilot.db"
        self.logs_dir = self.memopilot_dir / "logs"
        self.rules_dir = self.memopilot_dir / "rules"
        self.context_packs_dir = self.memopilot_dir / "context-packs"


def load_config() -> Config:
    """Load and merge configuration from global and workspace sources."""
    workspace_path = Path(os.environ.get("MEMOPILOT_WORKSPACE", os.getcwd()))
    global_dir = Path.home() / ".memopilot"
    memopilot_dir = workspace_path / ".memopilot"

    # Load global settings
    global_settings: dict = {}
    global_settings_path = global_dir / "settings.yaml"
    if global_settings_path.exists():
        with open(global_settings_path) as f:
            global_settings = yaml.safe_load(f) or {}

    # Load workspace settings (overrides global)
    workspace_settings: dict = {}
    workspace_settings_path = memopilot_dir / "settings.yaml"
    if workspace_settings_path.exists():
        with open(workspace_settings_path) as f:
            workspace_settings = yaml.safe_load(f) or {}

    # Merge: workspace wins
    merged = {**global_settings, **workspace_settings}
    budget_settings = merged.get("budget", {})
    if not isinstance(budget_settings, dict):
        budget_settings = {}

    validation_settings = merged.get("validation", {})
    if not isinstance(validation_settings, dict):
        validation_settings = {}

    mcp_settings = merged.get("mcp", {})
    if not isinstance(mcp_settings, dict):
        mcp_settings = {}

    iteration_caps = mcp_settings.get("iteration_caps", {})
    if not isinstance(iteration_caps, dict):
        iteration_caps = {}

    return Config(
        workspace_path=workspace_path,
        memopilot_dir=memopilot_dir,
        global_dir=global_dir,
        log_level=merged.get("log_level", "info"),
        monthly_budget_usd=float(budget_settings.get("monthly_budget_usd", 20.0)),
        budget_profile=str(budget_settings.get("profile", "balanced")),
        validation_default_timeout=int(validation_settings.get("default_timeout", 60)),
        validation_max_timeout=int(validation_settings.get("max_timeout", 300)),
        mcp_cap_pre_fetch=int(iteration_caps.get("pre_fetch", 8)),
        mcp_cap_patch_generation=int(iteration_caps.get("patch_generation", 5)),
        mcp_cap_investigation=int(iteration_caps.get("investigation", 12)),
        mcp_hard_absolute_cap=int(iteration_caps.get("hard_absolute_cap", 20)),
    )
