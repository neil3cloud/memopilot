"""Tests for LLM provider clients."""

from __future__ import annotations

from collections.abc import AsyncIterator
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

from agent.llm_client import (
    AnthropicClient,
    GoogleClient,
    LMStudioClient,
    LLMResponse,
    OllamaClient,
    OpenAIClient,
    OpenRouterClient,
    RetryingLLMClient,
    StreamChunk,
    _coerce_bool,
    build_client,
)


# ---------------------------------------------------------------------------
# Helper: build a fake httpx Response
# ---------------------------------------------------------------------------

def _mock_response(status: int, body: dict) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = body
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# AnthropicClient
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_anthropic_complete_returns_llm_response():
    payload = {
        "content": [{"text": "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-x\n+y"}],
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }
    with patch("httpx.AsyncClient") as mock_cls:
        instance = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=instance)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        instance.post = AsyncMock(return_value=_mock_response(200, payload))

        client = AnthropicClient(api_key="sk-test", model="claude-haiku-4-5")
        resp = await client.complete("system", "user")

    assert isinstance(resp, LLMResponse)
    assert resp.input_tokens == 100
    assert resp.output_tokens == 50
    assert resp.provider == "anthropic"
    assert resp.model_id == "claude-haiku-4-5"
    assert resp.cost_usd > 0


@pytest.mark.asyncio
async def test_anthropic_raises_on_http_error():
    with patch("httpx.AsyncClient") as mock_cls:
        instance = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=instance)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        error_resp = MagicMock(spec=httpx.Response)
        error_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401", request=MagicMock(), response=MagicMock()
        )
        instance.post = AsyncMock(return_value=error_resp)

        client = AnthropicClient(api_key="bad-key")
        with pytest.raises(httpx.HTTPStatusError):
            await client.complete("s", "u")


# ---------------------------------------------------------------------------
# OpenAIClient
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_openai_complete_returns_llm_response():
    payload = {
        "choices": [{"message": {"content": "patch content"}}],
        "usage": {"prompt_tokens": 200, "completion_tokens": 80},
    }
    with patch("httpx.AsyncClient") as mock_cls:
        instance = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=instance)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        instance.post = AsyncMock(return_value=_mock_response(200, payload))

        client = OpenAIClient(api_key="sk-test", model="gpt-4o-mini")
        resp = await client.complete("system", "user")

    assert isinstance(resp, LLMResponse)
    assert resp.content == "patch content"
    assert resp.provider == "openai"
    assert resp.cost_usd >= 0


# ---------------------------------------------------------------------------
# GoogleClient
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_google_complete_returns_llm_response():
    payload = {
        "candidates": [{"content": {"parts": [{"text": "gemini output"}]}}],
        "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 40},
    }
    with patch("httpx.AsyncClient") as mock_cls:
        instance = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=instance)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        instance.post = AsyncMock(return_value=_mock_response(200, payload))

        client = GoogleClient(api_key="AIza-test", model="gemini-2.0-flash")
        resp = await client.complete("system", "user")

    assert isinstance(resp, LLMResponse)
    assert resp.content == "gemini output"
    assert resp.provider == "google"
    assert resp.model_id == "gemini-2.0-flash"
    assert resp.cost_usd > 0


# ---------------------------------------------------------------------------
# OpenRouterClient
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_openrouter_complete_returns_llm_response_with_zero_cost():
    payload = {
        "choices": [{"message": {"content": "openrouter response"}}],
        "usage": {"prompt_tokens": 30, "completion_tokens": 15},
    }
    with patch("httpx.AsyncClient") as mock_cls:
        instance = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=instance)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        instance.post = AsyncMock(return_value=_mock_response(200, payload))

        client = OpenRouterClient(api_key="sk-or-test", model="deepseek/deepseek-chat-v3-0324:free")
        resp = await client.complete("system", "user")

    assert isinstance(resp, LLMResponse)
    assert resp.content == "openrouter response"
    assert resp.provider == "openrouter"
    assert resp.cost_usd == 0.0


