"""Tests for LLM provider clients."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

from agent.llm_client import (
    AnthropicClient,
    LMStudioClient,
    LLMResponse,
    OllamaClient,
    OpenAIClient,
    StreamChunk,
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
    assert isinstance(client, AnthropicClient)
    assert client.model_id == "claude-haiku-4-5"


def test_build_client_openai():
    config = {"openai_api_key": "sk-test", "openai_model": "gpt-4o-mini"}
    client = build_client("openai", config)
    assert isinstance(client, OpenAIClient)


def test_build_client_ollama_no_key_required():
    config = {"ollama_model": "llama3.1", "ollama_base_url": "http://localhost:11434"}
    client = build_client("ollama", config)
    assert isinstance(client, OllamaClient)


def test_build_client_raises_on_missing_anthropic_key():
    with pytest.raises(ValueError, match="anthropic_api_key"):
        build_client("anthropic", {})


def test_build_client_raises_on_unknown_provider():
    with pytest.raises(ValueError, match="Unknown provider"):
        build_client("unknown_provider", {})
