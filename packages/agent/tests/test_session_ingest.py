from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
import pytest_asyncio

from agent.config import Config
from agent.db import DatabaseManager
from agent.llm_client import LLMResponse
from agent.session_ingest import (
    ClaudeCodeParser,
    OUTCOME_ALREADY_INGESTED,
    OUTCOME_INGESTED,
    OUTCOME_NO_AFFINITY,
    OUTCOME_NO_SESSIONS,
    PARSERS,
    SessionDigest,
    SessionDiscovery,
    SessionIngestService,
)


class _FakeClient:
    def __init__(self, facts: list[str]) -> None:
        self._facts = facts

    async def complete(self, system: str, user: str, max_tokens: int = 4096) -> LLMResponse:
        _ = system, user, max_tokens
        return LLMResponse(
            content=json.dumps({"facts": self._facts}),
            input_tokens=10,
            output_tokens=10,
            cost_usd=0.0,
            model_id="test-model",
            provider="test",
        )

    async def stream(self, system: str, user: str, max_tokens: int = 4096):
        _ = system, user, max_tokens
        if False:
            yield None

    @property
    def model_id(self) -> str:
        return "test-model"

    @property
    def provider_name(self) -> str:
        return "test"


class _FakeParser:
    def __init__(self, source: str, session_id: str, ts: float, digest: SessionDigest) -> None:
        self.source = source
        self._session_id = session_id
        self._ts = ts
        self._digest = digest

    def discover_sessions(self, workspace_path: Path) -> list[SessionDiscovery]:
        _ = workspace_path
        return [
            SessionDiscovery(
                session_id=self._session_id,
                transcript_path=Path("/tmp") / f"{self._session_id}.jsonl",
                discovered_at_ts=self._ts,
            )
        ]

    def parse(self, transcript_path: Path, session_id: str) -> SessionDigest:
        _ = transcript_path, session_id
        return self._digest


class _EmptyParser:
    def discover_sessions(self, workspace_path: Path) -> list[SessionDiscovery]:
        _ = workspace_path
        return []

    def parse(self, transcript_path: Path, session_id: str) -> SessionDigest:
        raise NotImplementedError


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def test_config(tmp_workspace: Path) -> Config:
    return Config(
        workspace_path=tmp_workspace,
        memopilot_dir=tmp_workspace / ".memopilot",
        global_dir=tmp_workspace / ".memopilot-global",
    )


@pytest_asyncio.fixture
async def test_db(test_config: Config) -> DatabaseManager:
    from agent.migration_runner import run_migrations

    db = DatabaseManager(Path(":memory:"))
    conn = await db.connect()
    await run_migrations(conn)
    yield db
    await db.close()


def _sanitize_for_test(workspace_path: Path) -> str:
    from agent.session_ingest import _sanitize_workspace_for_claude
    return _sanitize_workspace_for_claude(workspace_path)


def test_discover_sessions_claude_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "proj"
    workspace.mkdir()

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr("agent.session_ingest.Path.home", lambda: fake_home)

    project_dir = fake_home / ".claude" / "projects" / _sanitize_for_test(workspace)
    project_dir.mkdir(parents=True)

    older = project_dir / "older.jsonl"
    newer = project_dir / "newer.jsonl"
    older.write_text("{}\n", encoding="utf-8")
    newer.write_text("{}\n", encoding="utf-8")
    os.utime(older, (1_700_000_000, 1_700_000_000))
    os.utime(newer, (1_800_000_000, 1_800_000_000))

    parser = ClaudeCodeParser()
    discovered = parser.discover_sessions(workspace)

    assert [item.session_id for item in discovered] == ["newer", "older"]


