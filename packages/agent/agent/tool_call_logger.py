"""Tool call audit logging for MemoPilot tool mode."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import aiosqlite


async def log_tool_call(
    db: aiosqlite.Connection,
    *,
    tool_name: str,
    caller: str,
    session_id: str,
    task_run_id: str | None = None,
    context_pack_hash: str | None = None,
    output_tokens: int = 0,
    stale_exclusion_count: int = 0,
    redacted_values: int = 0,
    patch_review_triggered: bool = False,
    writeback_triggered: bool = False,
) -> str:
    """Log a tool call to both audit_events and tool_call_events tables."""
    event_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()

    await db.execute(
        """INSERT INTO audit_events (id, event_type, actor, details_json, created_at)
           VALUES (?, 'tool_call', ?, ?, ?)""",
        [
            event_id,
            caller,
            json.dumps(
                {
                    "tool_name": tool_name,
                    "caller": caller,
                    "session_id": session_id,
                    "task_run_id": task_run_id,
                    "context_pack_hash": context_pack_hash,
                    "stale_exclusion_count": stale_exclusion_count,
                    "redacted_values": redacted_values,
                    "output_tokens": output_tokens,
                    "patch_governance_available": False,
                },
                ensure_ascii=False,
            ),
            now,
        ],
    )

    await db.execute(
        """INSERT INTO tool_call_events (
               id, session_id, task_run_id, tool_name, caller,
               context_pack_hash, output_tokens, stale_exclusion_count,
               redacted_values, patch_review_triggered, writeback_triggered,
               created_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            event_id,
            session_id,
            task_run_id,
            tool_name,
            caller,
            context_pack_hash,
            output_tokens,
            stale_exclusion_count,
            redacted_values,
            1 if patch_review_triggered else 0,
            1 if writeback_triggered else 0,
            now,
        ],
    )

    await db.commit()
    return event_id


async def get_or_create_tool_session(
    db: aiosqlite.Connection,
    *,
    caller: str,
    workspace_root: str,
) -> str:
    """Get or create a tool_mode_sessions record for this caller."""
    now = datetime.now(UTC).isoformat()

    cursor = await db.execute(
        """SELECT id FROM tool_mode_sessions
           WHERE caller = ? AND workspace_root = ? AND active = 1
           ORDER BY last_call_at DESC LIMIT 1""",
        [caller, workspace_root],
    )
    row = await cursor.fetchone()

    if row:
        session_id = row[0]
        await db.execute(
            """UPDATE tool_mode_sessions
               SET last_call_at = ?, total_calls = total_calls + 1
               WHERE id = ?""",
            [now, session_id],
        )
        await db.commit()
        return session_id

    session_id = str(uuid.uuid4())
    await db.execute(
        """INSERT INTO tool_mode_sessions (
               id, caller, workspace_root, first_call_at, last_call_at,
               total_calls, total_context_tokens_returned, total_redacted_values, active
           ) VALUES (?, ?, ?, ?, ?, 1, 0, 0, 1)""",
        [session_id, caller, workspace_root, now, now],
    )
    await db.commit()
    return session_id


async def update_session_stats(
    db: aiosqlite.Connection,
    *,
    session_id: str,
    tokens_returned: int = 0,
    redacted_values: int = 0,
) -> None:
    """Update cumulative stats on the tool mode session."""
    await db.execute(
        """UPDATE tool_mode_sessions
           SET total_context_tokens_returned = total_context_tokens_returned + ?,
               total_redacted_values = total_redacted_values + ?
           WHERE id = ?""",
        [tokens_returned, redacted_values, session_id],
    )
    await db.commit()
