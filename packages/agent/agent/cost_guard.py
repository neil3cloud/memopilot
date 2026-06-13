"""Cost guard, budget tracking, savings reporting, and budget profiles."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import yaml

from .config import Config
from .db import DatabaseManager


@dataclass(frozen=True)
class BudgetStatus:
    monthly_budget_usd: float
    spent_usd: float
    saved_usd: float
    remaining_usd: float


@dataclass(frozen=True)
class BudgetCheck:
    allowed: bool
    reason: str
    estimated_cost_usd: float
    status: BudgetStatus


@dataclass(frozen=True)
class SavingsReport:
    month_cache_hits: int
    month_total_ai_calls: int
    cache_hit_rate: float
    cache_savings_usd: float
    month_spend_usd: float
    month_net_usd: float


@dataclass(frozen=True)
class BudgetProfileResult:
    active_profile: str
    monthly_budget_usd: float
    effective_budget_usd: float
    multiplier: float
    profiles: dict[str, float]


class CostGuardService:
    """Handles budget checks, spend tracking, and savings reports."""

    def __init__(self, *, config: Config, db: DatabaseManager) -> None:
        self._config = config
        self._db = db
        self._profile_multipliers = {
            "cost_saver": 0.7,
            "balanced": 1.0,
            "frontier": 1.5,
        }

    async def get_budget_status(self) -> BudgetStatus:
        spent_usd = await self._sum_ledger("spend")
        saved_usd = await self._sum_ledger("save")
        base_budget = max(self._config.monthly_budget_usd, 0.0)
        multiplier = self._profile_multipliers.get(self._config.budget_profile, 1.0)
        monthly_budget = max(base_budget * multiplier, 0.0)
        remaining = max(monthly_budget - spent_usd, 0.0)
        return BudgetStatus(
            monthly_budget_usd=monthly_budget,
            spent_usd=spent_usd,
            saved_usd=saved_usd,
            remaining_usd=remaining,
        )

    async def check_budget(self, estimated_cost_usd: float) -> BudgetCheck:
        status = await self.get_budget_status()
        estimated_cost_usd = max(estimated_cost_usd, 0.0)
        allowed = estimated_cost_usd <= status.remaining_usd
        reason = "within_budget" if allowed else "monthly_budget_exceeded"
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
    ) -> str:
        task_run_id = uuid.uuid4().hex
        conn = await self._db.connect()
        await conn.execute(
            """
            INSERT INTO task_runs
            (id, user_request, task_type, mode, risk_level, selected_model, estimated_cost, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'running')
            """,
            (
                task_run_id,
                user_request,
                task_type,
                mode,
                risk_level,
                selected_model,
                estimated_cost,
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
        await conn.execute(
            """
            INSERT INTO ai_calls
            (
                id, task_run_id, provider, model, input_tokens, output_tokens,
                estimated_cost, actual_cost, cache_hit, context_pack_hash, purpose
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    async def get_savings_report(self) -> SavingsReport:
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT
                COUNT(*) AS total_calls,
                SUM(CASE WHEN cache_hit = 1 THEN 1 ELSE 0 END) AS cache_hits
            FROM ai_calls
            WHERE created_at >= datetime('now', 'start of month')
            """
        )
        usage_row = await cursor.fetchone()

        total_calls = int(usage_row["total_calls"] or 0)
        cache_hits = int(usage_row["cache_hits"] or 0)
        hit_rate = (cache_hits / total_calls) if total_calls > 0 else 0.0
        spend = await self._sum_ledger("spend")
        savings = await self._sum_ledger("save")

        return SavingsReport(
            month_cache_hits=cache_hits,
            month_total_ai_calls=total_calls,
            cache_hit_rate=hit_rate,
            cache_savings_usd=savings,
            month_spend_usd=spend,
            month_net_usd=spend - savings,
        )

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
