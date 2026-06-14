"""FastAPI routes for tool mode management (session tracking, caller approval)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/v1/tool-mode", tags=["tool-mode"])


class CallerActionRequest(BaseModel):
    caller: str


class CallerActionResponse(BaseModel):
    status: str
    caller: str


class ToolModeSessionSummary(BaseModel):
    caller: str
    display_name: str
    total_calls: int = 0
    total_tokens_returned: int = 0
    total_redacted_values: int = 0
    patch_reviews_triggered: int = 0
    writebacks_triggered: int = 0
    first_call_at: str | None = None
    active: bool = False


class PendingWritebacksSummary(BaseModel):
    count: int = 0
    task_run_ids: list[str] = Field(default_factory=list)
    oldest_pending_at: str | None = None


class SessionSummaryResponse(BaseModel):
    sessions: list[ToolModeSessionSummary]
    pending_writebacks: PendingWritebacksSummary


_approved_callers: set[str] = set()
_blocked_callers: set[str] = set()

CALLER_DISPLAY_NAMES = {
    "copilot_lm_tool": "Copilot Chat (LM Tool)",
    "cursor_mcp_tool": "Cursor Chat (MCP Tool)",
    "api": "External API",
}


def is_caller_approved(caller: str) -> bool:
    """Check if a caller has been approved for this session."""
    return caller in _approved_callers


def is_caller_blocked(caller: str) -> bool:
    """Check if a caller has been blocked for this session."""
    return caller in _blocked_callers


def is_first_tool_use(caller: str) -> bool:
    """Check if this is the first tool call from this caller."""
    return caller not in _approved_callers and caller not in _blocked_callers


def reset_caller_state() -> None:
    """Reset caller state (for testing)."""
    _approved_callers.clear()
    _blocked_callers.clear()


def create_tool_mode_routes(get_db):
    """Create tool mode router with database dependency."""

    @router.post("/approve-caller", response_model=CallerActionResponse)
    async def approve_caller(request: CallerActionRequest):
        _approved_callers.add(request.caller)
        _blocked_callers.discard(request.caller)
        return CallerActionResponse(status="approved", caller=request.caller)

    @router.post("/block-caller", response_model=CallerActionResponse)
    async def block_caller(request: CallerActionRequest):
        _blocked_callers.add(request.caller)
        _approved_callers.discard(request.caller)
        return CallerActionResponse(status="blocked", caller=request.caller)

    @router.get("/session-summary", response_model=SessionSummaryResponse)
    async def session_summary():
        db = await get_db()

        cursor = await db.execute(
            """SELECT
                   caller,
                   MIN(first_call_at) AS first_call_at,
                   MAX(last_call_at) AS last_call_at,
                   SUM(total_calls) AS total_calls,
                   SUM(total_context_tokens_returned) AS total_tokens_returned,
                   SUM(total_redacted_values) AS total_redacted_values,
                   MAX(active) AS active
               FROM tool_mode_sessions
               WHERE active = 1
               GROUP BY caller
               ORDER BY MAX(last_call_at) DESC"""
        )
        rows = await cursor.fetchall()

        sessions: list[ToolModeSessionSummary] = []
        seen_callers: set[str] = set()
        for row in rows:
            caller = row[0]
            seen_callers.add(caller)

            pr_cursor = await db.execute(
                """SELECT
                       COALESCE(SUM(tce.patch_review_triggered), 0),
                       COALESCE(SUM(tce.writeback_triggered), 0)
                   FROM tool_call_events AS tce
                   JOIN tool_mode_sessions AS tms ON tce.session_id = tms.id
                   WHERE tms.caller = ? AND tms.active = 1""",
                [caller],
            )
            pr_row = await pr_cursor.fetchone()

            sessions.append(
                ToolModeSessionSummary(
                    caller=caller,
                    display_name=CALLER_DISPLAY_NAMES.get(caller, caller),
                    total_calls=row[3] or 0,
                    total_tokens_returned=row[4] or 0,
                    total_redacted_values=row[5] or 0,
                    patch_reviews_triggered=pr_row[0] if pr_row else 0,
                    writebacks_triggered=pr_row[1] if pr_row else 0,
                    first_call_at=row[1],
                    active=bool(row[6]),
                )
            )

        for caller, display_name in CALLER_DISPLAY_NAMES.items():
            if caller not in seen_callers:
                sessions.append(
                    ToolModeSessionSummary(
                        caller=caller,
                        display_name=display_name,
                        active=False,
                    )
                )

        pending = PendingWritebacksSummary()
        try:
            wb_cursor = await db.execute(
                """SELECT id, created_at FROM task_runs
                   WHERE status = 'awaiting_writeback'
                   ORDER BY created_at ASC"""
            )
            wb_rows = await wb_cursor.fetchall()
            if wb_rows:
                pending.count = len(wb_rows)
                pending.task_run_ids = [row[0] for row in wb_rows]
                pending.oldest_pending_at = wb_rows[0][1]
        except Exception:
            pass

        return SessionSummaryResponse(sessions=sessions, pending_writebacks=pending)

    return router
