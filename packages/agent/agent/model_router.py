"""Model routing helpers — outcome-aware tier selection and provider resolution."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

import aiosqlite

from .db import DatabaseManager
from .local_model_discovery import LocalModel, discover_all_local


# ---------------------------------------------------------------------------
# Tier enum and ordering (used by /v1/model/route endpoint)
# ---------------------------------------------------------------------------

class ModelTier(str, Enum):
    LOCAL = "local"
    CHEAP_CLOUD = "cheap_cloud"
    FRONTIER = "frontier"


TIER_ORDER = {
    ModelTier.LOCAL: 0,
    ModelTier.CHEAP_CLOUD: 1,
    ModelTier.FRONTIER: 2,
}

_FRONTIER_MODELS = {"claude-3.5-sonnet"}
_LOCAL_TASK_TYPES = {"auto", "fix", "refactor", "test"}
_FRONTIER_TASK_TYPES = {"architecture", "complex"}


@dataclass(frozen=True)
class RoutingDecision:
    """Outcome-aware routing decision used by the /v1/model/route endpoint."""
    tier: ModelTier
    reason: str
    base_tier: ModelTier
    escalation_source: str | None = None


def _normalize_file_path(file_path: str) -> str:
    return file_path.replace("\\", "/").lower()


def _classify_base_tier(task_type: str, model_max_tokens: int) -> ModelTier:
    normalized_task = (task_type or "auto").strip().lower().replace("-", "_")
    if model_max_tokens <= 32_000 and normalized_task in _LOCAL_TASK_TYPES:
        return ModelTier.LOCAL
    if normalized_task in _FRONTIER_TASK_TYPES or model_max_tokens > 128_000:
        return ModelTier.FRONTIER
    return ModelTier.CHEAP_CLOUD


async def get_outcome_routing_hint(
    task_type: str,
    files_in_context: list[str] | None,
    db_conn: aiosqlite.Connection,
    lookback_days: int = 30,
) -> tuple[ModelTier | None, str | None]:
    del task_type
    if not files_in_context:
        return None, None

    normalized_files = []
    for file_path in files_in_context:
        normalized = _normalize_file_path(file_path)
        if normalized and normalized not in normalized_files:
            normalized_files.append(normalized)

    for file_path in normalized_files:
        cursor = await db_conn.execute(
            """
            SELECT COUNT(DISTINCT tr.id) AS failure_count
            FROM task_runs AS tr
            LEFT JOIN patch_attempts AS pa ON pa.task_run_id = tr.id
            WHERE tr.status = 'failed'
              AND COALESCE(tr.selected_model, '') NOT IN ('claude-3.5-sonnet')
              AND datetime(tr.created_at) >= datetime('now', ?)
              AND EXISTS (
                  SELECT 1
                  FROM json_each(COALESCE(pa.files_changed_json, '[]'))
                  WHERE lower(replace(CAST(value AS TEXT), '\\', '/')) = ?
              )
            """,
            (f"-{lookback_days} days", file_path),
        )
        row = await cursor.fetchone()
        failure_count = int(row[0] or 0) if row else 0
        if failure_count >= 2:
            return (
                ModelTier.FRONTIER,
                (
                    f"Escalating to frontier because {file_path} has {failure_count} failed "
                    f"non-frontier attempts in the last {lookback_days} days."
                ),
            )

    return None, None


async def route_with_outcome(
    task_type: str,
    files_in_context: list[str] | None,
    model_max_tokens: int,
    db: DatabaseManager,
) -> RoutingDecision:
    base_tier = _classify_base_tier(task_type, model_max_tokens)
    normalized_task = (task_type or "auto").strip().lower().replace("-", "_")

    if base_tier == ModelTier.LOCAL:
        return RoutingDecision(
            tier=base_tier,
            base_tier=base_tier,
            reason=(
                f"Routing to local for task_type={normalized_task} because the request fits the "
                "32K local context window. Frontier escalation would only trigger after 2 failed "
                "non-frontier attempts on the same file within 30 days, and local routes are not "
                "escalated automatically."
            ),
        )

    if base_tier == ModelTier.FRONTIER:
        return RoutingDecision(
            tier=base_tier,
            base_tier=base_tier,
            reason=(
                f"Routing directly to frontier for task_type={normalized_task} because the task "
                "already exceeds the cheap-cloud lane. Frontier escalation would otherwise "
                "trigger after 2 failed non-frontier attempts on the same file within 30 days, "
                "and no higher escalation tier exists."
            ),
        )

    conn = await db.connect()
    hinted_tier, hinted_reason = await get_outcome_routing_hint(
        task_type=task_type,
        files_in_context=files_in_context,
        db_conn=conn,
    )
    if hinted_tier is not None and TIER_ORDER[hinted_tier] > TIER_ORDER[base_tier]:
        return RoutingDecision(
            tier=hinted_tier,
            base_tier=base_tier,
            escalation_source="recent_file_failures",
            reason=(
                f"{hinted_reason} Without repeated file failures, this request would stay on "
                "cheap_cloud."
            ),
        )

    return RoutingDecision(
        tier=base_tier,
        base_tier=base_tier,
        reason=(
            f"Routing to cheap_cloud for task_type={normalized_task} based on the current "
            "context budget. Frontier escalation would trigger after 2 failed non-frontier "
            "attempts on the same file within 30 days."
        ),
    )


# ---------------------------------------------------------------------------
# Phase-33: provider-level routing — resolves a concrete model+provider
# ---------------------------------------------------------------------------

_local_cache: list[LocalModel] = []
_local_cache_ts: float = 0.0
_LOCAL_TTL = 300.0  # 5 minutes


@dataclass
class ProviderDecision:
    """Concrete model/provider selection for generate_patch and MCP tools."""
    tier: str
    model_id: str | None
    provider: str | None
    reason: str
    cost_estimate_usd: float = 0.0


async def _get_local_models(config: dict) -> list[LocalModel]:
    global _local_cache, _local_cache_ts
    if time.monotonic() - _local_cache_ts > _LOCAL_TTL:
        _local_cache = await discover_all_local(config)
        _local_cache_ts = time.monotonic()
    return _local_cache


async def route_model(
    task_type: str,
    risk_level: str,
    ctx_tokens: int,
    config: dict,
) -> ProviderDecision:
    """Resolve a concrete model+provider for a task. Never raises."""
    tier = _classify_tier(task_type, risk_level)
    needed = int(ctx_tokens / 0.60)
    profile = config.get("budget_profile", "cost_saver")

    if tier == "no_ai":
        return ProviderDecision("no_ai", None, None, "No AI needed for this task type.")

    locals_ = await _get_local_models(config)
    capable = [m for m in locals_ if m.max_context_tokens >= needed]

    use_local = (tier == "local") or (
        tier == "standard" and profile in ("cost_saver", "strict_local") and capable
    )

    if use_local and capable:
        best = sorted(capable, key=lambda m: m.max_context_tokens)[0]
        return ProviderDecision(
            tier="local",
            model_id=best.model_id,
            provider=best.source,
            reason=f"{best.source} model: {best.model_id} ({best.max_context_tokens:,} ctx, $0.00)",
            cost_estimate_usd=0.0,
        )

    if profile == "strict_local":
        return ProviderDecision(
            "context_pack_only", None, None,
            "Strict local mode — no cloud calls. "
            "Install Ollama or pull a model: `ollama pull qwen2.5-coder:7b`",
        )

    if config.get("host_models_available"):
        host_list: list[dict] = config.get("host_model_list", [])
        capable_h = [m for m in host_list if m.get("max_context_tokens", 0) >= needed]
        if capable_h:
            best_h = capable_h[0]
            return ProviderDecision(
                tier=tier,
                model_id=best_h["model_id"],
                provider="host",
                reason=f"Copilot: {best_h['model_id']} (subscription model)",
                cost_estimate_usd=0.0,
            )

    cloud = _pick_cloud(tier, needed, config)
    if cloud:
        return cloud

    return ProviderDecision(
        "context_pack_only", None, None,
        "No model available. Configure Ollama or add API keys in .memopilot/config.yaml.",
    )


def _classify_tier(task_type: str, risk: str) -> str:
    if risk in ("critical", "high"):
        return "advanced"
    if task_type in ("security_change", "billing_change", "architecture", "complex_refactor"):
        return "advanced"
    if task_type in ("code_formatting", "import_sorting", "exact_search"):
        return "no_ai"
    if task_type in ("summarization", "classification", "explanation", "memory_generation"):
        return "local"
    return "standard"


_CLOUD_CATALOG: list[tuple[str, str, int, float, float, str]] = [
    # (provider, model_id, max_ctx, cost_in, cost_out, min_tier)
    ("anthropic", "claude-haiku-4-5", 200_000, 0.80, 4.00, "standard"),
    ("openai", "gpt-4o-mini", 128_000, 0.15, 0.60, "standard"),
    ("anthropic", "claude-sonnet-4-6", 200_000, 3.00, 15.00, "advanced"),
    ("openai", "gpt-4o", 128_000, 2.50, 10.00, "advanced"),
]

_TIER_RANK = {"local": 0, "standard": 1, "advanced": 2}


def _pick_cloud(tier: str, needed_ctx: int, config: dict) -> ProviderDecision | None:
    tier_rank_val = _TIER_RANK.get(tier, 1)
    candidates = [
        (p, mid, ctx, ci, co)
        for p, mid, ctx, ci, co, mt in _CLOUD_CATALOG
        if _TIER_RANK.get(mt, 0) <= tier_rank_val
        and ctx >= needed_ctx
        and config.get(f"{p}_api_key")
    ]
    if not candidates:
        return None
    p, mid, _ctx, ci, co = sorted(candidates, key=lambda x: x[3])[0]
    return ProviderDecision(
        tier=tier,
        model_id=mid,
        provider=p,
        reason=f"{p}: {mid} (${ci}/1M in, ${co}/1M out)",
        cost_estimate_usd=0.0,
    )
