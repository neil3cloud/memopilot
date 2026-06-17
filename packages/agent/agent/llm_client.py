"""LLM provider client implementations.

Provides a uniform async interface across Anthropic, OpenAI, Ollama, and LM Studio.
All clients accept system + user prompt and return LLMResponse or stream StreamChunk.

Usage:
    client = build_client('anthropic', config)
    response = await client.complete(system_prompt, user_prompt)
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx


@dataclass
class LLMResponse:
    content: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    model_id: str
    provider: str


@dataclass
class StreamChunk:
    token: str
    is_final: bool = False


class BaseLLMClient(ABC):
    @abstractmethod
    async def complete(self, system: str, user: str, max_tokens: int = 4096) -> LLMResponse: ...

    @abstractmethod
    async def stream(self, system: str, user: str, max_tokens: int = 4096) -> AsyncIterator[StreamChunk]: ...

    @property
    @abstractmethod
    def model_id(self) -> str: ...

    @property
    @abstractmethod
    def provider_name(self) -> str: ...


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

class AnthropicClient(BaseLLMClient):
    _PRICING: dict[str, tuple[float, float]] = {
        "claude-haiku-4-5": (0.80, 4.00),
        "claude-sonnet-4-6": (3.00, 15.00),
        "claude-opus-4-7": (15.00, 75.00),
    }

    def __init__(self, api_key: str, model: str = "claude-haiku-4-5") -> None:
        self._key = api_key
        self._model = model

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def provider_name(self) -> str:
        return "anthropic"

    def _headers(self) -> dict:
        return {
            "x-api-key": self._key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def _body(self, system: str, user: str, max_tokens: int, stream: bool = False) -> dict:
        return {
            "model": self._model,
            "max_tokens": max_tokens,
            "stream": stream,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }

    def _cost(self, input_tokens: int, output_tokens: int) -> float:
        pi, po = self._PRICING.get(self._model, (3.00, 15.00))
        return input_tokens / 1e6 * pi + output_tokens / 1e6 * po

    async def complete(self, system: str, user: str, max_tokens: int = 4096) -> LLMResponse:
        async with httpx.AsyncClient(timeout=120.0) as c:
            r = await c.post(
                "https://api.anthropic.com/v1/messages",
                headers=self._headers(),
                json=self._body(system, user, max_tokens),
            )
            r.raise_for_status()
            d = r.json()
        i = d["usage"]["input_tokens"]
        o = d["usage"]["output_tokens"]
        return LLMResponse(d["content"][0]["text"], i, o, self._cost(i, o), self._model, "anthropic")

    async def stream(self, system: str, user: str, max_tokens: int = 4096) -> AsyncIterator[StreamChunk]:
        async with httpx.AsyncClient(timeout=120.0) as c:
            async with c.stream(
                "POST",
                "https://api.anthropic.com/v1/messages",
                headers=self._headers(),
                json=self._body(system, user, max_tokens, stream=True),
            ) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    p = json.loads(line[6:])
                    if p.get("type") == "content_block_delta":
                        t = p["delta"].get("text", "")
                        if t:
                            yield StreamChunk(token=t)
                    elif p.get("type") == "message_stop":
                        yield StreamChunk(token="", is_final=True)


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

class OpenAIClient(BaseLLMClient):
    _PRICING: dict[str, tuple[float, float]] = {
        "gpt-4o": (2.50, 10.00),
        "gpt-4o-mini": (0.15, 0.60),
        "o3": (10.00, 40.00),
        "gpt-4.1-nano": (0.10, 0.40),
    }

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1/chat/completions",
    ) -> None:
        self._key = api_key
        self._model = model
        self._url = base_url

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def provider_name(self) -> str:
        return "openai"

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }

    def _messages(self, system: str, user: str) -> list:
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def _cost(self, input_tokens: int, output_tokens: int) -> float:
        pi, po = self._PRICING.get(self._model, (2.50, 10.00))
        return input_tokens / 1e6 * pi + output_tokens / 1e6 * po

    async def complete(self, system: str, user: str, max_tokens: int = 4096) -> LLMResponse:
        async with httpx.AsyncClient(timeout=120.0) as c:
            r = await c.post(
                self._url,
                headers=self._headers(),
                json={"model": self._model, "max_tokens": max_tokens, "messages": self._messages(system, user)},
            )
            r.raise_for_status()
            d = r.json()
        i = d["usage"]["prompt_tokens"]
        o = d["usage"]["completion_tokens"]
        return LLMResponse(
            d["choices"][0]["message"]["content"], i, o, self._cost(i, o), self._model, self.provider_name
        )

    async def stream(self, system: str, user: str, max_tokens: int = 4096) -> AsyncIterator[StreamChunk]:
        async with httpx.AsyncClient(timeout=120.0) as c:
            async with c.stream(
                "POST",
                self._url,
                headers=self._headers(),
                json={
                    "model": self._model,
                    "max_tokens": max_tokens,
                    "stream": True,
                    "messages": self._messages(system, user),
                },
            ) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:].strip()
                    if raw == "[DONE]":
                        yield StreamChunk(token="", is_final=True)
                        return
                    t = json.loads(raw)["choices"][0].get("delta", {}).get("content", "")
                    if t:
                        yield StreamChunk(token=t)


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

class OllamaClient(BaseLLMClient):
    def __init__(
        self,
        model: str = "llama3.1",
        base_url: str = "http://localhost:11434",
    ) -> None:
        self._model = model
        self._url = base_url.rstrip("/")

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def provider_name(self) -> str:
        return "ollama"

    async def complete(self, system: str, user: str, max_tokens: int = 4096) -> LLMResponse:
        async with httpx.AsyncClient(timeout=300.0) as c:
            r = await c.post(
                f"{self._url}/api/chat",
                json={
                    "model": self._model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            r.raise_for_status()
        return LLMResponse(r.json()["message"]["content"], 0, 0, 0.0, self._model, "ollama")

    async def stream(self, system: str, user: str, max_tokens: int = 4096) -> AsyncIterator[StreamChunk]:
        async with httpx.AsyncClient(timeout=300.0) as c:
            async with c.stream(
                "POST",
                f"{self._url}/api/chat",
                json={
                    "model": self._model,
                    "stream": True,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            ) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    p = json.loads(line)
                    t = p.get("message", {}).get("content", "")
                    if t:
                        yield StreamChunk(token=t)
                    if p.get("done"):
                        yield StreamChunk(token="", is_final=True)
                        return


# ---------------------------------------------------------------------------
# LM Studio (OpenAI-compatible, no key required)
# ---------------------------------------------------------------------------

class LMStudioClient(OpenAIClient):
    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:1234",
    ) -> None:
        super().__init__(
            api_key="lm-studio",
            model=model,
            base_url=f"{base_url.rstrip('/')}/v1/chat/completions",
        )

    @property
    def provider_name(self) -> str:
        return "lmstudio"

    def _cost(self, input_tokens: int, output_tokens: int) -> float:
        return 0.0


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_client(provider: str, config: dict) -> BaseLLMClient:
    """Build a provider client from a config dict produced by load_provider_config()."""
    if provider == "anthropic":
        key = config.get("anthropic_api_key")
        if not key:
            raise ValueError("anthropic_api_key not set in config")
        return AnthropicClient(key, config.get("anthropic_model", "claude-haiku-4-5"))

    if provider == "openai":
        key = config.get("openai_api_key")
        if not key:
            raise ValueError("openai_api_key not set in config")
        return OpenAIClient(key, config.get("openai_model", "gpt-4o-mini"))

    if provider == "ollama":
        return OllamaClient(
            config.get("ollama_model", "llama3.1"),
            config.get("ollama_base_url", "http://localhost:11434"),
        )

    if provider == "lmstudio":
        model = config.get("lmstudio_model", "")
        if not model:
            raise ValueError("lmstudio_model not set in config")
        return LMStudioClient(model, config.get("lmstudio_base_url", "http://localhost:1234"))

    raise ValueError(f"Unknown provider: {provider}")
