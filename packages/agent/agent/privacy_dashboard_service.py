"""Privacy boundary dashboard summary service."""

from __future__ import annotations

from dataclasses import dataclass

from .db import DatabaseManager


@dataclass(frozen=True)
class RecentCloudCall:
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    estimated_cost: float
    cache_hit: bool
    redacted_values: int


@dataclass(frozen=True)
class PrivacyDashboardSummary:
    local_only: list[str]
    may_leave_machine: list[str]
    never_sent: list[str]
    pre_call_approval_summary: str
    mcp_data_status: str
    recent_cloud_calls: list[RecentCloudCall]


class PrivacyDashboardService:
    """Produces dashboard payload matching Phase 14 privacy section."""

    def __init__(self, *, db: DatabaseManager) -> None:
        self._db = db

    async def get_summary(self) -> PrivacyDashboardSummary:
        conn = await self._db.connect()
        recent_calls = await self._recent_cloud_calls()
        pre_call_summary = await self._pre_call_summary()

        cursor = await conn.execute(
            """
            SELECT
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count,
                SUM(CASE WHEN status = 'blocked' THEN 1 ELSE 0 END) AS blocked_count
            FROM mcp_calls
            """
        )
        mcp = await cursor.fetchone()
        success_count = int(mcp["success_count"] or 0)
        blocked_count = int(mcp["blocked_count"] or 0)
        mcp_status = f"success={success_count}, blocked={blocked_count}"

        return PrivacyDashboardSummary(
            local_only=[
                "code index",
                "symbol memory",
                "rules",
                "validation results",
                "local embeddings",
            ],
            may_leave_machine=[
                "context pack sent to cloud provider",
                "MCP results included in AI request",
            ],
            never_sent=[
                ".env files",
                "secrets",
                "ignored files",
                "private keys",
                "credentials",
            ],
            pre_call_approval_summary=pre_call_summary,
            mcp_data_status=mcp_status,
            recent_cloud_calls=recent_calls,
        )

    async def _recent_cloud_calls(self) -> list[RecentCloudCall]:
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT provider, model, input_tokens, output_tokens, estimated_cost, cache_hit
            FROM ai_calls
            ORDER BY created_at DESC
            LIMIT 10
            """
        )
        rows = await cursor.fetchall()

        redacted_counts = await self._redacted_count_by_call()
        calls: list[RecentCloudCall] = []
        for idx, row in enumerate(rows):
            calls.append(
                RecentCloudCall(
                    provider=str(row["provider"]),
                    model=str(row["model"]),
                    input_tokens=int(row["input_tokens"] or 0),
                    output_tokens=int(row["output_tokens"] or 0),
                    estimated_cost=float(row["estimated_cost"] or 0.0),
                    cache_hit=bool(row["cache_hit"]),
                    redacted_values=redacted_counts[idx] if idx < len(redacted_counts) else 0,
                )
            )
        return calls

    async def _redacted_count_by_call(self) -> list[int]:
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT
                CASE
                    WHEN input_json LIKE '%[REDACTED%' THEN 1
                    ELSE 0
                END AS redacted
            FROM mcp_calls
            ORDER BY created_at DESC
            LIMIT 10
            """
        )
        rows = await cursor.fetchall()
        return [int(row["redacted"] or 0) for row in rows]

    async def _pre_call_summary(self) -> str:
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT selected_model, estimated_cost
            FROM task_runs
            ORDER BY updated_at DESC
            LIMIT 1
            """
        )
        row = await cursor.fetchone()
        if row is None:
            return "No pending approval summary available."
        selected_model = row["selected_model"] or "unknown-model"
        estimated_cost = float(row["estimated_cost"] or 0.0)
        return f"Next call model={selected_model}, estimated_cost=${estimated_cost:.4f}"
