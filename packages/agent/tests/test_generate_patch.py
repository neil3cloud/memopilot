"""Tests for the real generate_patch() endpoint — all LLM calls mocked."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agent.api import app, PATCH_SYSTEM, _build_prompt, _extract_diff

client = TestClient(app, raise_server_exceptions=True)

HEADERS = {"X-Agent-Token": "test-token"}


# ---------------------------------------------------------------------------
# Patch helpers — bypass uninitialized app state during unit tests
# ---------------------------------------------------------------------------

def _auth_patch():
    return patch("agent.api._expected_token", "test-token")


def _db_patch():
    return patch("agent.api._get_db", return_value=MagicMock())


def _config_patch():
    return patch("agent.api._get_config", return_value=MagicMock())


# ---------------------------------------------------------------------------
# Helper to build a mock LLMResponse
# ---------------------------------------------------------------------------

def _mock_response(content: str, provider: str = "anthropic", model: str = "claude-haiku-4-5"):
    from agent.llm_client import LLMResponse
    return LLMResponse(
        content=content,
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.001,
        model_id=model,
        provider=provider,
    )


VALID_DIFF = (
    "--- a/src/foo.py\n"
    "+++ b/src/foo.py\n"
    "@@ -1,2 +1,3 @@\n"
    " def foo():\n"
    "-    pass\n"
    "+    return 42\n"
)


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------

def test_extract_diff_valid():
    assert _extract_diff(VALID_DIFF) is not None
    assert _extract_diff(VALID_DIFF).startswith("--- a/")


def test_extract_diff_none_when_no_diff():
    assert _extract_diff("Some explanation without a diff.") is None


def test_extract_diff_trims_preamble():
    text = "Here is the patch:\n\n" + VALID_DIFF
    result = _extract_diff(text)
    assert result.startswith("--- a/")


def test_build_prompt_includes_task():
    prompt = _build_prompt("fix the bug", ["src/foo.py"], "context here")
    assert "fix the bug" in prompt
    assert "src/foo.py" in prompt
    assert "context here" in prompt


# ---------------------------------------------------------------------------
# Endpoint — successful patch generation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_patch_success_with_anthropic():
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(return_value=_mock_response(VALID_DIFF))

    with (
        _auth_patch(),
        _config_patch(),
        _db_patch(),
        patch("agent.api.load_provider_config", return_value={"anthropic_api_key": "sk-test"}),
        patch("agent.api.build_client", return_value=mock_client),
        patch("agent.api._relay_to_host", AsyncMock(return_value=None)),
        patch("agent.api.CostGuardService") as mock_cost,
    ):
        mock_cost.return_value.record_ai_call = AsyncMock(return_value="ai-call-1")

        response = client.post(
            "/v1/task/generate-patch",
            headers=HEADERS,
            json={"task_description": "fix the bug", "context_files": ["src/foo.py"]},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["total_files_changed"] == 1
    assert data["patches"][0]["diff"].startswith("--- a/")
    assert data["model_used"] == "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# Endpoint — PATCH_REFUSED falls through to next provider
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_patch_refused_falls_through():
    refused_resp = _mock_response("PATCH_REFUSED: task is ambiguous", provider="anthropic")
    success_resp = _mock_response(VALID_DIFF, provider="openai", model="gpt-4o-mini")

    call_count = 0

    async def mock_complete(system, user):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return refused_resp
        return success_resp

    mock_client = MagicMock()
    mock_client.complete = mock_complete

    with (
        _auth_patch(),
        _config_patch(),
        _db_patch(),
        patch("agent.api.load_provider_config",
              return_value={"anthropic_api_key": "sk-a", "openai_api_key": "sk-o"}),
        patch("agent.api.build_client", return_value=mock_client),
        patch("agent.api._relay_to_host", AsyncMock(return_value=None)),
        patch("agent.api.CostGuardService") as mock_cost,
    ):
        mock_cost.return_value.record_ai_call = AsyncMock(return_value="ai-call-2")

        response = client.post(
            "/v1/task/generate-patch",
            headers=HEADERS,
            json={"task_description": "fix the bug"},
        )

    assert response.status_code == 200
    assert call_count == 2


# ---------------------------------------------------------------------------
# Endpoint — all providers fail returns 503
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_patch_all_fail_returns_503():
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(side_effect=RuntimeError("connection refused"))

    with (
        _auth_patch(),
        _config_patch(),
        _db_patch(),
        patch("agent.api.load_provider_config", return_value={"anthropic_api_key": "sk-test"}),
        patch("agent.api.build_client", return_value=mock_client),
        patch("agent.api._relay_to_host", AsyncMock(return_value=None)),
        patch("agent.api.CostGuardService") as mock_cost,
    ):
        mock_cost.return_value.record_ai_call = AsyncMock()

        response = client.post(
            "/v1/task/generate-patch",
            headers=HEADERS,
            json={"task_description": "fix the bug"},
        )

    assert response.status_code == 503


# ---------------------------------------------------------------------------
# Endpoint — invalid diff falls through to next provider
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_patch_invalid_diff_falls_through():
    no_diff_resp = _mock_response("I cannot produce a diff for this.", provider="anthropic")
    good_resp = _mock_response(VALID_DIFF, provider="openai", model="gpt-4o-mini")
    responses = [no_diff_resp, good_resp]
    call_count = 0

    async def mock_complete(system, user):
        nonlocal call_count
        r = responses[call_count]
        call_count += 1
        return r

    mock_client = MagicMock()
    mock_client.complete = mock_complete

    with (
        _auth_patch(),
        _config_patch(),
        _db_patch(),
        patch("agent.api.load_provider_config",
              return_value={"anthropic_api_key": "sk-a", "openai_api_key": "sk-o"}),
        patch("agent.api.build_client", return_value=mock_client),
        patch("agent.api._relay_to_host", AsyncMock(return_value=None)),
        patch("agent.api.CostGuardService") as mock_cost,
    ):
        mock_cost.return_value.record_ai_call = AsyncMock(return_value="ai-call-3")

        response = client.post(
            "/v1/task/generate-patch",
            headers=HEADERS,
            json={"task_description": "add feature"},
        )

    assert response.status_code == 200
    assert call_count == 2
