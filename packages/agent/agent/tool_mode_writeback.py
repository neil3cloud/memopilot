"""Tool Mode Memory Writeback — extracts memory proposals from tool-mode outcomes."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aiosqlite


@dataclass
class MemoryProposal:
    """A proposed memory item extracted from a writeback."""

    id: str
    title: str
    body: str
    memory_class: str
    memory_status: str
    trust_level: int
    source: str = ""
    reusable: bool = True


@dataclass
class WritebackResult:
    """Result of a writeback execution."""

    writeback_id: str
    task_run_id: str
    proposals: list[MemoryProposal]
    proposals_count: int
    blocked_content_count: int
    already_processed: bool = False
    rendered_summary: str = ""


PROPOSAL_BODY_BLOCKED_PATTERNS = [
    re.compile(r"^\+{3} .+"),
    re.compile(r"^-{3} .+"),
    re.compile(r"^\@\@ .+ \@\@"),
    re.compile(r"^[+-].{200,}"),
]

SECRET_PATTERNS = re.compile(
    r"(?:"
    r"(?:api[_-]?key|secret[_-]?key|password|token|auth[_-]?token)"
    r"\s*[=:]\s*['\"][^'\"]{8,}"
    r"|(?:-----BEGIN (?:RSA |EC )?PRIVATE KEY-----)"
    r"|(?:sk-[a-zA-Z0-9]{20,})"
    r"|(?:ghp_[a-zA-Z0-9]{36})"
    r"|(?:AKIA[A-Z0-9]{16})"
    r")",
    re.IGNORECASE,
)


def sanitize_for_proposal_body(text: str) -> str:
    """Remove raw diff markers and secrets from text intended for proposal bodies."""

    safe_lines = [
        line
        for line in text.splitlines()
        if not any(pattern.match(line) for pattern in PROPOSAL_BODY_BLOCKED_PATTERNS)
    ]
    result = "\n".join(safe_lines)
    return SECRET_PATTERNS.sub("[REDACTED]", result)


def compute_diff_hash(diff_text: str) -> str:
    """Compute SHA-256 hash of the diff for deduplication."""

    return hashlib.sha256(diff_text.encode("utf-8")).hexdigest()


def parse_diff_changed_files(diff_text: str) -> list[str]:
    """Extract file paths from a unified diff."""

    files: list[str] = []
    seen: set[str] = set()
    for line in diff_text.splitlines():
        file_path = ""
        if line.startswith("+++ b/"):
            file_path = line[6:]
        elif line.startswith("+++ ") and not line.startswith("+++ /dev/null"):
            file_path = line[4:]
        if file_path and file_path not in seen:
            seen.add(file_path)
            files.append(file_path)
    return files


def extract_modified_symbols(diff_text: str) -> list[dict[str, str]]:
    """Extract modified public symbols from a diff (simple heuristic)."""

    symbols: list[dict[str, str]] = []
    current_file = ""
    symbol_patterns = [
        re.compile(r"^\+\s*(?:def|async def)\s+([a-zA-Z_]\w*)\s*\("),
        re.compile(r"^\+\s*class\s+([A-Z]\w*)"),
        re.compile(r"^\+\s*(?:export\s+)?(?:function|const|class)\s+([a-zA-Z_]\w*)"),
    ]

    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
        elif line.startswith("+++ ") and not line.startswith("+++ /dev/null"):
            current_file = line[4:]
        elif line.startswith("+") and not line.startswith("+++"):
            for pattern in symbol_patterns:
                match = pattern.match(line)
                if match:
                    symbols.append(
                        {
                            "name": match.group(1),
                            "file": current_file,
                            "change_summary": "implementation added or modified",
                        }
                    )
                    break

    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for symbol in symbols:
        key = f"{symbol['file']}:{symbol['name']}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(symbol)
    return unique[:5]


def detect_test_files(changed_files: list[str]) -> list[str]:
    """Identify test files from the changed file list."""

    test_patterns = re.compile(r"(test_|_test\.|\.test\.|spec\.)", re.IGNORECASE)
    return [file_path for file_path in changed_files if test_patterns.search(file_path)]


async def execute_writeback(
    db: aiosqlite.Connection,
    *,
    outcome_summary: str,
    outcome_status: str,
    git_diff: str,
    workspace_root: str,
    caller: str,
    context_pack_hash: str | None = None,
) -> WritebackResult:
    """Execute the full writeback pipeline."""

    diff_hash = compute_diff_hash(git_diff)
    changed_files = parse_diff_changed_files(git_diff)

    cursor = await db.execute(
        """SELECT id, task_run_id FROM tool_mode_writebacks
           WHERE git_diff_hash = ? AND workspace_root = ? AND caller = ?
           ORDER BY created_at DESC LIMIT 1""",
        [diff_hash, workspace_root, caller],
    )
    existing = await cursor.fetchone()
    if existing:
        return WritebackResult(
            writeback_id=existing[0],
            task_run_id=existing[1],
            proposals=[],
            proposals_count=0,
            blocked_content_count=0,
            already_processed=True,
            rendered_summary=(
                "## MemoPilot Writeback — Already Processed\n\n"
                "This diff has already been recorded. "
                "Check the MemoPilot Memory Review Queue to review existing proposals."
            ),
        )

    task_run_id = await _resolve_task_run(db, workspace_root, context_pack_hash, caller)

    cursor = await db.execute(
        "SELECT id FROM tool_mode_writebacks WHERE git_diff_hash = ? AND task_run_id = ?",
        [diff_hash, task_run_id],
    )
    existing = await cursor.fetchone()
    if existing:
        return WritebackResult(
            writeback_id=existing[0],
            task_run_id=task_run_id,
            proposals=[],
            proposals_count=0,
            blocked_content_count=0,
            already_processed=True,
            rendered_summary=(
                "## MemoPilot Writeback — Already Processed\n\n"
                "This diff has already been recorded. "
                "Check the MemoPilot Memory Review Queue to review existing proposals."
            ),
        )

    blocked_count = 1 if SECRET_PATTERNS.search(git_diff) else 0
    proposals = _generate_proposals(
        outcome_summary=outcome_summary,
        outcome_status=outcome_status,
        git_diff=git_diff,
        changed_files=changed_files,
    )

    writeback_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()

    await db.execute(
        """INSERT INTO tool_mode_writebacks (
               id, task_run_id, workspace_root, caller, git_diff_hash,
               diff_files_json, proposals_count, blocked_content_count,
               outcome_summary, outcome_status, created_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            writeback_id,
            task_run_id,
            workspace_root,
            caller,
            diff_hash,
            json.dumps(changed_files),
            len(proposals),
            blocked_count,
            outcome_summary,
            outcome_status,
            now,
        ],
    )

    for proposal in proposals:
        await db.execute(
            """INSERT INTO memory_items (
                   id, type, title, body, source, source_path, source_hash,
                   trust_level, tags_json, stale, memory_class, memory_status,
                   visibility_scope, reusable, review_required, workspace_root,
                   writeback_id, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL, 0, ?, ?, 'workspace', ?, 1, ?, ?, ?, ?)""",
            [
                proposal.id,
                "tool_writeback",
                proposal.title,
                proposal.body,
                "tool_mode_writeback",
                proposal.source or None,
                proposal.trust_level,
                proposal.memory_class,
                proposal.memory_status,
                int(proposal.reusable),
                workspace_root,
                writeback_id,
                now,
                now,
            ],
        )

    await db.execute(
        """UPDATE task_runs
           SET status = 'completed_via_writeback',
               outcome_summary = ?,
               outcome_status = ?,
               writeback_id = ?,
               updated_at = ?
           WHERE id = ?""",
        [outcome_summary, outcome_status, writeback_id, now, task_run_id],
    )
    await db.commit()

    rendered_summary = _render_writeback_summary(
        outcome_summary=outcome_summary,
        outcome_status=outcome_status,
        changed_files=changed_files,
        proposals=proposals,
    )
    return WritebackResult(
        writeback_id=writeback_id,
        task_run_id=task_run_id,
        proposals=proposals,
        proposals_count=len(proposals),
        blocked_content_count=blocked_count,
        already_processed=False,
        rendered_summary=rendered_summary,
    )


