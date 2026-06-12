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
    schema_version: int = 1

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

    return Config(
        workspace_path=workspace_path,
        memopilot_dir=memopilot_dir,
        global_dir=global_dir,
        log_level=merged.get("log_level", "info"),
    )
