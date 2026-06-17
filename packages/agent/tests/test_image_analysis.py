from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from agent.image_analysis import ImageAnalysisResult, analyze_image


@pytest.mark.asyncio
async def test_analyze_image_cloud_openai_success(
    tmp_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_workspace / ".memopilot").mkdir(parents=True, exist_ok=True)
    image_path = tmp_workspace / "ui.png"
    Image.new("RGB", (32, 32), color=(255, 255, 255)).save(image_path)

    async def _fake_local(_file_path: Path) -> ImageAnalysisResult:
        return ImageAnalysisResult(source="local", error="local unavailable", error_messages=["local"])

    async def _fake_openai(**_kwargs) -> str:
        return (
            '{"description":"Error dialog with retry button",'
            '"ui_elements":["Retry button","Cancel button"],'
            '"error_messages":["Connection timeout"],'
            '"ocr_text":"Connection timeout"}'
        )

    monkeypatch.setattr("agent.image_analysis._analyze_image_local", _fake_local)
    monkeypatch.setattr(
        "agent.image_analysis.load_provider_config",
        lambda _root: {
            "fallback_order": ["openai"],
            "openai_api_key": "test-key",
            "openai_model": "gpt-4o-mini",
        },
    )
    monkeypatch.setattr("agent.image_analysis._call_openai_vision", _fake_openai)

    result = await analyze_image(image_path, allow_cloud=True, workspace_root=str(tmp_workspace))

    assert result.error is None
    assert result.source == "cloud:openai"
    assert "Error dialog" in result.description
    assert "Retry button" in result.ui_elements
    assert "Connection timeout" in result.error_messages


@pytest.mark.asyncio
async def test_analyze_image_returns_unavailable_when_cloud_disallowed(
    tmp_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_workspace / "ui.png"
    Image.new("RGB", (16, 16), color=(255, 255, 255)).save(image_path)

    async def _fake_local(_file_path: Path) -> ImageAnalysisResult:
        return ImageAnalysisResult(source="local", error="local unavailable", error_messages=["local"])

    monkeypatch.setattr("agent.image_analysis._analyze_image_local", _fake_local)
    monkeypatch.setattr("agent.image_analysis.extract_ocr_text", lambda _path: None)

    result = await analyze_image(image_path, allow_cloud=False)

    assert result.source == "unavailable"
    assert result.error is not None
    assert "Cloud analysis not permitted." in result.error_messages


@pytest.mark.asyncio
async def test_investigation_attach_uses_image_analysis_findings(
    client,
    test_token: str,
    tmp_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    image_path = tmp_workspace / "dialog.png"
    Image.new("RGB", (64, 48), color=(250, 250, 250)).save(image_path)

    async def _fake_analyze_image(
        _path: Path,
        allow_cloud: bool = False,
        workspace_root: str | None = None,
    ) -> ImageAnalysisResult:
        return ImageAnalysisResult(
            description="Modal dialog with Save and Cancel",
            ocr_text="Save\nCancel",
            ui_elements=["Save button", "Cancel button"],
            error_messages=["Validation warning"],
            source="cloud:openai" if allow_cloud else "local",
        )

    monkeypatch.setattr("agent.investigation_service.analyze_image", _fake_analyze_image)

    response = await client.post(
        "/v1/investigation/evidence/attach",
        headers=headers,
        json={
            "evidence_path": str(image_path),
            "allow_cloud_image_analysis": True,
        },
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["source_type"] == "screenshot"
    assert any("Image summary: Modal dialog" in finding for finding in payload["findings"])
    assert any("UI elements: Save button, Cancel button" in finding for finding in payload["findings"])
    assert any("OCR: Save" in finding for finding in payload["findings"])
