"""Tests for config_loader.py."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from agent.config_loader import load_provider_config


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_default_values_when_no_files(tmp_path: Path):
    config = load_provider_config(str(tmp_path))
    assert config["provider"] == "host"
    assert "host" in config["fallback_order"]
    assert config["budget_profile"] == "cost_saver"
    assert "ollama_base_url" in config


def test_workspace_config_overrides_defaults(tmp_path: Path):
    memopilot_dir = tmp_path / ".memopilot"
    memopilot_dir.mkdir()
    (memopilot_dir / "config.yaml").write_text(
        yaml.dump({"provider": "anthropic", "anthropic_model": "claude-sonnet-4-6"}),
        encoding="utf-8",
    )
    config = load_provider_config(str(tmp_path))
    assert config["provider"] == "anthropic"
    assert config["anthropic_model"] == "claude-sonnet-4-6"
    # Defaults still present
    assert "fallback_order" in config


def test_env_vars_override_file_config(tmp_path: Path):
    memopilot_dir = tmp_path / ".memopilot"
    memopilot_dir.mkdir()
    (memopilot_dir / "config.yaml").write_text(
        yaml.dump({"provider": "ollama"}), encoding="utf-8"
    )
    with patch.dict(os.environ, {"MEMOPILOT_PROVIDER": "openai", "MEMOPILOT_OPENAI_KEY": "sk-test"}):
        config = load_provider_config(str(tmp_path))
    assert config["provider"] == "openai"
    assert config["openai_api_key"] == "sk-test"


def test_template_written_on_first_call(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    load_provider_config(str(ws))
    template_path = ws / ".memopilot" / "config.yaml"
    assert template_path.exists()
    content = template_path.read_text()
    assert "GITIGNORED" in content
    assert "anthropic_api_key" in content


def test_gitignore_updated_with_config_entry(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    load_provider_config(str(ws))
    gitignore = ws / ".gitignore"
    assert gitignore.exists()
    assert ".memopilot/config.yaml" in gitignore.read_text()


def test_gitignore_not_duplicated_on_second_call(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    load_provider_config(str(ws))
    load_provider_config(str(ws))
    gitignore = ws / ".gitignore"
    content = gitignore.read_text()
    assert content.count(".memopilot/config.yaml") == 1


def test_invalid_yaml_file_skipped_gracefully(tmp_path: Path):
    memopilot_dir = tmp_path / ".memopilot"
    memopilot_dir.mkdir()
    (memopilot_dir / "config.yaml").write_text(
        "invalid: yaml: [unclosed", encoding="utf-8"
    )
    config = load_provider_config(str(tmp_path))
    # Should not raise, should return defaults
    assert config["provider"] == "host"


def test_none_workspace_root_returns_defaults():
    config = load_provider_config(None)
    assert "provider" in config
    assert "fallback_order" in config
