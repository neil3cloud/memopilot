"""Tests for POST /v1/context/build endpoint."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_context_build_empty_files(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    resp = await client.post(
        "/v1/context/build",
        headers=headers,
        json={"task_description": "Add validation", "suggested_files": []},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "files" in data
    assert "total_tokens" in data
    assert "estimated_cost_usd" in data
    assert data["total_tokens"] >= 0


@pytest.mark.asyncio
async def test_context_build_with_nonexistent_files(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    resp = await client.post(
        "/v1/context/build",
        headers=headers,
        json={
            "task_description": "Fix bug",
            "suggested_files": ["nonexistent_file_xyz.py"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["files"]) == 1
    assert data["files"][0]["path"] == "nonexistent_file_xyz.py"
    assert data["files"][0]["tokens"] >= 1


@pytest.mark.asyncio
async def test_context_build_file_overrides_take_priority(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    resp = await client.post(
        "/v1/context/build",
        headers=headers,
        json={
            "task_description": "Refactor service",
            "suggested_files": ["file_a.py", "file_b.py"],
            "file_overrides": ["override_x.py"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    paths = [f["path"] for f in data["files"]]
    assert "override_x.py" in paths
    assert "file_a.py" not in paths


@pytest.mark.asyncio
async def test_context_build_response_shape(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    resp = await client.post(
        "/v1/context/build",
        headers=headers,
        json={"task_description": "Add feature", "suggested_files": ["a.py"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["files"], list)
    assert isinstance(data["rules"], list)
    assert isinstance(data["skills"], list)
    assert isinstance(data["total_tokens"], int)
    assert isinstance(data["estimated_cost_usd"], float)


@pytest.mark.asyncio
async def test_context_build_caps_at_20_files(client: AsyncClient, test_token: str):
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    many_files = [f"file_{i}.py" for i in range(30)]
    resp = await client.post(
        "/v1/context/build",
        headers=headers,
        json={"task_description": "Large change", "suggested_files": many_files},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["files"]) <= 20


@pytest.mark.asyncio
async def test_context_build_falls_back_to_index_when_suggested_files_empty(
    client: AsyncClient,
    test_token: str,
    tmp_workspace,
):
    (tmp_workspace / "memory_indexing_service.py").write_text(
        "def build_memory_index() -> str:\n    return 'ok'\n",
        encoding="utf-8",
    )

    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)
    index_response = await client.post("/v1/workspace/index", headers=headers)
    assert index_response.status_code == 200

    resp = await client.post(
        "/v1/context/build",
        headers=headers,
        json={
            "task_description": "Improve memory indexing service",
            "suggested_files": [],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    paths = [entry["path"] for entry in data["files"]]
    # Symbol-level context uses "file.py::symbol_name" / "file.py::__skeleton__"
    # source keys for files with indexed symbols (see test_context_build_symbol_level.py) —
    # a file with indexed symbols is represented as one entry per included
    # symbol/skeleton block rather than a single whole-file entry.
    assert any(path.startswith("memory_indexing_service.py") for path in paths)


@pytest.mark.asyncio
async def test_context_assemble_returns_rendered_markdown(
    client: AsyncClient,
    test_token: str,
    tmp_workspace,
):
    (tmp_workspace / "billing_service.py").write_text(
        "def validate_billing() -> bool:\n    return True\n",
        encoding="utf-8",
    )

    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)
    index_response = await client.post("/v1/workspace/index", headers=headers)
    assert index_response.status_code == 200

    resp = await client.post(
        "/v1/context/assemble",
        headers=headers,
        json={
            "task_description": "Explain billing validation flow",
            "files_in_focus": ["billing_service.py"],
            "caller": "copilot_lm_tool",
            "max_output_tokens": 2000,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "rendered_markdown" in data
    assert "MemoPilot Context" in data["rendered_markdown"]
    assert "billing_service.py" in data["rendered_markdown"]
    assert data["context_pack_hash"]
    assert data["total_tokens"] >= 0


@pytest.mark.asyncio
async def test_context_assemble_enforces_token_budget(
    client: AsyncClient,
    test_token: str,
    tmp_workspace,
):
    """Regression test: /v1/context/assemble previously never set
    model_max_tokens on its internal request, so _generate_context_pack_response
    took an early-return "preview" branch that skipped budget enforcement,
    truncation, and deduplication entirely — found via real-world manual
    testing where a whole 137-line class got included in full, uncapped."""
    many_functions = "\n\n".join(
        f'''\
def helper_function_{i}(value):
    total = 0
    for offset in range(value):
        total += offset * {i}
        total -= offset // 2
    return total'''
        for i in range(20)
    )
    (tmp_workspace / "big_module.py").write_text(many_functions, encoding="utf-8")

    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)
    index_response = await client.post("/v1/workspace/index", headers=headers)
    assert index_response.status_code == 200

    whole_file_tokens = max(1, (len(many_functions) + 3) // 4)

    resp = await client.post(
        "/v1/context/assemble",
        headers=headers,
        json={
            "task_description": "explain helper_function_0",
            "files_in_focus": ["big_module.py"],
            "max_output_tokens": 200,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    # A small requested budget must actually constrain the result — before
    # the fix, this would include all 20 functions in full regardless of
    # max_output_tokens, since budget enforcement never ran.
    assert data["total_tokens"] < whole_file_tokens


@pytest.mark.asyncio
async def test_no_files_specified_finds_class_by_content_not_filename(
    client: AsyncClient,
    test_token: str,
    tmp_workspace,
):
    """Regression test: found via real-world manual testing. A task naming
    "ReservationService" failed to find the class when it lived inside a
    generically-named "services.py" (alongside an unrelated service) — the
    old fallback (_suggest_files_from_index) only matched keywords against
    file PATHS, so a file whose name doesn't mention the class it defines
    was invisible to it. Meanwhile an unrelated file that happened to share
    a filename substring with the keyword won out instead."""
    (tmp_workspace / "services.py").write_text(
        '''\
class ReservationService:
    """Handles reservation creation and cancellation."""
    def create_async(self, request):
        return True


class UnrelatedThing:
    def do_something(self):
        return False
''',
        encoding="utf-8",
    )
    # Named to collide with the task's own keywords via a literal filename
    # match — mirrors the real-world "reservationService.ts" false positive.
    (tmp_workspace / "reservation_service_client.py").write_text(
        "def call_reservation_service_api():\n    return None\n",
        encoding="utf-8",
    )

    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)
    index_response = await client.post("/v1/workspace/index", headers=headers)
    assert index_response.status_code == 200

    resp = await client.post(
        "/v1/context/build",
        headers=headers,
        json={
            "task_description": "fix the create_async method in ReservationService",
            "suggested_files": [],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    paths = [entry["path"] for entry in data["files"]]
    assert any(p.startswith("services.py") for p in paths), (
        f"expected services.py (containing the ReservationService class) to be found "
        f"by symbol content, got: {paths}"
    )