# ---------------------------------------------------------------------------
# OllamaClient
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ollama_complete_returns_llm_response():
    payload = {"message": {"content": "ollama diff output"}}
    with patch("httpx.AsyncClient") as mock_cls:
        instance = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=instance)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        instance.post = AsyncMock(return_value=_mock_response(200, payload))

        client = OllamaClient(model="llama3.1")
        resp = await client.complete("system", "user")

    assert isinstance(resp, LLMResponse)
    assert resp.content == "ollama diff output"
    assert resp.provider == "ollama"
    assert resp.cost_usd == 0.0
    assert resp.input_tokens == 0


# ---------------------------------------------------------------------------
# LMStudioClient
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lmstudio_extends_openai_with_zero_cost():
    payload = {
        "choices": [{"message": {"content": "lms response"}}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 20},
    }
    with patch("httpx.AsyncClient") as mock_cls:
        instance = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=instance)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        instance.post = AsyncMock(return_value=_mock_response(200, payload))

        client = LMStudioClient(model="mistral-7b")
        resp = await client.complete("system", "user")

    assert resp.cost_usd == 0.0
    assert resp.provider == "lmstudio"


# ---------------------------------------------------------------------------
# build_client factory
# ---------------------------------------------------------------------------

def test_build_client_anthropic():
    config = {"anthropic_api_key": "sk-ant-test", "anthropic_model": "claude-haiku-4-5"}
    client = build_client("anthropic", config)
    assert isinstance(client, RetryingLLMClient)
    assert isinstance(client._inner, AnthropicClient)
    assert client.model_id == "claude-haiku-4-5"


def test_build_client_openai():
    config = {"openai_api_key": "sk-test", "openai_model": "gpt-4o-mini"}
    client = build_client("openai", config)
    assert isinstance(client, RetryingLLMClient)
    assert isinstance(client._inner, OpenAIClient)


def test_build_client_ollama_no_key_required():
    config = {"ollama_model": "llama3.1", "ollama_base_url": "http://localhost:11434"}
    client = build_client("ollama", config)
    assert isinstance(client, OllamaClient)
    assert not isinstance(client, RetryingLLMClient)


def test_build_client_google():
    config = {"google_api_key": "AIza-test", "google_model": "gemini-2.0-flash"}
    client = build_client("google", config)
    assert isinstance(client, RetryingLLMClient)
    assert isinstance(client._inner, GoogleClient)
    assert client.model_id == "gemini-2.0-flash"


def test_build_client_raises_on_missing_anthropic_key():
    with pytest.raises(ValueError, match="anthropic_api_key"):
        build_client("anthropic", {})


def test_build_client_raises_on_missing_google_key():
    with pytest.raises(ValueError, match="google_api_key"):
        build_client("google", {})


def test_build_client_openrouter():
    config = {"openrouter_api_key": "sk-or-test", "openrouter_model": "deepseek/deepseek-chat-v3-0324:free"}
    client = build_client("openrouter", config)
    assert isinstance(client, RetryingLLMClient)
    assert isinstance(client._inner, OpenRouterClient)
    assert client.model_id == "deepseek/deepseek-chat-v3-0324:free"


def test_build_client_raises_on_missing_openrouter_key():
    with pytest.raises(ValueError, match="openrouter_api_key"):
        build_client("openrouter", {})


def test_build_client_raises_on_unknown_provider():
    with pytest.raises(ValueError, match="Unknown provider"):
        build_client("unknown_provider", {})


def test_build_client_retry_globally_disabled_for_cloud_provider():
    config = {
        "openai_api_key": "sk-test",
        "retry_enabled": False,
    }
    client = build_client("openai", config)
    assert isinstance(client, OpenAIClient)
    assert not isinstance(client, RetryingLLMClient)


def test_build_client_retry_provider_override_disabled_for_cloud_provider():
    config = {
        "openrouter_api_key": "sk-or-test",
        "retry_enabled": True,
        "openrouter_retry_enabled": False,
    }
    client = build_client("openrouter", config)
    assert isinstance(client, OpenRouterClient)
    assert not isinstance(client, RetryingLLMClient)


