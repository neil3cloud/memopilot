"""Agentic MCP loop with capped iterations and policy enforcement."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

from .config import Config
from .db import DatabaseManager
from .security_policy import CredentialRedactor, DatabaseWriteBlocker

MCPContext = Literal["pre_fetch", "patch_generation", "investigation"]

# Type alias for an MCP tool dispatcher (real or simulated)
# Receives (tool_name: str, arguments: dict[str, Any]) and returns text result
MCPDispatcher = Callable[[str, dict[str, Any]], Awaitable[str]]


@dataclass(frozen=True)
class ToolCall:
    tool_name: str
    input_data: Any


@dataclass(frozen=True)
class ToolCallResult:
    tool_name: str
    iteration: int
    status: str
    blocked_reason: str | None
    redacted_input_json: str
    redacted_count: int
    result_summary: str


@dataclass(frozen=True)
class AgenticRunResult:
    requested_iterations: int
    executed_iterations: int
    capped_at: int
    calls: list[ToolCallResult]


class MCPOrchestrator:
    """Executes MCP tool calls with credential redaction and DB write blocking."""

    def __init__(
        self,
        *,
        db: DatabaseManager,
        config: Config,
        redactor: CredentialRedactor | None = None,
        blocker: DatabaseWriteBlocker | None = None,
        dispatcher: MCPDispatcher | None = None,
    ) -> None:
        self._db = db
        self._config = config
        self._redactor = redactor or CredentialRedactor()
        self._blocker = blocker or DatabaseWriteBlocker()
        self._dispatcher = dispatcher

    async def run_agentic_loop(
        self,
        *,
        task_run_id: str,
        server_name: str,
        tool_calls: list[ToolCall],
        max_iterations: int,
        context: MCPContext = "patch_generation",
    ) -> AgenticRunResult:
        capped = self._resolve_iteration_cap(context=context, requested_iterations=max_iterations)
        requested = len(tool_calls)
        executable_calls = tool_calls[:capped]

        conn = await self._db.connect()
        cursor = await conn.execute("SELECT 1 FROM task_runs WHERE id = ?", (task_run_id,))
        if await cursor.fetchone() is None:
            raise ValueError(f"Task run not found: {task_run_id}")

        results: list[ToolCallResult] = []
        for idx, call in enumerate(executable_calls, start=1):
            raw_json = self._to_json(call.input_data)
            redacted = self._redactor.redact(raw_json)
            policy = self._blocker.check_payload(redacted.redacted_text)

            status = "blocked" if policy.blocked else "success"
            blocked_reason = policy.reason
            
            if policy.blocked:
                result_summary = "Blocked DB write statement in MCP payload."
            elif self._dispatcher is not None:
                # Execute real MCP call via dispatcher
                try:
                    result_summary = await self._dispatcher(call.tool_name, call.input_data)
                except Exception as e:
                    status = "error"
                    result_summary = f"MCP dispatch failed: {str(e)}"
            else:
                # Simulated call (backward compatible)
                result_summary = f"Simulated MCP call executed for tool '{call.tool_name}'."

            await conn.execute(
                """
                INSERT INTO mcp_calls
                (
                    id, task_run_id, server_name, tool_name, input_json,
                    result_summary, iteration, status, blocked_reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    task_run_id,
                    server_name,
                    call.tool_name,
                    redacted.redacted_text,
                    result_summary,
                    idx,
                    status,
                    blocked_reason,
                ),
            )

            results.append(
                ToolCallResult(
                    tool_name=call.tool_name,
                    iteration=idx,
                    status=status,
                    blocked_reason=blocked_reason,
                    redacted_input_json=redacted.redacted_text,
                    redacted_count=redacted.redacted_count,
                    result_summary=result_summary,
                )
            )

        await conn.commit()
        return AgenticRunResult(
            requested_iterations=requested,
            executed_iterations=len(results),
            capped_at=capped,
            calls=results,
        )

    def _resolve_iteration_cap(
        self,
        *,
        context: MCPContext,
        requested_iterations: int,
    ) -> int:
        context_caps: dict[MCPContext, int] = {
            "pre_fetch": self._config.mcp_cap_pre_fetch,
            "patch_generation": self._config.mcp_cap_patch_generation,
            "investigation": self._config.mcp_cap_investigation,
        }
        context_cap = context_caps[context]
        absolute_cap = max(int(self._config.mcp_hard_absolute_cap), 0)
        bounded_context_cap = min(max(int(context_cap), 0), absolute_cap)
        return min(max(requested_iterations, 0), bounded_context_cap)

    def _to_json(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=True, sort_keys=True)
