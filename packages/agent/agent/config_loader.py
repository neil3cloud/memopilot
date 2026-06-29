"""Provider configuration loader.

Reads provider/API-key config separately from the main Config dataclass.
Sources (later overrides earlier):
  1. ~/.memopilot/config.yaml
  2. <workspace>/.memopilot/config.yaml
  3. Environment variables

Returns a plain dict. Does NOT contain workspace-structural config.
Auto-writes a commented template and adds it to .gitignore on first call.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

_ENV_MAP = {
    "MEMOPILOT_PROVIDER": "provider",
    "MEMOPILOT_ANTHROPIC_KEY": "anthropic_api_key",
    "MEMOPILOT_OPENAI_KEY": "openai_api_key",
    "MEMOPILOT_ANTHROPIC_MODEL": "anthropic_model",
    "MEMOPILOT_OPENAI_MODEL": "openai_model",
    "MEMOPILOT_OLLAMA_MODEL": "ollama_model",
    "MEMOPILOT_OLLAMA_URL": "ollama_base_url",
    "MEMOPILOT_LMSTUDIO_URL": "lmstudio_base_url",
    "MEMOPILOT_LOCAL_URL": "local_url",
    "MEMOPILOT_LOCAL_MODEL": "local_model",
    "MEMOPILOT_BUDGET_PROFILE": "budget_profile",
}

_TEMPLATE = """\
# .memopilot/config.yaml — GITIGNORED — do not commit API keys

provider: local          # local | anthropic | openai | host
budget_profile: cost_saver   # cost_saver | balanced | strict_local | max_accuracy

# Local AI — any OpenAI-compatible server (Ollama, LM Studio, vLLM, OpenVINO, llama.cpp, etc.)
local_url: http://localhost:1234
local_model: qwen2.5-coder-7b-instruct

# Cloud API keys (uncomment to enable)
# anthropic_api_key: sk-ant-...
# anthropic_model: claude-haiku-4-5
# openai_api_key: sk-...
# openai_model: gpt-4o-mini
"""

_DEFAULTS: dict = {
    "provider": "host",
    "budget_profile": "cost_saver",
    "local_url": "http://localhost:1234",
}


def load_provider_config(workspace_root: str | None = None) -> dict:
    """Load and merge provider configuration from all sources.

    Never raises — missing files and invalid YAML are silently skipped.
    API keys are not logged anywhere in this module.
    """
    config = dict(_DEFAULTS)

    paths = [Path.home() / ".memopilot" / "config.yaml"]
    if workspace_root:
        ws_config = Path(workspace_root) / ".memopilot" / "config.yaml"
        paths.append(ws_config)
        _ensure_template_and_gitignore(ws_config.parent)

    for path in paths:
        if path.exists():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                if isinstance(data, dict):
                    config.update(data)
            except Exception:
                pass

    for env_key, config_key in _ENV_MAP.items():
        value = os.environ.get(env_key)
        if value:
            # Never log API key values
            config[config_key] = value

    return config


def _ensure_template_and_gitignore(memopilot_dir: Path) -> None:
    """Write template config if absent and ensure config.yaml is gitignored."""
    try:
        memopilot_dir.mkdir(parents=True, exist_ok=True)
        config_path = memopilot_dir / "config.yaml"
        if not config_path.exists():
            config_path.write_text(_TEMPLATE, encoding="utf-8")

        gitignore = memopilot_dir.parent / ".gitignore"
        entry = ".memopilot/config.yaml"
        if gitignore.exists():
            content = gitignore.read_text(encoding="utf-8")
            if entry not in content:
                sep = "\n" if content.endswith("\n") else "\n\n"
                gitignore.write_text(content + sep + entry + "\n", encoding="utf-8")
        else:
            gitignore.write_text(entry + "\n", encoding="utf-8")
    except OSError:
        pass