async def dismiss_writeback(db: aiosqlite.Connection, task_run_id: str) -> None:
    """Mark a task_run as writeback_dismissed."""

    now = datetime.now(UTC).isoformat()
    await db.execute(
        """UPDATE task_runs
           SET status = 'writeback_dismissed', writeback_dismissed = 1, updated_at = ?
           WHERE id = ?""",
        [now, task_run_id],
    )
    await db.commit()


async def get_pending_writebacks(
    db: aiosqlite.Connection, workspace_root: str
) -> list[dict[str, Any]]:
    """List task_runs in awaiting_writeback status."""

    if workspace_root:
        cursor = await db.execute(
            """SELECT id, created_at, source
               FROM task_runs
               WHERE workspace_root = ? AND status = 'awaiting_writeback'
               ORDER BY created_at DESC""",
            [workspace_root],
        )
    else:
        cursor = await db.execute(
            """SELECT id, created_at, source
               FROM task_runs
               WHERE status = 'awaiting_writeback'
               ORDER BY created_at DESC"""
        )
    rows = await cursor.fetchall()
    return [{"id": row[0], "created_at": row[1], "source": row[2]} for row in rows]


async def _resolve_task_run(
    db: aiosqlite.Connection,
    workspace_root: str,
    context_pack_hash: str | None,
    caller: str,
) -> str:
    """Find the most recent awaiting_writeback task_run, or create one."""

    if context_pack_hash:
        cursor = await db.execute(
            """SELECT tr.id FROM task_runs AS tr
               JOIN context_pack_versions AS cpv ON cpv.task_run_id = tr.id
               WHERE cpv.pack_hash = ? AND tr.status = 'awaiting_writeback'
               ORDER BY tr.created_at DESC LIMIT 1""",
            [context_pack_hash],
        )
        row = await cursor.fetchone()
        if row:
            return row[0]

    cursor = await db.execute(
        """SELECT id FROM task_runs
           WHERE workspace_root = ? AND status = 'awaiting_writeback'
             AND source IN ('copilot_lm_tool', 'cursor_mcp_tool')
           ORDER BY created_at DESC LIMIT 1""",
        [workspace_root],
    )
    row = await cursor.fetchone()
    if row:
        return row[0]

    task_run_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    await db.execute(
        """INSERT INTO task_runs (
               id, user_request, task_type, status, mode,
               workspace_root, source, patch_governance_available,
               created_at, updated_at
           ) VALUES (?, ?, 'writeback', 'awaiting_writeback', 'tool_writeback', ?, ?, 0, ?, ?)""",
        [task_run_id, f"Tool-mode writeback ({caller})", workspace_root, caller, now, now],
    )
    await db.commit()
    return task_run_id


