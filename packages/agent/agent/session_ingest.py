"""Session transcript ingestion from external AI coding tools.

Parses bounded session digests from supported transcript formats,
synthesizes project facts with an LLM, and stores them as pending-review
memory items.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from .config import Config
from .db import DatabaseManager
from .llm_client import BaseLLMClient
from .memory_manager_service import check_writeback_safety

_MAX_DIGEST_CHARS = 8000
_MAX_USER_QUERIES = 10
_MAX_ASSISTANT_DECISIONS = 15
_MAX_FILES = 20
_MAX_COMMANDS = 10


@dataclass(frozen=True)
class SessionDiscovery:
    session_id: str
    transcript_path: Path
    discovered_at_ts: float


@dataclass
class SessionDigest:
    """Tool-agnostic bounded extraction from a session transcript."""

    session_id: str
    source: str
    title: str
    user_queries: list[str]
    assistant_decisions: list[str]
    files_read: list[str]
    files_modified: list[str]
    commands_run: list[str]
    total_messages: int
    model_used: str


OUTCOME_INGESTED = "ingested"
OUTCOME_ALREADY_INGESTED = "already_ingested"
OUTCOME_NO_SESSIONS = "no_sessions"
OUTCOME_NO_AFFINITY = "no_affinity"

IngestOutcome = Literal["ingested", "already_ingested", "no_sessions", "no_affinity"]


@dataclass
class IngestResult:
    session_id: str
    source: str
    facts_written: int
    already_ingested: bool
    outcome: IngestOutcome
    memory_item_ids: list[str]
    reason: str = ""


class TranscriptParser(Protocol):
    def discover_sessions(self, workspace_path: Path) -> list[SessionDiscovery]:
        """Return discovered sessions sorted newest-first."""

    def parse(self, transcript_path: Path, session_id: str) -> SessionDigest:
        """Parse transcript into bounded SessionDigest."""


def _truncate(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text[:limit]


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _safe_json_loads(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None


_MAX_JSONL_LINES = 5000
_MAX_JSONL_BYTES = 20 * 1024 * 1024


def _iter_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    bytes_read = 0
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                bytes_read += len(line)
                if bytes_read > _MAX_JSONL_BYTES:
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                payload = _safe_json_loads(stripped)
                if isinstance(payload, dict):
                    rows.append(payload)
                if len(rows) >= _MAX_JSONL_LINES:
                    break
    except OSError:
        return []
    return rows


def _first_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            if item.get("type") in {"thinking", "reasoning"}:
                continue
            if isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item.get("content"), str):
                parts.append(item["content"])
        return "\n".join(parts)
    if isinstance(value, dict):
        for key in ("text", "content", "message", "value"):
            field = value.get(key)
            if isinstance(field, str):
                return field
    return ""


def _extract_paths(node: Any, out: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            lowered = str(key).lower()
            if isinstance(value, str):
                if any(token in lowered for token in ("path", "file", "uri")):
                    out.add(value)
                elif re.search(r"[\\/].+\.[a-zA-Z0-9]{1,8}$", value):
                    out.add(value)
            else:
                _extract_paths(value, out)
    elif isinstance(node, list):
        for item in node:
            _extract_paths(item, out)


def _extract_command(tool_name: str, tool_input: Any) -> str | None:
    lowered = tool_name.lower()
    if any(token in lowered for token in ("bash", "shell", "terminal", "command", "run")):
        if isinstance(tool_input, str):
            return _truncate(tool_input, 80)
        if isinstance(tool_input, dict):
            for key in ("command", "cmd", "description", "script"):
                value = tool_input.get(key)
                if isinstance(value, str):
                    return _truncate(value, 80)
    return None


def _sanitize_workspace_for_claude(workspace_path: Path) -> str:
    raw = str(workspace_path.resolve())
    if os.name == "nt":
        # C:\Users\Neil\Projects\foo -> C--Users-Neil-Projects-foo
        normalized = raw.replace(":\\", "--").replace("\\", "-")
    else:
        # /home/neil/projects/foo -> -home-neil-projects-foo
        normalized = raw.replace("/", "-")
    return normalized


def _build_bounded_digest(
    *,
    session_id: str,
    source: str,
    title: str,
    user_queries: list[str],
    assistant_decisions: list[str],
    files_read: list[str],
    files_modified: list[str],
    commands_run: list[str],
    total_messages: int,
    model_used: str,
) -> SessionDigest:
    users = [_truncate(s, 200) for s in _dedupe_keep_order(user_queries)[:_MAX_USER_QUERIES]]
    assistants = [
        _truncate(s, 150)
        for s in _dedupe_keep_order(assistant_decisions)[:_MAX_ASSISTANT_DECISIONS]
    ]

    # Bound text-heavy fields by an approximate char budget.
    budget = _MAX_DIGEST_CHARS
    bounded_users: list[str] = []
    for item in users:
        if budget - len(item) < 0:
            break
        bounded_users.append(item)
        budget -= len(item)

    bounded_assistants: list[str] = []
    for item in assistants:
        if budget - len(item) < 0:
            break
        bounded_assistants.append(item)
        budget -= len(item)

    return SessionDigest(
        session_id=session_id,
        source=source,
        title=_truncate(title, 160),
        user_queries=bounded_users,
        assistant_decisions=bounded_assistants,
        files_read=_dedupe_keep_order(files_read)[:_MAX_FILES],
        files_modified=_dedupe_keep_order(files_modified)[:_MAX_FILES],
        commands_run=[_truncate(cmd, 80) for cmd in _dedupe_keep_order(commands_run)[:_MAX_COMMANDS]],
        total_messages=total_messages,
        model_used=_truncate(model_used, 80),
    )


class ClaudeCodeParser:
    source = "claude_code"

    def discover_sessions(self, workspace_path: Path) -> list[SessionDiscovery]:
        projects_dir = Path.home() / ".claude" / "projects"
        if not projects_dir.exists():
            return []
        sanitized = _sanitize_workspace_for_claude(workspace_path)
        root = None
        for candidate in projects_dir.iterdir():
            if not candidate.is_dir():
                continue
            if candidate.name == sanitized or (
                os.name == "nt" and candidate.name.lower() == sanitized.lower()
            ):
                root = candidate
                break
        if root is None:
            return []
        found: list[SessionDiscovery] = []
        for path in root.glob("*.jsonl"):
            try:
                stat = path.stat()
            except OSError:
                continue
            found.append(
                SessionDiscovery(
                    session_id=path.stem,
                    transcript_path=path,
                    discovered_at_ts=stat.st_mtime,
                )
            )
        return sorted(found, key=lambda item: item.discovered_at_ts, reverse=True)

    def parse(self, transcript_path: Path, session_id: str) -> SessionDigest:
        rows = _iter_jsonl(transcript_path)
        users: list[str] = []
        assistants: list[str] = []
        commands: list[str] = []
        file_reads: set[str] = set()
        file_mods: set[str] = set()
        title = ""
        model = ""
        total = 0

        for row in rows:
            msg_type = str(row.get("type") or row.get("role") or "").lower()
            content = _first_text(row.get("content") or row.get("message") or row.get("text"))
            if msg_type == "user":
                total += 1
                if content:
                    users.append(content)
            elif msg_type == "assistant":
                total += 1
                if content:
                    assistants.append(content)

            if not title:
                title = str(row.get("ai-title") or row.get("title") or "")
            if not model:
                model = str(row.get("model") or row.get("model_name") or "")

            tool_name = str(row.get("name") or row.get("tool_name") or "")
            tool_input = row.get("input") or row.get("arguments") or row.get("tool_input")
            cmd = _extract_command(tool_name, tool_input)
            if cmd:
                commands.append(cmd)

            _extract_paths(row, file_reads)

            if any(token in tool_name.lower() for token in ("edit", "write", "replace", "apply_patch")):
                _extract_paths(tool_input, file_mods)

        return _build_bounded_digest(
            session_id=session_id,
            source=self.source,
            title=title,
            user_queries=users,
            assistant_decisions=assistants,
            files_read=sorted(file_reads),
            files_modified=sorted(file_mods),
            commands_run=commands,
            total_messages=total,
            model_used=model,
        )


class CopilotParser:
    source = "copilot"

    def discover_sessions(self, workspace_path: Path) -> list[SessionDiscovery]:
        home = Path.home()
        roots = [
            home / "AppData" / "Roaming" / "Code" / "User" / "globalStorage" / "github.copilot-chat" / "debug-logs",
            home / "AppData" / "Roaming" / "Code" / "User" / "workspaceStorage",
        ]
        found: list[SessionDiscovery] = []

        global_root = roots[0]
        if global_root.exists():
            for path in global_root.glob("*/main.jsonl"):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                found.append(SessionDiscovery(path.parent.name, path, stat.st_mtime))

        ws_root = roots[1]
        if ws_root.exists():
            for path in ws_root.rglob("GitHub.copilot-chat/debug-logs/*/main.jsonl"):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                found.append(SessionDiscovery(path.parent.name, path, stat.st_mtime))

        return sorted(found, key=lambda item: item.discovered_at_ts, reverse=True)

    def parse(self, transcript_path: Path, session_id: str) -> SessionDigest:
        rows = _iter_jsonl(transcript_path)
        users: list[str] = []
        assistants: list[str] = []
        commands: list[str] = []
        file_reads: set[str] = set()
        file_mods: set[str] = set()
        model = ""
        total = 0

        for row in rows:
            role = str(row.get("role") or row.get("type") or "").lower()
            text = _first_text(row.get("content") or row.get("message") or row.get("body") or row)
            if role == "user":
                total += 1
                if text:
                    users.append(text)
            elif role == "assistant":
                total += 1
                if text:
                    assistants.append(text)

            if not model:
                model = str(row.get("model") or row.get("modelId") or row.get("model_name") or "")

            tool_name = str(row.get("tool") or row.get("tool_name") or row.get("name") or "")
            tool_input = row.get("input") or row.get("arguments") or row.get("params")
            cmd = _extract_command(tool_name, tool_input)
            if cmd:
                commands.append(cmd)

            _extract_paths(row, file_reads)
            if any(token in tool_name.lower() for token in ("edit", "write", "patch", "replace")):
                _extract_paths(tool_input, file_mods)

        # Optional markdown fallback alongside the debug session.
        ask_agent_md = transcript_path.parent.parent / "ask-agent" / "Ask.agent.md"
        if ask_agent_md.exists():
            try:
                for line in ask_agent_md.read_text(encoding="utf-8", errors="ignore").splitlines():
                    stripped = line.strip()
                    if stripped.lower().startswith("user:"):
                        users.append(stripped.split(":", 1)[1].strip())
                    elif stripped.lower().startswith("assistant:"):
                        assistants.append(stripped.split(":", 1)[1].strip())
            except OSError:
                pass

        return _build_bounded_digest(
            session_id=session_id,
            source=self.source,
            title=f"Copilot session {session_id}",
            user_queries=users,
            assistant_decisions=assistants,
            files_read=sorted(file_reads),
            files_modified=sorted(file_mods),
            commands_run=commands,
            total_messages=total,
            model_used=model,
        )


class CursorParser:
    source = "cursor"

    def discover_sessions(self, workspace_path: Path) -> list[SessionDiscovery]:
        appdata = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
        root = appdata / "Cursor" / "User" / "workspaceStorage"
        if not root.exists():
            return []
        found: list[SessionDiscovery] = []
        for db_path in root.glob("*/state.vscdb"):
            try:
                stat = db_path.stat()
            except OSError:
                continue
            found.append(
                SessionDiscovery(
                    session_id=db_path.parent.name,
                    transcript_path=db_path,
                    discovered_at_ts=stat.st_mtime,
                )
            )
        return sorted(found, key=lambda item: item.discovered_at_ts, reverse=True)

    def parse(self, transcript_path: Path, session_id: str) -> SessionDigest:
        users: list[str] = []
        assistants: list[str] = []
        commands: list[str] = []
        files_read: set[str] = set()
        file_mods: set[str] = set()

        # Open read-only to reduce lock contention with Cursor.
        db_uri = f"file:{transcript_path.as_posix()}?mode=ro"
        rows_json: str = ""
        try:
            conn = sqlite3.connect(db_uri, uri=True)
            try:
                cursor = conn.execute(
                    "SELECT value FROM ItemTable WHERE key='aiService.prompts' LIMIT 1"
                )
                row = cursor.fetchone()
                if row and isinstance(row[0], str):
                    rows_json = row[0]
            finally:
                conn.close()
        except sqlite3.Error:
            rows_json = ""

        payload = _safe_json_loads(rows_json)
        total = 0
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role") or item.get("type") or "").lower()
                text = _first_text(item.get("content") or item.get("message") or item)
                if role == "user":
                    total += 1
                    if text:
                        users.append(text)
                elif role == "assistant":
                    total += 1
                    if text:
                        assistants.append(text)
                tool_name = str(item.get("tool") or item.get("name") or "")
                tool_input = item.get("input") or item.get("arguments")
                cmd = _extract_command(tool_name, tool_input)
                if cmd:
                    commands.append(cmd)
                _extract_paths(item, files_read)
                if any(token in tool_name.lower() for token in ("edit", "write", "patch", "replace")):
                    _extract_paths(tool_input, file_mods)

        return _build_bounded_digest(
            session_id=session_id,
            source=self.source,
            title=f"Cursor session {session_id}",
            user_queries=users,
            assistant_decisions=assistants,
            files_read=sorted(files_read),
            files_modified=sorted(file_mods),
            commands_run=commands,
            total_messages=total,
            model_used="",
        )


class GeminiCliParser:
    source = "gemini_cli"

    def discover_sessions(self, workspace_path: Path) -> list[SessionDiscovery]:
        root = Path.home() / ".gemini" / "tmp"
        if not root.exists():
            return []
        found: list[SessionDiscovery] = []
        for path in root.rglob("chats/*.jsonl"):
            try:
                stat = path.stat()
            except OSError:
                continue
            found.append(SessionDiscovery(path.stem, path, stat.st_mtime))
        return sorted(found, key=lambda item: item.discovered_at_ts, reverse=True)

    def parse(self, transcript_path: Path, session_id: str) -> SessionDigest:
        return _parse_generic_jsonl_source(
            transcript_path=transcript_path,
            session_id=session_id,
            source=self.source,
            title=f"Gemini CLI session {session_id}",
        )


class CodexCliParser:
    source = "codex_cli"

    def discover_sessions(self, workspace_path: Path) -> list[SessionDiscovery]:
        root = Path.home() / ".codex" / "sessions"
        if not root.exists():
            return []
        found: list[SessionDiscovery] = []
        for path in root.rglob("*.jsonl"):
            try:
                stat = path.stat()
            except OSError:
                continue
            found.append(SessionDiscovery(path.stem, path, stat.st_mtime))
        return sorted(found, key=lambda item: item.discovered_at_ts, reverse=True)

    def parse(self, transcript_path: Path, session_id: str) -> SessionDigest:
        return _parse_generic_jsonl_source(
            transcript_path=transcript_path,
            session_id=session_id,
            source=self.source,
            title=f"Codex CLI session {session_id}",
        )


def _parse_generic_jsonl_source(
    *,
    transcript_path: Path,
    session_id: str,
    source: str,
    title: str,
) -> SessionDigest:
    rows = _iter_jsonl(transcript_path)
    users: list[str] = []
    assistants: list[str] = []
    commands: list[str] = []
    files_read: set[str] = set()
    files_modified: set[str] = set()
    model = ""
    total = 0

    for row in rows:
        role = str(row.get("role") or row.get("type") or "").lower()
        text = _first_text(row.get("content") or row.get("message") or row.get("text") or row)
        if role == "user":
            total += 1
            if text:
                users.append(text)
        elif role in {"assistant", "model"}:
            total += 1
            if text:
                assistants.append(text)

        if not model:
            model = str(row.get("model") or row.get("model_name") or row.get("modelId") or "")

        tool_name = str(row.get("tool") or row.get("tool_name") or row.get("name") or "")
        tool_input = row.get("input") or row.get("arguments") or row.get("params")
        cmd = _extract_command(tool_name, tool_input)
        if cmd:
            commands.append(cmd)

        _extract_paths(row, files_read)
        if any(token in tool_name.lower() for token in ("edit", "write", "patch", "replace")):
            _extract_paths(tool_input, files_modified)

    return _build_bounded_digest(
        session_id=session_id,
        source=source,
        title=title,
        user_queries=users,
        assistant_decisions=assistants,
        files_read=sorted(files_read),
        files_modified=sorted(files_modified),
        commands_run=commands,
        total_messages=total,
        model_used=model,
    )


PARSERS: dict[str, TranscriptParser] = {
    "claude_code": ClaudeCodeParser(),
    "copilot": CopilotParser(),
    "cursor": CursorParser(),
    "gemini_cli": GeminiCliParser(),
    "codex_cli": CodexCliParser(),
}

SESSION_INGEST_SYSTEM = (
    "You extract reusable project facts from an AI coding session transcript. "
    "You are given a digest: the user's requests, the assistant's key decisions, "
    "files read or modified, and commands run. "
    "Extract facts that would help future developers understand this codebase - "
    "architectural decisions, implementation patterns, gotchas discovered, "
    "dependency constraints, test strategies, and configuration choices. "
    "Ignore generic coding knowledge and tool mechanics. Focus on project-specific knowledge. "
    'Respond ONLY with valid JSON: {"facts": ["fact1", "fact2"]}. '
    "Each fact is one sentence, max 25 words. Return 1-8 facts or an empty list."
)


class SessionIngestService:
    def __init__(self, *, config: Config, db: DatabaseManager) -> None:
        self._config = config
        self._db = db

    def _normalize_workspace_root(self, workspace_root: str | None) -> str:
        if workspace_root is None or not workspace_root.strip():
            return str(self._config.workspace_path.resolve())
        return str(Path(workspace_root).resolve())

    async def list_available_sessions(self, workspace_path: Path) -> dict[str, list[dict[str, str]]]:
        normalized_root = self._normalize_workspace_root(str(workspace_path))
        out: dict[str, list[dict[str, str]]] = {}
        for source, parser in PARSERS.items():
            entries: list[dict[str, str]] = []
            for item in parser.discover_sessions(workspace_path):
                if await self._check_already_ingested(
                    source=source,
                    session_id=item.session_id,
                    workspace_root=normalized_root,
                ):
                    continue
                entries.append(
                    {
                        "session_id": item.session_id,
                        "transcript_path": str(item.transcript_path),
                    }
                )
            if entries:
                out[source] = entries
        return out

    async def ingest_session(
        self,
        *,
        source: str,
        session_id: str,
        client: BaseLLMClient,
        workspace_root: str | None = None,
    ) -> IngestResult:
        normalized_root = self._normalize_workspace_root(workspace_root)
        workspace_path = Path(normalized_root)

        if source == "auto":
            auto_result = await self._resolve_auto_uningested(
                session_id=session_id,
                workspace_path=workspace_path,
                normalized_root=normalized_root,
            )
            if isinstance(auto_result, tuple):
                discovered_source, selected = auto_result
            else:
                reason = auto_result if isinstance(auto_result, str) else "No eligible sessions found"
                if "No session transcripts" in reason or "not found in discovered" in reason:
                    outcome = OUTCOME_NO_SESSIONS
                elif "not matching" in reason or "no file path" in reason:
                    outcome = OUTCOME_NO_AFFINITY
                else:
                    outcome = OUTCOME_ALREADY_INGESTED
                return IngestResult(
                    session_id="",
                    source="auto",
                    facts_written=0,
                    already_ingested=outcome == OUTCOME_ALREADY_INGESTED,
                    outcome=outcome,
                    memory_item_ids=[],
                    reason=reason,
                )
        else:
            discovered_source, selected = self._resolve_target_session(
                source=source,
                session_id=session_id,
                workspace_path=workspace_path,
            )
            if await self._check_already_ingested(
                source=discovered_source,
                session_id=selected.session_id,
                workspace_root=normalized_root,
            ):
                return IngestResult(
                    session_id=selected.session_id,
                    source=discovered_source,
                    facts_written=0,
                    already_ingested=True,
                    outcome=OUTCOME_ALREADY_INGESTED,
                    memory_item_ids=[],
                    reason="Session already ingested",
                )

        parser = PARSERS[discovered_source]
        digest = parser.parse(selected.transcript_path, selected.session_id)

        if not self._has_workspace_affinity(digest, normalized_root):
            return IngestResult(
                session_id=selected.session_id,
                source=discovered_source,
                facts_written=0,
                already_ingested=False,
                outcome=OUTCOME_NO_AFFINITY,
                memory_item_ids=[],
                reason="Session has no workspace affinity (no matching file paths)",
            )

        facts = await self._synthesize(digest=digest, client=client)
        memory_item_ids = await self._write_facts(
            facts=facts,
            session_id=selected.session_id,
            source=discovered_source,
            workspace_root=normalized_root,
        )
        await self._mark_ingested(
            source=discovered_source,
            session_id=selected.session_id,
            facts_count=len(memory_item_ids),
            transcript_path=str(selected.transcript_path),
            workspace_root=normalized_root,
        )

        return IngestResult(
            session_id=selected.session_id,
            source=discovered_source,
            facts_written=len(memory_item_ids),
            already_ingested=False,
            outcome=OUTCOME_INGESTED,
            memory_item_ids=memory_item_ids,
        )

    async def _resolve_auto_uningested(
        self,
        *,
        session_id: str,
        workspace_path: Path,
        normalized_root: str,
    ) -> tuple[str, SessionDiscovery] | None | str:
        """Return (source, discovery) on match, a reason string on failure, or None (legacy)."""
        candidates: list[tuple[str, SessionDiscovery]] = []
        for src, parser in PARSERS.items():
            for item in parser.discover_sessions(workspace_path):
                candidates.append((src, item))
        if not candidates:
            return "No session transcripts found for any supported source"
        candidates.sort(key=lambda pair: pair[1].discovered_at_ts, reverse=True)

        if session_id != "latest":
            found = False
            for src, item in candidates:
                if item.session_id == session_id:
                    found = True
                    if not await self._check_already_ingested(
                        source=src, session_id=item.session_id, workspace_root=normalized_root
                    ):
                        return src, item
            if found:
                return "Session already ingested"
            return f"Requested session '{session_id}' not found in discovered sessions"

        all_ingested = 0
        non_affine = 0
        for src, item in candidates:
            if await self._check_already_ingested(
                source=src, session_id=item.session_id, workspace_root=normalized_root
            ):
                all_ingested += 1
                continue
            parser = PARSERS[src]
            digest = parser.parse(item.transcript_path, item.session_id)
            if not self._has_workspace_affinity(digest, normalized_root):
                non_affine += 1
                continue
            return src, item

        if non_affine and not all_ingested:
            return "No sessions matched this workspace (no file path overlap)"
        if non_affine:
            return "All discovered sessions already ingested or not matching this workspace"
        return "All discovered sessions already ingested"

    def _resolve_target_session(
        self,
        *,
        source: str,
        session_id: str,
        workspace_path: Path,
    ) -> tuple[str, SessionDiscovery]:
        parser = PARSERS.get(source)
        if parser is None:
            allowed = ", ".join(sorted([*PARSERS.keys(), "auto"]))
            raise ValueError(f"Unsupported source '{source}'. Allowed: {allowed}")

        found = parser.discover_sessions(workspace_path)
        if not found:
            raise ValueError(f"No session transcripts found for source '{source}'")

        if session_id == "latest":
            return source, found[0]

        for item in found:
            if item.session_id == session_id:
                return source, item
        raise ValueError(f"Session '{session_id}' not found for source '{source}'")

    @staticmethod
    def _has_workspace_affinity(digest: SessionDigest, workspace_root: str) -> bool:
        """Check if digest file paths plausibly belong to the target workspace."""
        if digest.source == "claude_code":
            return True
        all_paths = digest.files_read + digest.files_modified
        if not all_paths:
            return False
        ws_lower = workspace_root.replace("\\", "/").rstrip("/").lower() + "/"
        for file_path in all_paths:
            normalized = file_path.replace("\\", "/").lower()
            if normalized.startswith(ws_lower):
                return True
        return False

    async def _synthesize(self, *, digest: SessionDigest, client: BaseLLMClient) -> list[str]:
        prompt = self._digest_to_prompt(digest)
        try:
            response = await client.complete(SESSION_INGEST_SYSTEM, prompt, max_tokens=400)
            payload = _safe_json_loads(response.content)
            if not isinstance(payload, dict):
                # Attempt to recover from fenced markdown.
                match = re.search(r"\{[\s\S]*\}", response.content)
                payload = _safe_json_loads(match.group(0)) if match else None
            if not isinstance(payload, dict):
                return []
            facts = payload.get("facts", [])
            if not isinstance(facts, list):
                return []
            return [str(item).strip() for item in facts if str(item).strip()][:8]
        except Exception:
            return []

    def _digest_to_prompt(self, digest: SessionDigest) -> str:
        payload = asdict(digest)
        return json.dumps(payload, indent=2, ensure_ascii=True)

    async def _write_facts(
        self,
        *,
        facts: list[str],
        session_id: str,
        source: str,
        workspace_root: str,
    ) -> list[str]:
        conn = await self._db.connect()
        inserted_ids: list[str] = []
        tags_json = json.dumps({"session_id": session_id, "source": source})

        for fact in facts:
            fact = " ".join(fact.split())
            if not fact:
                continue
            safe, _reason = check_writeback_safety(fact)
            if not safe:
                continue

            memory_id = uuid.uuid4().hex
            await conn.execute(
                """
                INSERT OR IGNORE INTO memory_items
                (
                    id, type, title, body, source, source_path, source_hash,
                    trust_level, tags_json, stale, memory_class, memory_status,
                    visibility_scope, reusable, review_required, workspace_root,
                    created_at, updated_at
                )
                VALUES
                (
                    ?, 'learned', ?, ?, ?, NULL, NULL,
                    2, ?, 0, 'fact', 'pending_review',
                    'workspace', 1, 1, ?,
                    datetime('now'), datetime('now')
                )
                """,
                (
                    memory_id,
                    fact[:80],
                    fact,
                    f"session_ingest:{source}",
                    tags_json,
                    workspace_root,
                ),
            )
            inserted_ids.append(memory_id)

        await conn.commit()
        return inserted_ids

    async def _check_already_ingested(
        self,
        *,
        source: str,
        session_id: str,
        workspace_root: str,
    ) -> bool:
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT 1
            FROM ingested_sessions
            WHERE source = ? AND session_id = ? AND workspace_root = ?
            LIMIT 1
            """,
            (source, session_id, workspace_root),
        )
        row = await cursor.fetchone()
        return row is not None

    async def _mark_ingested(
        self,
        *,
        source: str,
        session_id: str,
        facts_count: int,
        transcript_path: str,
        workspace_root: str,
    ) -> None:
        conn = await self._db.connect()
        await conn.execute(
            """
            INSERT OR REPLACE INTO ingested_sessions
            (source, session_id, workspace_root, transcript_path, facts_count, ingested_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            (source, session_id, workspace_root, transcript_path, facts_count),
        )
        await conn.commit()


async def _cli_main() -> None:
    import argparse
    import logging

    from .config import load_config
    from .db import DatabaseManager
    from .migration_runner import run_migrations

    parser = argparse.ArgumentParser(
        description="Ingest AI coding session transcripts into MemoPilot memory.",
    )
    parser.add_argument(
        "--source",
        default="auto",
        choices=[*sorted(PARSERS.keys()), "auto"],
        help="Session source (default: auto-detect newest)",
    )
    parser.add_argument(
        "--session-id",
        default="latest",
        help="Session UUID or 'latest' (default: latest)",
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help="Workspace root path (default: current directory)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_sessions",
        help="List available un-ingested sessions and exit",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if args.workspace:
        os.environ.setdefault("MEMOPILOT_WORKSPACE", args.workspace)
    config = load_config()

    db = DatabaseManager(config.memopilot_dir / "agent.db")
    conn = await db.connect()
    await run_migrations(conn)

    service = SessionIngestService(config=config, db=db)

    if args.list_sessions:
        available = await service.list_available_sessions(config.workspace_path)
        if not available:
            print("No un-ingested sessions found.")
        else:
            for src, sessions in available.items():
                print(f"\n{src}:")
                for s in sessions[:5]:
                    print(f"  {s['session_id']}")
        await db.close()
        return

    llm_client = _build_cli_llm_client()
    if llm_client is None:
        print("ERROR: No LLM client configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY.")
        await db.close()
        return

    try:
        result = await service.ingest_session(
            source=args.source,
            session_id=args.session_id,
            client=llm_client,
            workspace_root=args.workspace,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}")
        await db.close()
        return

    if result.already_ingested:
        print(f"No new sessions to ingest (source={result.source}).")
    else:
        print(f"Ingested session {result.session_id} from {result.source}.")
        print(f"  Facts written: {result.facts_written} (pending review)")
        for mid in result.memory_item_ids:
            print(f"  - {mid}")

    await db.close()


def _build_cli_llm_client() -> "BaseLLMClient | None":
    from .llm_client import AnthropicClient, OpenAIClient
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return AnthropicClient(key, os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5"))
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return OpenAIClient(key, os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
    return None


if __name__ == "__main__":
    import asyncio
    asyncio.run(_cli_main())