def test_build_client_local_providers_hard_exempt_from_retry():
    ollama_client = build_client("ollama", {"retry_enabled": True, "ollama_retry_enabled": True})
    local_client = build_client(
        "local",
        {
            "local_model": "qwen2.5-coder-7b-instruct",
            "local_url": "http://localhost:1234",
            "retry_enabled": True,
            "local_retry_enabled": True,
        },
    )
    lmstudio_client = build_client(
        "lmstudio",
        {
            "lmstudio_model": "mistral-7b",
            "retry_enabled": True,
            "lmstudio_retry_enabled": True,
        },
    )

    assert isinstance(ollama_client, OllamaClient)
    assert isinstance(local_client, OpenAIClient)
    assert isinstance(lmstudio_client, LMStudioClient)
    assert not isinstance(ollama_client, RetryingLLMClient)
    assert not isinstance(local_client, RetryingLLMClient)
    assert not isinstance(lmstudio_client, RetryingLLMClient)


def _http_error(status_code: int, retry_after: str | None = None) -> httpx.HTTPStatusError:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    headers: dict[str, str] = {}
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    response.headers = headers
    request = MagicMock(spec=httpx.Request)
    return httpx.HTTPStatusError(f"HTTP {status_code}", request=request, response=response)


class _ScriptedCompleteClient(OpenAIClient):
    def __init__(self, script: list[object]) -> None:
        super().__init__(api_key="sk-test", model="gpt-4o-mini")
        self._script = script
        self.calls = 0

    async def complete(self, system: str, user: str, max_tokens: int = 4096) -> LLMResponse:
        self.calls += 1
        event = self._script.pop(0)
        if isinstance(event, Exception):
            raise event
        return event


class _FailOnFirstPullIterator:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.closed = False

    def __aiter__(self) -> AsyncIterator[StreamChunk]:
        return self

    async def __anext__(self) -> StreamChunk:
        raise self._exc

    async def aclose(self) -> None:
        self.closed = True


class _OneChunkIterator:
    def __init__(self, chunks: list[StreamChunk], *, error_after_first: Exception | None = None) -> None:
        self._chunks = chunks
        self._index = 0
        self._error_after_first = error_after_first

    def __aiter__(self) -> AsyncIterator[StreamChunk]:
        return self

    async def __anext__(self) -> StreamChunk:
        if self._error_after_first is not None and self._index == 1:
            raise self._error_after_first
        if self._index >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk

    async def aclose(self) -> None:
        return None


class _ScriptedStreamClient(OpenAIClient):
    def __init__(self, streams: list[AsyncIterator[StreamChunk]]) -> None:
        super().__init__(api_key="sk-test", model="gpt-4o-mini")
        self._streams = streams
        self.calls = 0

    async def stream(self, system: str, user: str, max_tokens: int = 4096) -> AsyncIterator[StreamChunk]:
        self.calls += 1
        iterator = self._streams.pop(0)
        try:
            async for chunk in iterator:
                yield chunk
        finally:
            aclose = getattr(iterator, "aclose", None)
            if aclose is not None:
                await aclose()