def _generate_proposals(
    *,
    outcome_summary: str,
    outcome_status: str,
    git_diff: str,
    changed_files: list[str],
) -> list[MemoryProposal]:
    """Generate memory proposals from the writeback data. Capped at 10 total."""

    proposals: list[MemoryProposal] = []
    files_summary = ", ".join(changed_files[:5]) or "none"
    if len(changed_files) > 5:
        files_summary += f" (+{len(changed_files) - 5} more)"

    proposals.append(
        MemoryProposal(
            id=str(uuid.uuid4()),
            title=outcome_summary,
            body=sanitize_for_proposal_body(
                f"Outcome: {outcome_summary}\n"
                f"Status: {outcome_status}\n"
                f"Files changed: {files_summary}\n"
                "Source: tool-mode task (patch applied by host AI, not MemoPilot)."
            ),
            memory_class="fact",
            memory_status="pending_review",
            trust_level=3,
            reusable=outcome_status == "success",
        )
    )

    for symbol in extract_modified_symbols(git_diff):
        proposals.append(
            MemoryProposal(
                id=str(uuid.uuid4()),
                title=f"{symbol['name']} — {symbol['change_summary']}",
                body=sanitize_for_proposal_body(
                    f"Task '{outcome_summary}' modified {symbol['name']}: "
                    f"{symbol['change_summary']}\nFile: {symbol['file']}"
                ),
                memory_class="fact",
                memory_status="pending_review",
                trust_level=4,
                source=symbol["file"],
                reusable=True,
            )
        )

    test_files = detect_test_files(changed_files)
    if test_files and outcome_status == "success":
        proposals.append(
            MemoryProposal(
                id=str(uuid.uuid4()),
                title=f"Tests added/updated: {outcome_summary}",
                body=sanitize_for_proposal_body(
                    f"Task '{outcome_summary}' added or modified tests:\n"
                    + "\n".join(f"- {name}" for name in test_files[:3])
                ),
                memory_class="fact",
                memory_status="pending_review",
                trust_level=4,
                reusable=True,
            )
        )

    if outcome_status == "reverted":
        for proposal in proposals:
            proposal.reusable = False

    return proposals[:10]


def _render_writeback_summary(
    *,
    outcome_summary: str,
    outcome_status: str,
    changed_files: list[str],
    proposals: list[MemoryProposal],
) -> str:
    """Render the writeback result as Markdown for the host AI."""

    files_str = ", ".join(changed_files[:5]) or "none"
    if len(changed_files) > 5:
        files_str += f" (+{len(changed_files) - 5} more)"

    lines = [
        "## MemoPilot Memory Update — Recorded\n",
        f"**Task:** {outcome_summary}",
        f"**Status:** {outcome_status}",
        f"**Files changed:** {files_str}\n",
        f"**{len(proposals)} memory proposals created and queued for developer review:**\n",
    ]
    for index, proposal in enumerate(proposals, 1):
        reusable_note = "" if proposal.reusable else " (non-reusable)"
        lines.append(
            f"{index}. [{proposal.memory_class} / {proposal.memory_status}] \"{proposal.title}\"{reusable_note}"
        )
        lines.append(f"   → Trust level: {proposal.trust_level}.\n")

    lines.append(
        "**Developer action required:** Open the MemoPilot Memory Review Queue to approve, "
        "edit, or reject these proposals.\n\n"
        "_Nothing has been automatically added to confirmed memory._"
    )
    return "\n".join(lines)
