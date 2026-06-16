"""Local LLM discovery for Ollama and LM Studio.

Probes well-known local ports with a 2-second timeout.
Returns [] immediately when nothing is running — never raises.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx

_CONTEXT_TOKENS: dict[str, int] = {
    "llama3.1": 131072,
    "llama3.2": 131072,
    "qwen2.5-coder": 32768,
    "qwen2.5": 32768,
    "codellama": 16384,
    "deepseek-coder-v2": 163840,
    "phi4": 16384,
    "gemma3": 32768,
    "mistral": 32768,
    "default": 8192,
}

_TOOL_FAMILIES: frozenset[str] = frozenset({
    "llama3.1", "llama3.2", "qwen2.5", "mistral-nemo", "command-r", "firefunction",
})


@dataclass
class LocalModel:
    model_id: str
    name: str
    source: str          # 'ollama' | 'lmstudio'
    max_context_tokens: int
    supports_tools: bool
    cost_per_1m_input: float = 0.0
    cost_per_1m_output: float = 0.0


def _ctx_tokens(model_name: str) -> int:
    family = model_name.split(":")[0].lower()
    return _CONTEXT_TOKENS.get(model_name, _CONTEXT_TOKENS.get(family, _CONTEXT_TOKENS["default"]))


def _supports_tools(model_name: str) -> bool:
    family = model_name.split(":")[0].lower()
    return any(t in family for t in _TOOL_FAMILIES)


async def discover_ollama(base_url: str = "http://localhost:11434") -> list[LocalModel]:
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.get(f"{base_url}/api/tags")
            if r.status_code == 200:
                return [
                    LocalModel(
                        model_id=m["name"],
                        name=m["name"],
                        source="ollama",
                        max_context_tokens=_ctx_tokens(m["name"]),
                        supports_tools=_supports_tools(m["name"]),
                    )
                    for m in r.json().get("models", [])
                ]
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    return []


async def discover_lmstudio(base_url: str = "http://localhost:1234") -> list[LocalModel]:
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.get(f"{base_url}/v1/models")
            if r.status_code == 200:
                return [
                    LocalModel(
                        model_id=m["id"],
                        name=m.get("name", m["id"]),
                        source="lmstudio",
                        max_context_tokens=_ctx_tokens(m["id"]),
                        supports_tools=_supports_tools(m["id"]),
                    )
                    for m in r.json().get("data", [])
                ]
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    return []


async def discover_all_local(config: dict) -> list[LocalModel]:
    """Discover all locally running LLM servers.

    Runs Ollama and LM Studio probes concurrently with a 2-second timeout each.
    Returns combined list; never raises.
    """
    results = await asyncio.gather(
        discover_ollama(config.get("ollama_base_url", "http://localhost:11434")),
        discover_lmstudio(config.get("lmstudio_base_url", "http://localhost:1234")),
        return_exceptions=True,
    )
    models: list[LocalModel] = []
    for r in results:
        if isinstance(r, list):
            models.extend(r)
    return models
