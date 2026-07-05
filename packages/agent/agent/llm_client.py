"""LLM provider client implementations.

Provides a uniform async interface across Anthropic, OpenAI, Google (Gemini), Ollama, and LM Studio.
All clients accept system + user prompt and return LLMResponse or stream StreamChunk.

Usage:
    client = build_client('anthropic', config)
    response = await client.complete(system_prompt, user_prompt)
"""

from __future__ import annotations

import asyncio
import email.utils
import json
import logging
import random
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx


logger = logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_RETRYABLE_TRANSPORT_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.PoolTimeout,
)
_LOCAL_PROVIDERS = {"local", "ollama", "lmstudio"}

_TRUE_STRINGS = {"true", "1", "yes", "on"}
_FALSE_STRINGS = {"false", "0", "no", "off"}


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


def _parse_retry_after(header_value: str | None, max_delay: float) -> float | None:
    """Parse Retry-After header into seconds, or return None when invalid."""
    if not header_value:
        return None
    try:
        seconds = float(header_value)
        if seconds < 0:
            return None
        return min(seconds, max_delay)
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(header_value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        seconds = (dt - datetime.now(timezone.utc)).total_seconds()
        if seconds < 0:
            return None
        return min(seconds, max_delay)
    except (TypeError, ValueError):
        return None


def _compute_delay(attempt: int, base_delay: float, max_delay: float, retry_after: float | None) -> float:
    """Compute jittered backoff delay where attempt is 1-indexed."""
    raw = retry_after if retry_after is not None else base_delay * (2 ** (attempt - 1))
    capped = min(raw, max_delay)
    return capped / 2 + random.uniform(0, capped / 2)


def _coerce_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUE_STRINGS:
            return True
        if normalized in _FALSE_STRINGS:
            return False
        logger.warning("Invalid boolean config value %r; using default %s", value, default)
        return default
    return default


def _coerce_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("Invalid integer config value %r; using default %s", value, default)
        return default


def _coerce_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        logger.warning("Invalid float config value %r; using default %s", value, default)
        return default


async def _safe_aclose(agen: AsyncIterator[StreamChunk]) -> None:
    """Best-effort close for abandoned async generators during retries."""
    aclose = getattr(agen, "aclose", None)
    if aclose is None:
        return
    try:
        await aclose()
    except Exception:
        logger.debug("Ignoring stream close error during retry cleanup", exc_info=True)


class RetryingLLMClient(BaseLLMClient):
    """Wrapper adding retry/backoff behavior to cloud provider clients."""

    def __init__(self, inner: BaseLLMClient, *, max_attempts: int, base_delay: float, max_delay: float) -> None:
        self._inner = inner
        self._max_attempts = max_attempts
        self._base_delay = base_delay
        self._max_delay = max_delay

    @property
    def model_id(self) -> str:
        return self._inner.model_id

    @property
    def provider_name(self) -> str:
        return self._inner.provider_name

    async def complete(self, system: str, user: str, max_tokens: int = 4096) -> LLMResponse:
        attempt = 0
        while True:
            attempt += 1
            try:
                return await self._inner.complete(system, user, max_tokens)
            except httpx.HTTPStatusError as exc:
                retryable = exc.response.status_code in _RETRYABLE_STATUS_CODES
                retry_after = _parse_retry_after(exc.response.headers.get("Retry-After"), self._max_delay)
                label = f"HTTP {exc.response.status_code}"
                caught_exc: Exception = exc
            except _RETRYABLE_TRANSPORT_EXCEPTIONS as exc:
                retryable = True
                retry_after = None
                label = type(exc).__name__
                caught_exc = exc
            if not retryable or attempt >= self._max_attempts:
                raise caught_exc
            delay = _compute_delay(attempt, self._base_delay, self._max_delay, retry_after)
            logger.warning(
                "%s: retrying after %s (attempt %d/%d, sleeping %.2fs)",
                self.provider_name,
                label,
                attempt,
                self._max_attempts,
                delay,
            )
            await asyncio.sleep(delay)

    async def stream(self, system: str, user: str, max_tokens: int = 4096) -> AsyncIterator[StreamChunk]:
        attempt = 0
        while True:
            attempt += 1
            agen = self._inner.stream(system, user, max_tokens)
            try:
                first_chunk = await agen.__anext__()
            except StopAsyncIteration:
                return
            except httpx.HTTPStatusError as exc:
                retryable = exc.response.status_code in _RETRYABLE_STATUS_CODES
                retry_after = _parse_retry_after(exc.response.headers.get("Retry-After"), self._max_delay)
                label = f"HTTP {exc.response.status_code}"
                await _safe_aclose(agen)
                caught_exc: Exception = exc
            except _RETRYABLE_TRANSPORT_EXCEPTIONS as exc:
                retryable = True
                retry_after = None
                label = type(exc).__name__
                await _safe_aclose(agen)
                caught_exc = exc
            else:
                yield first_chunk
                async for chunk in agen:
                    yield chunk
                return
            if not retryable or attempt >= self._max_attempts:
                raise caught_exc
            delay = _compute_delay(attempt, self._base_delay, self._max_delay, retry_after)
            logger.warning(
                "%s: retrying stream after %s (attempt %d/%d, sleeping %.2fs)",
                self.provider_name,
                label,
                attempt,
                self._max_attempts,
                delay,
            )
            await asyncio.sleep(delay)


def _wrap_with_retry(client: BaseLLMClient, provider: str, config: dict) -> BaseLLMClient:
    normalized_provider = (provider or "").strip().lower()
    if normalized_provider in _LOCAL_PROVIDERS:
        return client

    def _get(key: str, default: object) -> object:
        provider_key = f"{normalized_provider}_{key}"
        if provider_key in config:
            return config[provider_key]
        if key in config:
            return config[key]
        return default

    enabled = _coerce_bool(_get("retry_enabled", True), default=True)
    if not enabled:
        return client

    max_attempts = _coerce_int(_get("retry_max_attempts", 3), default=3)
    if max_attempts < 1:
        logger.warning("retry_max_attempts=%s is invalid (must be >=1); clamping to 1", max_attempts)
        max_attempts = 1

    base_delay = _coerce_float(_get("retry_base_delay_seconds", 1.0), default=1.0)
    if base_delay < 0:
        logger.warning("retry_base_delay_seconds=%s is invalid (must be >=0); clamping to 0", base_delay)
        base_delay = 0.0

    max_delay = _coerce_float(_get("retry_max_delay_seconds", 60.0), default=60.0)
    if max_delay < 0:
        logger.warning("retry_max_delay_seconds=%s is invalid (must be >=0); clamping to 0", max_delay)
        max_delay = 0.0
    if max_delay < base_delay:
        logger.warning(
            "retry_max_delay_seconds=%s is less than retry_base_delay_seconds=%s; clamping max to base",
            max_delay,
            base_delay,
        )
        max_delay = base_delay

    return RetryingLLMClient(
        client,
        max_attempts=max_attempts,
        base_delay=base_delay,
        max_delay=max_delay,
    )


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
# OpenRouter (OpenAI-compatible aggregator — many free-tier models)
# ---------------------------------------------------------------------------

class OpenRouterClient(OpenAIClient):
    """OpenRouter speaks the OpenAI chat-completions schema, so this just points
    OpenAIClient at OpenRouter's endpoint and adds its recommended attribution headers.

    Per-model pricing varies widely (and free-tier models — model ids ending in
    ':free' — cost nothing), so unlike OpenAIClient this always reports cost_usd=0.0
    rather than guessing from an unrelated OpenAI price table.
    """

    def __init__(self, api_key: str, model: str = "deepseek/deepseek-chat-v3-0324:free") -> None:
        super().__init__(
            api_key=api_key,
            model=model,
            base_url="https://openrouter.ai/api/v1/chat/completions",
        )

    @property
    def provider_name(self) -> str:
        return "openrouter"

    def _headers(self) -> dict:
        headers = super()._headers()
        headers["X-Title"] = "MemoPilot"
        return headers

    def _cost(self, input_tokens: int, output_tokens: int) -> float:
        return 0.0


# ---------------------------------------------------------------------------
# Google AI Studio (Gemini)
# ---------------------------------------------------------------------------

class GoogleClient(BaseLLMClient):
    _PRICING: dict[str, tuple[float, float]] = {
        "gemini-2.0-flash": (0.10, 0.40),
        "gemini-1.5-flash": (0.075, 0.30),
        "gemini-1.5-pro": (1.25, 5.00),
    }

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash") -> None:
        self._key = api_key
        self._model = model

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def provider_name(self) -> str:
        return "google"

    def _url(self, action: str) -> str:
        base = f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:{action}"
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}key={self._key}"

    def _body(self, system: str, user: str, max_tokens: int) -> dict:
        return {
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "systemInstruction": {"parts": [{"text": system}]},
            "generationConfig": {"maxOutputTokens": max_tokens},
        }

    def _cost(self, input_tokens: int, output_tokens: int) -> float:
        pi, po = self._PRICING.get(self._model, (0.10, 0.40))
        return input_tokens / 1e6 * pi + output_tokens / 1e6 * po

    async def complete(self, system: str, user: str, max_tokens: int = 4096) -> LLMResponse:
        async with httpx.AsyncClient(timeout=120.0) as c:
            r = await c.post(
                self._url("generateContent"),
                headers={"content-type": "application/json"},
                json=self._body(system, user, max_tokens),
            )
            r.raise_for_status()
            d = r.json()
        usage = d.get("usageMetadata", {})
        i = usage.get("promptTokenCount", 0)
        o = usage.get("candidatesTokenCount", 0)
        text = d["candidates"][0]["content"]["parts"][0]["text"]
        return LLMResponse(text, i, o, self._cost(i, o), self._model, "google")

    async def stream(self, system: str, user: str, max_tokens: int = 4096) -> AsyncIterator[StreamChunk]:
        async with httpx.AsyncClient(timeout=120.0) as c:
            async with c.stream(
                "POST",
                self._url("streamGenerateContent") + "&alt=sse",
                headers={"content-type": "application/json"},
                json=self._body(system, user, max_tokens),
            ) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    p = json.loads(line[6:])
                    candidates = p.get("candidates") or []
                    if not candidates:
                        continue
                    parts = candidates[0].get("content", {}).get("parts") or []
                    for part in parts:
                        t = part.get("text", "")
                        if t:
                            yield StreamChunk(token=t)
                yield StreamChunk(token="", is_final=True)


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
# Local (any OpenAI-compatible server — Ollama, LM Studio, vLLM, OpenVINO, etc.)
# ---------------------------------------------------------------------------

