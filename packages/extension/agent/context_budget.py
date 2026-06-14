"""Budget-aware context pack selection helpers."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Mapping


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


class ExclusionReason(StrEnum):
    STALE = "stale"
    BUDGET_EXCEEDED = "budget_exceeded"
    LOW_RELEVANCE = "low_relevance"
    TRUST_TOO_LOW = "trust_too_low"
    VISIBILITY_RESTRICTED = "visibility_restricted"
    DUPLICATE = "duplicate"
    BLOCKED_BY_RULE = "blocked_by_rule"


TIER_ORDER_BY_TASK_TYPE: dict[str, list[str]] = {
    "default": ["current_file", "stack_trace", "fts", "rules", "skills"],
    "bug_fix": ["stack_trace", "current_file", "fts", "rules", "skills"],
    "investigation": ["fts", "current_file", "stack_trace", "rules", "skills"],
    "test_generation": ["current_file", "fts", "rules", "skills", "stack_trace"],
}

_DEFAULT_TIER_RATIOS: dict[str, float] = {
    "current_file": 0.35,
    "stack_trace": 0.15,
    "fts": 0.2,
    "rules": 0.2,
    "skills": 0.1,
}

_TASK_TYPE_TIER_RATIO_OVERRIDES: dict[str, dict[str, float]] = {
    "bug_fix": {
        "current_file": 0.3,
        "stack_trace": 0.25,
        "fts": 0.2,
        "rules": 0.15,
        "skills": 0.1,
    },
    "investigation": {
        "current_file": 0.2,
        "stack_trace": 0.15,
        "fts": 0.35,
        "rules": 0.2,
        "skills": 0.1,
    },
    "test_generation": {
        "current_file": 0.35,
        "stack_trace": 0.05,
        "fts": 0.2,
        "rules": 0.2,
        "skills": 0.2,
    },
}

_BUG_FIX_TEMPLATE_OVERRIDES: dict[str, float] = {
    "current_file": 0.2,
    "stack_trace": 0.35,
    "fts": 0.2,
    "rules": 0.15,
    "skills": 0.1,
}


@dataclass(frozen=True)
class ContextItem:
    content: str
    source: str
    source_type: str
    tokens: int
    relevance_score: float
    inclusion_reason: str
    retrieval_method: str
    trust_level: int
    tier: str


@dataclass(frozen=True)
class ExcludedItem:
    source: str
    source_type: str
    exclusion_reason: ExclusionReason
    tokens_would_have_used: int


@dataclass(frozen=True)
class ContextBudget:
    model_max_tokens: int
    total_budget_tokens: int
    tier_token_caps: dict[str, int]
    task_type: str = "default"
    template_id: str | None = None

    @classmethod
    def from_model_max_tokens(
        cls,
        model_max_tokens: int,
        *,
        task_type: str | None = None,
        template_id: str | None = None,
        tier_order: list[str] | None = None,
    ) -> ContextBudget:
        normalized_task_type = _normalize_task_type(task_type)
        resolved_tier_order = tier_order or TIER_ORDER_BY_TASK_TYPE[normalized_task_type]
        total_budget_tokens = max(1, int(model_max_tokens * 0.6))
        tier_ratios = _resolve_tier_ratios(
            task_type=normalized_task_type,
            template_id=template_id,
            tier_order=resolved_tier_order,
        )
        tier_token_caps = _allocate_tier_caps(total_budget_tokens, tier_ratios, resolved_tier_order)
        return cls(
            model_max_tokens=model_max_tokens,
            total_budget_tokens=total_budget_tokens,
            tier_token_caps=tier_token_caps,
            task_type=normalized_task_type,
            template_id=template_id,
        )


def build_budget_aware_context_pack(
    *,
    tier_order: list[str],
    budget: ContextBudget,
    retrieval_results: Mapping[str, list[ContextItem | Mapping[str, object]]],
    min_relevance_score: float = 0.15,
    min_trust_level: int = 1,
) -> tuple[list[ContextItem], list[ExcludedItem], dict[str, object]]:
    included_items: list[ContextItem] = []
    excluded_items: list[ExcludedItem] = []
    seen_keys: set[tuple[str, str]] = set()
    roll_forward_tokens = 0
    tier_summaries: dict[str, dict[str, int]] = {}
    stale_sources: list[str] = []

    for tier in tier_order:
        base_cap = int(budget.tier_token_caps.get(tier, 0))
        available_tokens = base_cap + roll_forward_tokens
        used_tokens = 0
        tier_included = 0
        tier_excluded = 0

        for raw_item in retrieval_results.get(tier, []):
            item, metadata = _coerce_candidate(raw_item, tier=tier)
            key = (item.source_type, item.source)

            exclusion_reason: ExclusionReason | None = None
            if metadata["stale"]:
                exclusion_reason = ExclusionReason.STALE
                stale_sources.append(item.source)
            elif metadata["blocked_by_rule"]:
                exclusion_reason = ExclusionReason.BLOCKED_BY_RULE
            elif metadata["visibility_restricted"]:
                exclusion_reason = ExclusionReason.VISIBILITY_RESTRICTED
            elif item.trust_level < min_trust_level:
                exclusion_reason = ExclusionReason.TRUST_TOO_LOW
            elif item.relevance_score < min_relevance_score:
                exclusion_reason = ExclusionReason.LOW_RELEVANCE
            elif key in seen_keys:
                exclusion_reason = ExclusionReason.DUPLICATE

            if exclusion_reason is not None:
                excluded_items.append(
                    ExcludedItem(
                        source=item.source,
                        source_type=item.source_type,
                        exclusion_reason=exclusion_reason,
                        tokens_would_have_used=item.tokens,
                    )
                )
                tier_excluded += 1
                continue

            remaining_tokens = max(0, available_tokens - used_tokens)
            if remaining_tokens <= 0:
                excluded_items.append(
                    ExcludedItem(
                        source=item.source,
                        source_type=item.source_type,
                        exclusion_reason=ExclusionReason.BUDGET_EXCEEDED,
                        tokens_would_have_used=item.tokens,
                    )
                )
                tier_excluded += 1
                continue

            selected = _fit_item_to_budget(item, remaining_tokens)
            included_items.append(selected)
            seen_keys.add(key)
            used_tokens += selected.tokens
            tier_included += 1

        roll_forward_tokens = max(0, available_tokens - used_tokens)
        tier_summaries[tier] = {
            "cap_tokens": base_cap,
            "available_tokens": available_tokens,
            "used_tokens": used_tokens,
            "unused_tokens": roll_forward_tokens,
            "included_count": tier_included,
            "excluded_count": tier_excluded,
        }

    total_used_tokens = sum(item.tokens for item in included_items)
    stale_modules = sorted({_source_to_module_name(source) for source in stale_sources if source})
    budget_summary: dict[str, object] = {
        "task_type": budget.task_type,
        "template_id": budget.template_id,
        "model_max_tokens": budget.model_max_tokens,
        "total_budget_tokens": budget.total_budget_tokens,
        "total_used_tokens": total_used_tokens,
        "unused_tokens": max(0, budget.total_budget_tokens - total_used_tokens),
        "tier_caps": dict(budget.tier_token_caps),
        "tiers": tier_summaries,
        "included_count": len(included_items),
        "excluded_count": len(excluded_items),
        "stale_exclusions": {
            "count": len(stale_sources),
            "affected_modules": stale_modules,
        },
    }
    return included_items, excluded_items, budget_summary


def _normalize_task_type(task_type: str | None) -> str:
    normalized = (task_type or "default").strip().lower().replace("-", "_")
    if normalized not in TIER_ORDER_BY_TASK_TYPE:
        return "default"
    return normalized


def _resolve_tier_ratios(
    *,
    task_type: str,
    template_id: str | None,
    tier_order: list[str],
) -> dict[str, float]:
    ratios = dict(_DEFAULT_TIER_RATIOS)
    ratios.update(_TASK_TYPE_TIER_RATIO_OVERRIDES.get(task_type, {}))
    if template_id and "bug-fix" in template_id.lower():
        ratios.update(_BUG_FIX_TEMPLATE_OVERRIDES)
    for tier in tier_order:
        ratios.setdefault(tier, 0.0)
    return ratios


def _allocate_tier_caps(
    total_budget_tokens: int,
    tier_ratios: Mapping[str, float],
    tier_order: list[str],
) -> dict[str, int]:
    caps: dict[str, int] = {}
    assigned = 0
    for index, tier in enumerate(tier_order):
        if index == len(tier_order) - 1:
            cap = max(0, total_budget_tokens - assigned)
        else:
            cap = int(total_budget_tokens * float(tier_ratios.get(tier, 0.0)))
            assigned += cap
        caps[tier] = cap
    return caps


def _coerce_candidate(
    raw_item: ContextItem | Mapping[str, object],
    *,
    tier: str,
) -> tuple[ContextItem, dict[str, bool]]:
    if isinstance(raw_item, ContextItem):
        return raw_item, {
            "stale": False,
            "blocked_by_rule": False,
            "visibility_restricted": False,
        }

    payload = dict(raw_item)
    content = str(payload.get("content", ""))
    source = str(payload.get("source", ""))
    source_type = str(payload.get("source_type", "unknown"))
    tokens = int(payload.get("tokens") or _estimate_tokens(content))
    relevance_score = float(payload.get("relevance_score") or 0.0)
    retrieval_method = str(payload.get("retrieval_method") or tier)
    trust_level = int(payload.get("trust_level") or 0)
    inclusion_reason = str(payload.get("inclusion_reason") or "")
    item_tier = str(payload.get("tier") or tier)
    item = ContextItem(
        content=content,
        source=source,
        source_type=source_type,
        tokens=max(1, tokens),
        relevance_score=relevance_score,
        inclusion_reason=inclusion_reason,
        retrieval_method=retrieval_method,
        trust_level=trust_level,
        tier=item_tier,
    )
    return item, {
        "stale": bool(payload.get("stale", False)),
        "blocked_by_rule": bool(payload.get("blocked_by_rule", False)),
        "visibility_restricted": bool(payload.get("visibility_restricted", False)),
    }


def _fit_item_to_budget(item: ContextItem, remaining_tokens: int) -> ContextItem:
    allowed_tokens = min(item.tokens, remaining_tokens)
    content = item.content
    if allowed_tokens < item.tokens and content:
        max_chars = max(0, allowed_tokens * 4)
        suffix = "\n\n[truncated for budget]"
        content = content[:max_chars].rstrip()
        if len(content) < len(item.content):
            content += suffix
    reason = item.inclusion_reason.strip() or (
        f"Selected from {item.tier} via {item.retrieval_method} "
        f"(score={item.relevance_score:.3f})."
    )
    if allowed_tokens < item.tokens:
        reason = f"{reason} Truncated to {allowed_tokens} tokens for tier budget."
    return replace(item, content=content, tokens=max(1, allowed_tokens), inclusion_reason=reason)


def _source_to_module_name(source: str) -> str:
    cleaned = source.replace("\\", "/").strip()
    if not cleaned:
        return "unknown"
    last_part = cleaned.rsplit("/", 1)[-1]
    if "." in last_part:
        return last_part.rsplit(".", 1)[0]
    return last_part
