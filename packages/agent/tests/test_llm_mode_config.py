from __future__ import annotations

import pytest
from httpx import AsyncClient

from agent import api
from agent.api import _configured_llm_mode


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("anthropic", "cloud"),
        ("openai", "cloud"),
        ("google", "cloud"),
        ("openrouter", "cloud"),
        ("local", "local"),
        ("ollama", "local"),
        ("lmstudio", "local"),
        (" host ", None),
        ("", None),
    ],
)
def test_configured_llm_mode(provider: str, expected: str | None) -> None:
    assert _configured_llm_mode(provider) == expected


@pytest.fixture(autouse=True)
def _reset_llm_mode_globals():
    """These are module-level globals in agent.api shared across the whole test session."""
    prev_mode, prev_user_set, prev_host_available = (
        api._llm_mode,
        api._llm_mode_user_set,
        api._host_model_available,
    )
    yield
    api._llm_mode, api._llm_mode_user_set, api._host_model_available = (
        prev_mode,
        prev_user_set,
        prev_host_available,
    )


@pytest.mark.asyncio
async def test_host_model_ready_auto_upgrades_to_copilot_even_after_cloud_config(
    client: AsyncClient, test_token: str
) -> None:
    """A configured cloud/local provider default must not block the Copilot auto-upgrade.

    Regression test for: startup_event() setting _llm_mode="cloud" from config used to make
    host_model_ready()'s `_llm_mode == "local"` check permanently false, so Copilot was never
    auto-selected even when available.
    """
    api._llm_mode = "cloud"
    api._llm_mode_user_set = False

    response = await client.post(
        "/v1/host/model-ready",
        json={"available": True, "model_id": "gpt-4o"},
        headers={"X-Agent-Token": test_token},
    )

    assert response.status_code == 200
    assert api._llm_mode == "copilot"


@pytest.mark.asyncio
async def test_host_model_ready_does_not_override_explicit_user_choice(
    client: AsyncClient, test_token: str
) -> None:
    """Once the user explicitly picks a mode, a later host probe must not silently flip it."""
    api._llm_mode = "local"
    api._llm_mode_user_set = True

    response = await client.post(
        "/v1/host/model-ready",
        json={"available": True, "model_id": "gpt-4o"},
        headers={"X-Agent-Token": test_token},
    )

    assert response.status_code == 200
    assert api._llm_mode == "local"


@pytest.mark.asyncio
async def test_set_llm_mode_marks_user_set(client: AsyncClient, test_token: str) -> None:
    api._host_model_available = True

    response = await client.post(
        "/v1/config/llm-mode",
        json={"mode": "copilot"},
        headers={"X-Agent-Token": test_token},
    )

    assert response.status_code == 200
    assert api._llm_mode_user_set is True