class LocalClient(OpenAIClient):
    """Generic OpenAI-compatible client for any locally hosted model server.

    Works with any server that implements POST /v1/chat/completions:
    Ollama, LM Studio, vLLM, llama.cpp, OpenVINO GenAI, LocalAI, etc.
    No API key required.
    """

    def __init__(self, model: str, base_url: str = "http://localhost:1234") -> None:
        # Strip /v1 suffix if user pasted the full base URL — we always append /v1/chat/completions
        normalized = base_url.rstrip("/")
        if normalized.endswith("/v1"):
            normalized = normalized[:-3]
        super().__init__(
            api_key="local",
            model=model,
            base_url=f"{normalized}/v1/chat/completions",
        )

    @property
    def provider_name(self) -> str:
        return "local"

    def _headers(self) -> dict:
        # No Authorization header — local servers don't require it
        return {"Content-Type": "application/json"}

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
        return _wrap_with_retry(
            AnthropicClient(key, config.get("anthropic_model", "claude-haiku-4-5")),
            provider,
            config,
        )

    if provider == "openai":
        key = config.get("openai_api_key")
        if not key:
            raise ValueError("openai_api_key not set in config")
        return _wrap_with_retry(
            OpenAIClient(key, config.get("openai_model", "gpt-4o-mini")),
            provider,
            config,
        )

    if provider == "google":
        key = config.get("google_api_key")
        if not key:
            raise ValueError("google_api_key not set in config")
        return _wrap_with_retry(
            GoogleClient(key, config.get("google_model", "gemini-2.0-flash")),
            provider,
            config,
        )

    if provider == "openrouter":
        key = config.get("openrouter_api_key")
        if not key:
            raise ValueError("openrouter_api_key not set in config")
        return _wrap_with_retry(
            OpenRouterClient(key, config.get("openrouter_model", "deepseek/deepseek-chat-v3-0324:free")),
            provider,
            config,
        )

    if provider == "ollama":
        return _wrap_with_retry(
            OllamaClient(
                config.get("ollama_model", "llama3.1"),
                config.get("ollama_base_url", "http://localhost:11434"),
            ),
            provider,
            config,
        )

    if provider == "lmstudio":
        model = config.get("lmstudio_model", "")
        if not model:
            raise ValueError("lmstudio_model not set in config")
        return _wrap_with_retry(
            LMStudioClient(model, config.get("lmstudio_base_url", "http://localhost:1234")),
            provider,
            config,
        )

    if provider == "local":
        model = config.get("local_model", "")
        if not model:
            raise ValueError("local_model not set in config")
        return _wrap_with_retry(
            LocalClient(model, config.get("local_url", "http://localhost:1234")),
            provider,
            config,
        )

    raise ValueError(f"Unknown provider: {provider}")


class RelayLLMClient(BaseLLMClient):
    """LLM client that routes requests through the extension SSE relay (copilot mode)."""

    def __init__(self, relay_fn: Any, request_type: str = "generic") -> None:
        self._relay_fn = relay_fn
        self._request_type = request_type

    @property
    def model_id(self) -> str:
        return "copilot-relay"

    @property
    def provider_name(self) -> str:
        return "copilot"

    async def complete(self, system: str, user: str, max_tokens: int = 4096) -> "LLMResponse":
        content = await self._relay_fn(
            request_type=self._request_type,
            system=system,
            user=user,
        )
        return LLMResponse(content=content, input_tokens=0, output_tokens=0, cost_usd=0.0, model_id="copilot-relay", provider="copilot")

    async def stream(self, system: str, user: str, max_tokens: int = 4096) -> "AsyncIterator[StreamChunk]":
        content = await self.complete(system, user, max_tokens)
        async def _gen():
            yield StreamChunk(delta=content.content, finish_reason="stop")
        return _gen()
