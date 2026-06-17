"""Tests for local_model_discovery.py — all network calls mocked."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

from agent.local_model_discovery import (
    LocalModel,
    discover_all_local,
    discover_lmstudio,
    discover_ollama,
    _ctx_tokens,
    _supports_tools,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_get(status: int, body: dict):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = body
    return resp


# ---------------------------------------------------------------------------
# discover_ollama
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_discover_ollama_returns_models_when_running():
    payload = {
        "models": [
            {"name": "llama3.1:latest"},
            {"name": "qwen2.5-coder:7b"},
        ]
    }
    with patch("httpx.AsyncClient") as mock_cls:
        instance = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=instance)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        instance.get = AsyncMock(return_value=_mock_get(200, payload))

        models = await discover_ollama()

    assert len(models) == 2
    assert all(m.source == "ollama" for m in models)
    assert any(m.model_id == "llama3.1:latest" for m in models)


@pytest.mark.asyncio
async def test_discover_ollama_returns_empty_on_connect_error():
    with patch("httpx.AsyncClient") as mock_cls:
        instance = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=instance)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        instance.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        models = await discover_ollama()

    assert models == []


@pytest.mark.asyncio
async def test_discover_ollama_returns_empty_on_timeout():
    with patch("httpx.AsyncClient") as mock_cls:
        instance = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=instance)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        instance.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        models = await discover_ollama()

    assert models == []


# ---------------------------------------------------------------------------
# discover_lmstudio
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_discover_lmstudio_returns_models_when_running():
    payload = {
        "data": [
            {"id": "mistral-7b-instruct", "name": "Mistral 7B Instruct"},
        ]
    }
    with patch("httpx.AsyncClient") as mock_cls:
        instance = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=instance)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        instance.get = AsyncMock(return_value=_mock_get(200, payload))

        models = await discover_lmstudio()

    assert len(models) == 1
    assert models[0].source == "lmstudio"
    assert models[0].model_id == "mistral-7b-instruct"


@pytest.mark.asyncio
async def test_discover_lmstudio_returns_empty_when_not_running():
    with patch("httpx.AsyncClient") as mock_cls:
        instance = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=instance)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        instance.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        models = await discover_lmstudio()

    assert models == []


# ---------------------------------------------------------------------------
# discover_all_local
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_discover_all_local_combines_both_sources():
    ollama_payload = {"models": [{"name": "llama3.1"}]}
    lms_payload = {"data": [{"id": "phi4"}]}

    call_count = 0

    async def fake_get(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if "11434" in url:
            return _mock_get(200, ollama_payload)
        return _mock_get(200, lms_payload)

    with patch("httpx.AsyncClient") as mock_cls:
        instance = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=instance)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        instance.get = AsyncMock(side_effect=fake_get)

        models = await discover_all_local({})

    sources = {m.source for m in models}
    assert "ollama" in sources
    assert "lmstudio" in sources


@pytest.mark.asyncio
async def test_discover_all_local_returns_empty_within_timeout_when_nothing_running():
    with patch("httpx.AsyncClient") as mock_cls:
        instance = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=instance)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        instance.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        models = await discover_all_local({})

    assert models == []


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------

def test_ctx_tokens_known_family():
    assert _ctx_tokens("llama3.1:8b") == 131072
    assert _ctx_tokens("qwen2.5-coder:7b") == 32768


def test_ctx_tokens_unknown_falls_back_to_default():
    assert _ctx_tokens("some-unknown-model") == 8192


def test_supports_tools_known_family():
    assert _supports_tools("llama3.1:latest") is True
    assert _supports_tools("qwen2.5:7b") is True


def test_supports_tools_unknown_model_returns_false():
    assert _supports_tools("phi4:latest") is False
