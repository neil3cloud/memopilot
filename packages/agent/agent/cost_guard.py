"""Cost guard, budget tracking, savings reporting, and budget profiles."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

import yaml

from .config import Config
from .db import DatabaseManager

BUDGET_WARNING_THRESHOLD = 0.80
BUDGET_APPROVAL_THRESHOLD = 0.90
FRONTIER_BASELINE_COST_USD = 0.018


@dataclass(frozen=True)
class BudgetStatus:
    monthly_budget_usd: float
    spent_usd: float
    saved_usd: float
    remaining_usd: float
    warning_threshold_usd: float
    warning_triggered: bool
    blocked: bool
    spend_ratio: float
    pct_used: float = 0.0
    at_limit: bool = False
    warning_threshold: float = BUDGET_WARNING_THRESHOLD
    at_warning: bool = False
    last_updated_at: str | None = None


@dataclass(frozen=True)
class BudgetCheck:
    allowed: bool
    reason: str
    estimated_cost_usd: float
    status: BudgetStatus


@dataclass(frozen=True)
class SavingsReport:
    actual_cost: float
    hypothetical_frontier_cost: float
    savings: float
    reduction_pct: float
    total_tasks: int
    local_tasks: int
    cheap_cloud_tasks: int
    frontier_tasks: int
    month_cache_hits: int = 0
    month_total_ai_calls: int = 0
    cache_hit_rate: float = 0.0
    cache_savings_usd: float = 0.0
    month_spend_usd: float = 0.0
    month_net_usd: float = 0.0


@dataclass(frozen=True)
class BudgetProfileResult:
    active_profile: str
    monthly_budget_usd: float
    effective_budget_usd: float
    multiplier: float
    profiles: dict[str, float]


class CostGuardService:
    """Handles budget checks, spend tracking, savings reports, and profile enforcement."""

    def __init__(self, *, config: Config, db: DatabaseManager) -> None:
        self._config = config
        self._db = db
        self._profile_multipliers = {
            "strict_local": 1.0,
            "enterprise_privacy": 1.0,
            "cost_saver": 0.7,
            "balanced": 1.0,
            "frontier": 1.5,
        }

    async def get_budget_status(self) -> BudgetStatus:
        spent_usd = await self._sum_ledger("spend")
        saved_usd = await self._sum_ledger("save")
        base_budget = max(self._config.monthly_budget_usd, 0.0)
        multiplier = self._profile_multipliers.get(self._active_budget_profile(), 1.0)
        monthly_budget = max(base_budget * multiplier, 0.0)
        remaining = max(monthly_budget - spent_usd, 0.0)
        warning_threshold_usd = round(monthly_budget * BUDGET_WARNING_THRESHOLD, 4)
        pct_used = (
            (spent_usd / monthly_budget) if monthly_budget > 0 else 1.0 if spent_usd > 0 else 0.0
        )
        at_limit = monthly_budget == 0.0 or spent_usd >= monthly_budget
        at_warning = spent_usd >= warning_threshold_usd if monthly_budget > 0 else spent_usd > 0
        last_updated_at = await self._last_cost_update_at()
        return BudgetStatus(
            monthly_budget_usd=monthly_budget,
            spent_usd=spent_usd,
            saved_usd=saved_usd,
            remaining_usd=remaining,
            warning_threshold_usd=warning_threshold_usd,
            warning_triggered=at_warning,
            blocked=at_limit,
            spend_ratio=round(pct_used, 4),
            pct_used=round(pct_used, 4),
            at_limit=at_limit,
            warning_threshold=BUDGET_WARNING_THRESHOLD,
            at_warning=at_warning,
            last_updated_at=last_updated_at,
        )

    async def check_budget(self, estimated_cost_usd: float) -> BudgetCheck:
        status = await self.get_budget_status()
        estimated_cost_usd = max(estimated_cost_usd, 0.0)
        allowed = estimated_cost_usd <= status.remaining_usd
        reason = "within_budget"
        if not allowed:
            reason = "monthly_budget_exceeded"
        elif estimated_cost_usd > 0 and status.warning_triggered:
            reason = "monthly_budget_warning"
        return BudgetCheck(
            allowed=allowed,
            reason=reason,
            estimated_cost_usd=estimated_cost_usd,
            status=status,
        )

    async def create_task_run(
        self,
        *,
        user_request: str,
        task_type: str | None,
        mode: str | None,
        risk_level: str | None,
        selected_model: str | None,
        estimated_cost: float | None,
        workspace_root: str | None = None,
    ) -> str:
        task_run_id = uuid.uuid4().hex
        conn = await self._db.connect()
        # Some schema lineages can expose task_runs with an FK to
        # investigation_sessions before the referenced table exists.
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS investigation_sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                mode TEXT NOT NULL DEFAULT 'investigation',
                status TEXT NOT NULL DEFAULT 'open',
                workspace_root TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        await conn.execute(
            """
            INSERT INTO task_runs
            (
                id, user_request, task_type, mode, risk_level,
                selected_model, estimated_cost, status, workspace_root
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?)
            """,
            (
                task_run_id,
                user_request,
                task_type,
                mode,
                risk_level,
                selected_model,
                estimated_cost,
                workspace_root,
            ),
        )
        await conn.commit()
        return task_run_id

    async def record_ai_call(
        self,
        *,
        task_run_id: str,
        provider: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        estimated_cost: float | None,
        actual_cost: float | None,
        cache_hit: bool,
        context_pack_hash: str | None,
        purpose: str | None,
    ) -> str:
        conn = await self._db.connect()

        exists_cursor = await conn.execute("SELECT 1 FROM task_runs WHERE id = ?", (task_run_id,))
        if await exists_cursor.fetchone() is None:
            raise ValueError(f"Task run not found: {task_run_id}")

        ai_call_id = uuid.uuid4().hex
        hypothetical_frontier_cost = estimate_hypothetical_frontier_cost(
            provider=provider,
            model=model,
            estimated_cost=estimated_cost,
            actual_cost=actual_cost,
        )
        await conn.execute(
            """
            INSERT INTO ai_calls
            (
                id, task_run_id, provider, model, input_tokens, output_tokens,
                estimated_cost, actual_cost, cache_hit, context_pack_hash, purpose,
                hypothetical_frontier_cost
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ai_call_id,
                task_run_id,
                provider,
                model,
                input_tokens,
                output_tokens,
                estimated_cost,
                actual_cost,
                1 if cache_hit else 0,
                context_pack_hash,
                purpose,
                hypothetical_frontier_cost,
            ),
        )

        amount = actual_cost if actual_cost is not None else estimated_cost
        if amount is not None and amount > 0:
            if cache_hit:
                await self._insert_ledger("save", amount, "ai_call_cache_hit", ai_call_id)
            else:
                await self._insert_ledger("spend", amount, "ai_call", ai_call_id)

        await conn.execute(
            """
            UPDATE task_runs
            SET
                actual_cost = COALESCE(actual_cost, 0) + ?,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (0.0 if cache_hit else (amount or 0.0), task_run_id),
        )
        await conn.commit()
        return ai_call_id

    async def get_savings_report(
        self,
        *,
        start_date: date | datetime | str | None = None,
        end_date: date | datetime | str | None = None,
    ) -> SavingsReport:
        if start_date is None or end_date is None:
            default_start, default_end = current_month_range()
            start_date = start_date or default_start
            end_date = end_date or default_end
        return await calculate_savings(start_date, end_date, self._db)

    async def add_cache_savings(self, *, amount_usd: float, reference_id: str) -> None:
        if amount_usd <= 0:
            return
        conn = await self._db.connect()
        await self._insert_ledger("save", amount_usd, "response_cache_hit", reference_id)
        await conn.commit()

    async def _sum_ledger(self, entry_type: str) -> float:
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT SUM(amount) AS total
            FROM cost_ledger
            WHERE entry_type = ?
              AND created_at >= datetime('now', 'start of month')
            """,
            (entry_type,),
        )
        row = await cursor.fetchone()
        return float(row["total"] or 0.0)

    async def _insert_ledger(
        self,
        entry_type: str,
        amount: float,
        source: str,
        reference_id: str,
    ) -> None:
        conn = await self._db.connect()
        await conn.execute(
            """
            INSERT INTO cost_ledger (id, entry_type, amount, source, reference_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (uuid.uuid4().hex, entry_type, amount, source, reference_id),
        )

    async def _last_cost_update_at(self) -> str | None:
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT MAX(ts) AS last_updated_at
            FROM (
                SELECT MAX(created_at) AS ts
                FROM cost_ledger
                WHERE created_at >= datetime('now', 'start of month')
                UNION ALL
                SELECT MAX(created_at) AS ts
                FROM ai_calls
                WHERE created_at >= datetime('now', 'start of month')
            )
            """
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return row["last_updated_at"]

    # ------------------------------------------------------------------
    # Budget Profiles (v1.5)
    # ------------------------------------------------------------------

    async def get_budget_profiles(self) -> BudgetProfileResult:
        active = self._active_budget_profile()
        multiplier = self._profile_multipliers.get(active, 1.0)
        monthly = max(self._config.monthly_budget_usd, 0.0)
        return BudgetProfileResult(
            active_profile=active,
            monthly_budget_usd=monthly,
            effective_budget_usd=round(monthly * multiplier, 4),
            multiplier=multiplier,
            profiles=self._profile_multipliers.copy(),
        )

    async def set_budget_profile(self, profile: str) -> BudgetProfileResult:
        if profile not in self._profile_multipliers:
            raise ValueError(f"Unknown profile: {profile}")
        settings = self._read_workspace_settings()
        budget = settings.get("budget")
        if not isinstance(budget, dict):
            budget = {}
        budget["profile"] = profile
        settings["budget"] = budget
        budget_settings_path = self._config.memopilot_dir / "settings.yaml"
        budget_settings_path.parent.mkdir(parents=True, exist_ok=True)
        budget_settings_path.write_text(
            yaml.safe_dump(settings, sort_keys=False),
            encoding="utf-8",
        )
        self._config.budget_profile = profile
        return await self.get_budget_profiles()

    def _active_budget_profile(self) -> str:
        settings = self._read_workspace_settings()
        budget = settings.get("budget")
        if isinstance(budget, dict):
            profile = budget.get("profile")
            if isinstance(profile, str) and profile in self._profile_multipliers:
                return profile
        if self._config.budget_profile in self._profile_multipliers:
            return self._config.budget_profile
        return "balanced"

    def _read_workspace_settings(self) -> dict:
        budget_settings_path = self._config.memopilot_dir / "settings.yaml"
        if not budget_settings_path.exists():
            return {}
        loaded = yaml.safe_load(budget_settings_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return loaded
        return {}

async def calculate_savings(
    start_date: date | datetime | str,
    end_date: date | datetime | str,
    db: DatabaseManager,
) -> SavingsReport:
    start_sql = normalize_sqlite_range_value(start_date)
    end_sql = normalize_sqlite_range_value(end_date, end=True)
    conn = await db.connect()

    cursor = await conn.execute(
        """
        SELECT provider, model, estimated_cost, actual_cost, hypothetical_frontier_cost, cache_hit
        FROM ai_calls
        WHERE datetime(created_at) >= datetime(?)
          AND datetime(created_at) < datetime(?)
        """,
        (start_sql, end_sql),
    )
    rows = await cursor.fetchall()

    total_tasks = 0
    local_tasks = 0
    cheap_cloud_tasks = 0
    frontier_tasks = 0
    cache_hits = 0
    actual_cost_total = 0.0
    hypothetical_total = 0.0

    for row in rows:
        total_tasks += 1
        provider = row["provider"]
        model = row["model"]
        tier = infer_selected_tier(provider=provider, model=model)
        if tier == "local":
            local_tasks += 1
        elif tier == "frontier":
            frontier_tasks += 1
        else:
            cheap_cloud_tasks += 1

        if int(row["cache_hit"] or 0) == 1:
            cache_hits += 1

        actual_cost = coalesce_cost(row["actual_cost"], row["estimated_cost"])
        hypothetical_cost = row["hypothetical_frontier_cost"]
        if hypothetical_cost is None:
            hypothetical_cost = estimate_hypothetical_frontier_cost(
                provider=provider,
                model=model,
                estimated_cost=row["estimated_cost"],
                actual_cost=row["actual_cost"],
            )

        actual_cost_total += actual_cost
        hypothetical_total += float(hypothetical_cost or 0.0)

    ledger_cursor = await conn.execute(
        """
        SELECT
            SUM(CASE WHEN entry_type = 'save' THEN amount ELSE 0 END) AS saved_total,
            SUM(CASE WHEN entry_type = 'spend' THEN amount ELSE 0 END) AS spend_total
        FROM cost_ledger
        WHERE datetime(created_at) >= datetime(?)
          AND datetime(created_at) < datetime(?)
        """,
        (start_sql, end_sql),
    )
    ledger_row = await ledger_cursor.fetchone()
    saved_total = float(ledger_row["saved_total"] or 0.0) if ledger_row is not None else 0.0
    spend_total = float(ledger_row["spend_total"] or 0.0) if ledger_row is not None else 0.0

    savings = max(hypothetical_total - actual_cost_total, 0.0)
    reduction_pct = (savings / hypothetical_total) if hypothetical_total > 0 else 0.0
    cache_hit_rate = (cache_hits / total_tasks) if total_tasks > 0 else 0.0

    return SavingsReport(
        actual_cost=round(actual_cost_total, 4),
        hypothetical_frontier_cost=round(hypothetical_total, 4),
        savings=round(savings, 4),
        reduction_pct=round(reduction_pct, 4),
        total_tasks=total_tasks,
        local_tasks=local_tasks,
        cheap_cloud_tasks=cheap_cloud_tasks,
        frontier_tasks=frontier_tasks,
        month_cache_hits=cache_hits,
        month_total_ai_calls=total_tasks,
        cache_hit_rate=round(cache_hit_rate, 4),
        cache_savings_usd=round(saved_total, 4),
        month_spend_usd=round(spend_total, 4),
        month_net_usd=round(spend_total - saved_total, 4),
    )


def current_month_range() -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start, now + timedelta(seconds=1)


def infer_selected_tier(
    *,
    provider: str | None,
    model: str | None,
    privacy_level: str | None = None,
    requires_approval: bool = False,
) -> str:
    normalized_provider = (provider or "").strip().lower()
    normalized_model = (model or "").strip().lower()
    normalized_privacy = (privacy_level or "").strip().lower()

    if normalized_privacy == "local" or normalized_provider in {"ollama", "local", "llama.cpp"}:
        return "local"
    if requires_approval:
        return "frontier"
    if any(local_marker in normalized_model for local_marker in ("llama", "mistral", "ollama")):
        return "local"
    if "mini" in normalized_model or "flash" in normalized_model:
        return "cheap_cloud"
    if any(
        marker in normalized_model
        for marker in ("gpt-4", "gpt-5", "claude", "sonnet", "opus", "frontier", "gemini-1.5-pro", "gemini-2.0-pro", "gemini-2.5-pro")
    ):
        return "frontier"
    return "cheap_cloud"


def estimate_hypothetical_frontier_cost(
    *,
    provider: str | None,
    model: str | None,
    estimated_cost: float | None,
    actual_cost: float | None,
    privacy_level: str | None = None,
    requires_approval: bool = False,
) -> float:
    actual_amount = coalesce_cost(actual_cost, estimated_cost)
    tier = infer_selected_tier(
        provider=provider,
        model=model,
        privacy_level=privacy_level,
        requires_approval=requires_approval,
    )
    if tier == "frontier":
        return round(max(actual_amount, FRONTIER_BASELINE_COST_USD), 4)
    return round(max(FRONTIER_BASELINE_COST_USD, actual_amount, estimated_cost or 0.0), 4)


def coalesce_cost(primary: float | None, fallback: float | None = None) -> float:
    if primary is not None:
        return float(primary)
    if fallback is not None:
        return float(fallback)
    return 0.0


def normalize_sqlite_range_value(value: date | datetime | str, *, end: bool = False) -> str:
    if isinstance(value, datetime):
        normalized = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return normalized.astimezone(UTC).replace(tzinfo=None).isoformat(sep=" ")
    if isinstance(value, date):
        target_date = value + timedelta(days=1) if end else value
        return datetime.combine(target_date, time.min).isoformat(sep=" ")

    text = value.strip()
    if len(text) == 10:
        parsed = date.fromisoformat(text)
        if end:
            parsed += timedelta(days=1)
        return datetime.combine(parsed, time.min).isoformat(sep=" ")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
        parsed_dt = datetime.fromisoformat(text)
        return parsed_dt.astimezone(UTC).replace(tzinfo=None).isoformat(sep=" ")
    return text.replace("T", " ")