def test_parse_transcript_bounded(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    lines: list[str] = []
    for i in range(30):
        lines.append(json.dumps({"type": "user", "content": f"user message {i} " + "x" * 500}))
        lines.append(json.dumps({"type": "assistant", "content": f"assistant message {i} " + "y" * 500}))
        lines.append(
            json.dumps(
                {
                    "name": "bash",
                    "input": {"command": f"pytest -k case_{i} --maxfail=1"},
                    "file_path": f"src/module_{i}.py",
                }
            )
        )

    transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")

    digest = ClaudeCodeParser().parse(transcript, "session")

    assert len(digest.user_queries) <= 10
    assert len(digest.assistant_decisions) <= 15
    assert len(digest.files_read) <= 20
    assert len(digest.commands_run) <= 10
    assert all(len(item) <= 200 for item in digest.user_queries)
    assert all(len(item) <= 150 for item in digest.assistant_decisions)
    assert all(len(item) <= 80 for item in digest.commands_run)


@pytest.mark.asyncio
async def test_ingest_deduplication(
    test_config: Config,
    test_db: DatabaseManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = SessionDigest(
        session_id="s1",
        source="claude_code",
        title="title",
        user_queries=["u1"],
        assistant_decisions=["a1"],
        files_read=["a.py"],
        files_modified=[],
        commands_run=[],
        total_messages=2,
        model_used="model",
    )

    monkeypatch.setattr(
        "agent.session_ingest.PARSERS",
        {"claude_code": _FakeParser("claude_code", "s1", 2000.0, digest)},
    )

    service = SessionIngestService(config=test_config, db=test_db)
    client = _FakeClient(["Use pytest -q for smoke checks."])

    first = await service.ingest_session(
        source="claude_code",
        session_id="latest",
        client=client,
        workspace_root=str(test_config.workspace_path),
    )
    second = await service.ingest_session(
        source="claude_code",
        session_id="latest",
        client=client,
        workspace_root=str(test_config.workspace_path),
    )

    assert first.already_ingested is False
    assert first.facts_written == 1
    assert second.already_ingested is True
    assert second.facts_written == 0


@pytest.mark.asyncio
async def test_ingest_writes_facts(
    test_config: Config,
    test_db: DatabaseManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = str(test_config.workspace_path)
    digest = SessionDigest(
        session_id="s2",
        source="copilot",
        title="title",
        user_queries=["u"],
        assistant_decisions=["a"],
        files_read=[os.path.join(ws, "src", "app.py")],
        files_modified=[os.path.join(ws, "src", "app.py")],
        commands_run=["pytest"],
        total_messages=2,
        model_used="gpt",
    )
    monkeypatch.setattr(
        "agent.session_ingest.PARSERS",
        {"copilot": _FakeParser("copilot", "s2", 2000.0, digest)},
    )

    service = SessionIngestService(config=test_config, db=test_db)
    client = _FakeClient([
        "Use workspace-scoped settings for indexer behavior.",
        "Run unit tests from packages/agent before extension builds.",
    ])

    result = await service.ingest_session(
        source="copilot",
        session_id="latest",
        client=client,
        workspace_root=str(test_config.workspace_path),
    )
    assert result.facts_written == 2

    conn = await test_db.connect()
    cursor = await conn.execute(
        """
        SELECT source, trust_level, memory_status, review_required
        FROM memory_items
        WHERE source = 'session_ingest:copilot'
        ORDER BY created_at ASC
        """
    )
    rows = await cursor.fetchall()
    assert len(rows) == 2
    assert all(row["trust_level"] == 2 for row in rows)
    assert all(row["memory_status"] == "pending_review" for row in rows)
    assert all(row["review_required"] == 1 for row in rows)


@pytest.mark.asyncio
async def test_auto_source_detection(
    test_config: Config,
    test_db: DatabaseManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_digest = SessionDigest(
        session_id="old",
        source="claude_code",
        title="old",
        user_queries=["u"],
        assistant_decisions=["a"],
        files_read=[],
        files_modified=[],
        commands_run=[],
        total_messages=2,
        model_used="m",
    )
    ws = str(test_config.workspace_path).replace("\\", "/")
    new_digest = SessionDigest(
        session_id="new",
        source="copilot",
        title="new",
        user_queries=["u"],
        assistant_decisions=["a"],
        files_read=[f"{ws}/src/app.py"],
        files_modified=[],
        commands_run=[],
        total_messages=2,
        model_used="m",
    )

    monkeypatch.setattr(
        "agent.session_ingest.PARSERS",
        {
            "claude_code": _FakeParser("claude_code", "old", 100.0, old_digest),
            "copilot": _FakeParser("copilot", "new", 200.0, new_digest),
        },
    )

    service = SessionIngestService(config=test_config, db=test_db)
    client = _FakeClient(["Use source auto-selection by newest timestamp."])

    result = await service.ingest_session(
        source="auto",
        session_id="latest",
        client=client,
        workspace_root=str(test_config.workspace_path),
    )

    assert result.source == "copilot"
    assert result.session_id == "new"


@pytest.mark.asyncio
async def test_writeback_safety_filter(
    test_config: Config,
    test_db: DatabaseManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = SessionDigest(
        session_id="s3",
        source="claude_code",
        title="title",
        user_queries=["u"],
        assistant_decisions=["a"],
        files_read=[],
        files_modified=[],
        commands_run=[],
        total_messages=2,
        model_used="m",
    )
    monkeypatch.setattr(
        "agent.session_ingest.PARSERS",
        {"claude_code": _FakeParser("claude_code", "s3", 300.0, digest)},
    )

    service = SessionIngestService(config=test_config, db=test_db)
    client = _FakeClient(
        [
            "api_key = sk-live-1234 should be copied into config.",
            "Prefer explicit migrations for schema evolution.",
        ]
    )

    result = await service.ingest_session(
        source="claude_code",
        session_id="latest",
        client=client,
        workspace_root=str(test_config.workspace_path),
    )
    assert result.facts_written == 1


@pytest.mark.asyncio
async def test_digest_to_prompt_format(test_config: Config, test_db: DatabaseManager) -> None:
    service = SessionIngestService(config=test_config, db=test_db)
    digest = SessionDigest(
        session_id="s4",
        source="codex_cli",
        title="demo",
        user_queries=["how do we run tests"],
        assistant_decisions=["use pytest"],
        files_read=["packages/agent/agent/api.py"],
        files_modified=["packages/agent/agent/mcp_server.py"],
        commands_run=["pytest -q"],
        total_messages=2,
        model_used="gpt-5",
    )

    prompt = service._digest_to_prompt(digest)

    assert '"session_id": "s4"' in prompt
    assert '"source": "codex_cli"' in prompt
    assert '"commands_run"' in prompt


@pytest.mark.asyncio
async def test_auto_no_transcripts(
    test_config: Config,
    test_db: DatabaseManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent.session_ingest.PARSERS",
        {"claude_code": _EmptyParser(), "copilot": _EmptyParser()},
    )
    service = SessionIngestService(config=test_config, db=test_db)
    client = _FakeClient([])

    result = await service.ingest_session(
        source="auto",
        session_id="latest",
        client=client,
        workspace_root=str(test_config.workspace_path),
    )

    assert result.outcome == OUTCOME_NO_SESSIONS
    assert result.already_ingested is False
    assert result.facts_written == 0
    assert "No session transcripts" in result.reason


@pytest.mark.asyncio
async def test_auto_only_non_affine_candidates(
    test_config: Config,
    test_db: DatabaseManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = SessionDigest(
        session_id="foreign",
        source="copilot",
        title="other project",
        user_queries=["u"],
        assistant_decisions=["a"],
        files_read=["/other/project/src/main.py"],
        files_modified=[],
        commands_run=[],
        total_messages=2,
        model_used="m",
    )
    monkeypatch.setattr(
        "agent.session_ingest.PARSERS",
        {"copilot": _FakeParser("copilot", "foreign", 500.0, digest)},
    )

    service = SessionIngestService(config=test_config, db=test_db)
    client = _FakeClient([])

    result = await service.ingest_session(
        source="auto",
        session_id="latest",
        client=client,
        workspace_root=str(test_config.workspace_path),
    )

    assert result.outcome == OUTCOME_NO_AFFINITY
    assert result.already_ingested is False
    assert result.facts_written == 0
    assert "not matching" in result.reason or "no file path" in result.reason


@pytest.mark.asyncio
async def test_explicit_source_non_affine_contract(
    test_config: Config,
    test_db: DatabaseManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = SessionDigest(
        session_id="ext1",
        source="cursor",
        title="unrelated workspace",
        user_queries=["u"],
        assistant_decisions=["a"],
        files_read=["/somewhere/else/file.ts"],
        files_modified=[],
        commands_run=[],
        total_messages=2,
        model_used="m",
    )
    monkeypatch.setattr(
        "agent.session_ingest.PARSERS",
        {"cursor": _FakeParser("cursor", "ext1", 100.0, digest)},
    )

    service = SessionIngestService(config=test_config, db=test_db)
    client = _FakeClient(["should not be written"])

    result = await service.ingest_session(
        source="cursor",
        session_id="latest",
        client=client,
        workspace_root=str(test_config.workspace_path),
    )

    assert result.outcome == OUTCOME_NO_AFFINITY
    assert result.already_ingested is False
    assert result.facts_written == 0
    assert len(result.memory_item_ids) == 0
    assert "no workspace affinity" in result.reason.lower()