@pytest.mark.asyncio
async def test_retrying_client_complete_retries_and_succeeds():
    scripted = _ScriptedCompleteClient(
        [
            _http_error(429),
            LLMResponse("ok", 1, 1, 0.0, "gpt-4o-mini", "openai"),
        ]
    )
    client = RetryingLLMClient(scripted, max_attempts=3, base_delay=1.0, max_delay=60.0)

    with patch("random.uniform", return_value=0.0), patch("asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
        response = await client.complete("system", "user")

    assert response.content == "ok"
    assert scripted.calls == 2
    sleep_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_retrying_client_complete_uses_retry_after_when_present():
    scripted = _ScriptedCompleteClient(
        [
            _http_error(429, retry_after="10"),
            LLMResponse("ok", 1, 1, 0.0, "gpt-4o-mini", "openai"),
        ]
    )
    client = RetryingLLMClient(scripted, max_attempts=3, base_delay=1.0, max_delay=60.0)

    with patch("random.uniform", return_value=0.0), patch("asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
        await client.complete("system", "user")

    sleep_mock.assert_awaited_once_with(5.0)


@pytest.mark.asyncio
async def test_retrying_client_complete_falls_back_when_retry_after_invalid():
    scripted = _ScriptedCompleteClient(
        [
            _http_error(429, retry_after="invalid"),
            LLMResponse("ok", 1, 1, 0.0, "gpt-4o-mini", "openai"),
        ]
    )
    client = RetryingLLMClient(scripted, max_attempts=3, base_delay=2.0, max_delay=60.0)

    with patch("random.uniform", return_value=0.0), patch("asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
        await client.complete("system", "user")

    sleep_mock.assert_awaited_once_with(1.0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("connect error"),
        httpx.ConnectTimeout("connect timeout"),
        httpx.ReadTimeout("read timeout"),
        httpx.PoolTimeout("pool timeout"),
    ],
)
async def test_retrying_client_complete_retries_on_transport_errors(exc: Exception):
    scripted = _ScriptedCompleteClient(
        [
            exc,
            LLMResponse("ok", 1, 1, 0.0, "gpt-4o-mini", "openai"),
        ]
    )
    client = RetryingLLMClient(scripted, max_attempts=3, base_delay=1.0, max_delay=60.0)

    with patch("random.uniform", return_value=0.0), patch("asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
        response = await client.complete("system", "user")

    assert response.content == "ok"
    sleep_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_retrying_client_complete_does_not_retry_non_retryable_status():
    scripted = _ScriptedCompleteClient([_http_error(401)])
    client = RetryingLLMClient(scripted, max_attempts=3, base_delay=1.0, max_delay=60.0)

    with patch("asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
        with pytest.raises(httpx.HTTPStatusError):
            await client.complete("system", "user")

    assert scripted.calls == 1
    sleep_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_retrying_client_complete_reraises_after_max_attempts_exhausted():
    scripted = _ScriptedCompleteClient([_http_error(503), _http_error(503), _http_error(503)])
    client = RetryingLLMClient(scripted, max_attempts=3, base_delay=1.0, max_delay=60.0)

    with patch("random.uniform", return_value=0.0), patch("asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
        with pytest.raises(httpx.HTTPStatusError):
            await client.complete("system", "user")

    assert scripted.calls == 3
    assert sleep_mock.await_count == 2


@pytest.mark.asyncio
async def test_retrying_client_stream_no_retry_after_first_chunk():
    stream_error = httpx.ReadTimeout("timed out")
    scripted = _ScriptedStreamClient(
        [
            _OneChunkIterator(
                [StreamChunk(token="first")],
                error_after_first=stream_error,
            )
        ]
    )
    client = RetryingLLMClient(scripted, max_attempts=3, base_delay=1.0, max_delay=60.0)

    with pytest.raises(httpx.ReadTimeout):
        async for _ in client.stream("system", "user"):
            pass

    assert scripted.calls == 1


@pytest.mark.asyncio
async def test_retrying_client_stream_closes_failed_generator_before_retry():
    first_iter = _FailOnFirstPullIterator(httpx.ConnectError("connect error"))
    second_iter = _OneChunkIterator([StreamChunk(token="ok")])
    scripted = _ScriptedStreamClient([first_iter, second_iter])
    client = RetryingLLMClient(scripted, max_attempts=3, base_delay=1.0, max_delay=60.0)

    chunks: list[StreamChunk] = []
    with patch("random.uniform", return_value=0.0), patch("asyncio.sleep", new_callable=AsyncMock):
        async for chunk in client.stream("system", "user"):
            chunks.append(chunk)

    assert first_iter.closed is True
    assert scripted.calls == 2
    assert chunks[0].token == "ok"


def test_coerce_bool_handles_strings_and_defaults():
    assert _coerce_bool("false", default=True) is False
    assert _coerce_bool("TRUE", default=False) is True
    assert _coerce_bool("not-a-bool", default=True) is True
    assert _coerce_bool(False, default=True) is False


def test_build_client_retry_max_attempts_clamps_to_minimum_one():
    client_zero = build_client(
        "openai",
        {
            "openai_api_key": "sk-test",
            "retry_max_attempts": 0,
        },
    )
    client_negative = build_client(
        "openai",
        {
            "openai_api_key": "sk-test",
            "retry_max_attempts": -5,
        },
    )

    assert isinstance(client_zero, RetryingLLMClient)
    assert client_zero._max_attempts == 1
    assert isinstance(client_negative, RetryingLLMClient)
    assert client_negative._max_attempts == 1
