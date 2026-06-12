"""Tests for Group 5 investigation mode and evidence workflows."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_evidence_source_classification_and_trust_levels(
    client: AsyncClient,
    test_token: str,
    tmp_workspace,
):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    fixtures = [
        ("notes.md", "# title\nInvestigation note", "markdown_doc", 2),
        ("trace.log", "timeout error", "text_log", 3),
        ("metrics.csv", "name,value\nlatency,12", "csv_data", 3),
        ("payload.json", '{"error":"invalid_token"}', "api_payload", 3),
        ("screenshot.png", None, "image", 5),
    ]
    for name, content, expected_type, expected_trust in fixtures:
        file_path = tmp_workspace / name
        if content is None:
            file_path.write_bytes(b"\x89PNG\r\n\x1a\n\x00")
        else:
            file_path.write_text(content, encoding="utf-8")

        attached = await client.post(
            "/v1/investigation/evidence/attach",
            headers=headers,
            json={"evidence_path": str(file_path)},
        )
        assert attached.status_code == 200
        body = attached.json()
        assert body["source_type"] == expected_type
        assert body["trust_level"] == expected_trust
        if expected_type == "image":
            assert body["extraction_status"] == "requires_ocr"


@pytest.mark.asyncio
async def test_attach_evidence_redacts_secrets(
    client: AsyncClient,
    test_token: str,
    tmp_workspace,
):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    evidence = tmp_workspace / "incident.txt"
    evidence.write_text("api_key=super-secret-value", encoding="utf-8")
    attached = await client.post(
        "/v1/investigation/evidence/attach",
        headers=headers,
        json={"evidence_path": str(evidence)},
    )
    assert attached.status_code == 200
    body = attached.json()
    assert body["redacted_values"] >= 1
    assert "super-secret-value" not in "\n".join(body["findings"])

    board = await client.get("/v1/investigation/evidence", headers=headers)
    assert board.status_code == 200
    item = board.json()["items"][0]
    assert item["extraction_status"] == "ok"
    assert item["redacted_values"] >= 1


@pytest.mark.asyncio
async def test_run_investigation_builds_context_pack_and_coverage(
    client: AsyncClient,
    test_token: str,
    tmp_workspace,
):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    (tmp_workspace / "service.py").write_text(
        "def retry_service() -> None:\n    pass\n",
        encoding="utf-8",
    )
    (tmp_workspace / "tests").mkdir()
    (tmp_workspace / "tests" / "test_service.py").write_text(
        "def test_retry_service() -> None:\n    assert True\n",
        encoding="utf-8",
    )
    await client.post("/v1/workspace/index", headers=headers)

    evidence = tmp_workspace / "summary.txt"
    evidence.write_text(
        "retry_service failed under timeout and service error path.",
        encoding="utf-8",
    )
    attached = await client.post(
        "/v1/investigation/evidence/attach",
        headers=headers,
        json={"evidence_path": str(evidence)},
    )
    assert attached.status_code == 200

    run = await client.post(
        "/v1/investigation/run",
        headers=headers,
        json={
            "title": "Retry failure",
            "description": "Investigate retry behavior regression",
            "acceptance_criteria": [
                "service retry path is covered by tests",
                "timeout fallback behavior is covered by tests",
            ],
        },
    )
    assert run.status_code == 200
    payload = run.json()
    assert payload["evidence_count"] >= 1
    assert any(path.endswith("service.py") for path in payload["impacted_files"])
    assert any("test_service.py" in path for path in payload["related_tests"])
    assert "timeout fallback behavior is covered by tests" in payload["missing_test_coverage"]
    assert "## Evidence Sources" in payload["context_pack"]
    assert "## Missing Test Coverage" in payload["context_pack"]
    assert "[text_log:" in payload["context_pack"]
