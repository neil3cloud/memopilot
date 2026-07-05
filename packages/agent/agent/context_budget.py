"""Budget-aware context pack selection helpers."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum

logger = logging.getLogger(__name__)

# Source types whose content is source code, where a mid-function cut destroys
# more signal than a mid-line cut would for prose. Matches on real source_type
# values produced by api.py (_read_context_file_item, symbol extraction).
_CODE_SOURCE_TYPES = frozenset({"file", "symbol", "symbol_skeleton"})

_DECLARATION_BOUNDARY_RE = re.compile(
    r"^[ \t]*("
    r"async\s+def\s|def\s|class\s|"
    r"function\s|async\s+function\s|"
    r"export\s+(default\s+)?(async\s+)?function\s|"
    r"export\s+(default\s+)?class\s"
    r")"
)
# Decorator lines (Python @foo, TS/JS-style annotations) directly preceding a
# matched declaration are folded into the same boundary - cutting before a
# declaration but leaving its decorator dangling would be its own kind of
# mid-unit truncation.
_DECORATOR_RE = re.compile(r"^[ \t]*@\w")

# Only accept a declaration-boundary cut if it keeps at least this fraction of
# the allowed budget; otherwise falling back to a plain line cut wastes less
# of the available tokens.
_DECLARATION_BOUNDARY_MIN_KEEP_RATIO = 0.6


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def _coerce_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float, str)) and value:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return default


def _coerce_float(value: object, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float, str)) and value:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    return default


def _truncate_at_line_boundary(content: str, max_chars: int) -> str:
    """Cut content at or before max_chars without splitting a line in half."""
    if len(content) <= max_chars:
        return content
    candidate = content[:max_chars]
    last_newline = candidate.rfind("\n")
    if last_newline > 0:
        return candidate[:last_newline]
    return candidate


def _truncate_at_declaration_boundary(content: str, max_chars: int) -> str | None:
    """Cut before the last declaration (top-level or indented/method-level)
    that would otherwise be cut mid-body. Returns None if no declaration
    boundary is found close enough to max_chars to be worth preferring over a
    plain line-boundary cut.
    """
    if len(content) <= max_chars:
        return None
    lines = content.splitlines(keepends=True)
    line_starts: list[int] = []
    offset = 0
    for line in lines:
        line_starts.append(offset)
        offset += len(line)

    last_decl_index: int | None = None
    offset = 0
    for index, line in enumerate(lines):
        if offset > max_chars:
            break
        if _DECLARATION_BOUNDARY_RE.match(line):
            last_decl_index = index
        offset += len(line)
    if last_decl_index is None:
        return None

    # Fold in any decorator lines immediately preceding this declaration so we
    # don't strand a decorator without the def/class it decorates.
    cut_index = last_decl_index
    while cut_index > 0 and _DECORATOR_RE.match(lines[cut_index - 1]):
        cut_index -= 1

    candidate_cut = line_starts[cut_index]
    if candidate_cut == 0:
        return None
    if candidate_cut < max_chars * _DECLARATION_BOUNDARY_MIN_KEEP_RATIO:
        return None
    return content[:candidate_cut].rstrip()


def _truncate_head_tail(content: str, max_chars: int) -> str:
    """Keep both the start and end of content, dropping the middle. Mirrors
    context_synthesizer.build_synthesis_user_prompt's head/tail pattern -
    useful for stack traces, where the exception message is usually at the
    tail but the failing call site may be at the head.
    """
    if len(content) <= max_chars:
        return content
    marker = "\n\n[... middle omitted for budget ...]\n\n"
    available = max(0, max_chars - len(marker))
    head_len = available // 2
    tail_len = available - head_len
    if head_len <= 0 or tail_len <= 0:
        return _truncate_at_line_boundary(content, max_chars)
    return f"{content[:head_len]}{marker}{content[-tail_len:]}"


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
    reference_id: str | None = None
    truncated: bool = False
    truncation_boundary: str = "none"  # "none" | "line" | "declaration" | "head_tail"


@dataclass(frozen=True)
class ExcludedItem:
    source: str
    source_type: str
    exclusion_reason: ExclusionReason
    tokens_would_have_used: int
    reference_id: str | None = None


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
                        reference_id=item.reference_id,
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
                        reference_id=item.reference_id,
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
    reference_id_value = payload.get("reference_id")
    reference_id = str(reference_id_value) if reference_id_value else None
    source_type = str(payload.get("source_type", "unknown"))
    tokens = _coerce_int(payload.get("tokens"), _estimate_tokens(content))
    relevance_score = _coerce_float(payload.get("relevance_score"), 0.0)
    retrieval_method = str(payload.get("retrieval_method") or tier)
    trust_level = _coerce_int(payload.get("trust_level"), 0)
    inclusion_reason = str(payload.get("inclusion_reason") or "")
    item_tier = str(payload.get("tier") or tier)
    item = ContextItem(
        content=content,
        source=source,
        reference_id=reference_id,
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
    boundary_used = "none"
    if allowed_tokens < item.tokens and content:
        max_chars = max(0, allowed_tokens * 4)
        if item.source_type in _CODE_SOURCE_TYPES:
            declaration_cut = _truncate_at_declaration_boundary(content, max_chars)
            if declaration_cut is not None:
                content = declaration_cut
                boundary_used = "declaration"
            else:
                content = _truncate_at_line_boundary(content, max_chars)
                boundary_used = "line"
        elif item.source_type == "stack_trace":
            content = _truncate_head_tail(content, max_chars)
            boundary_used = "head_tail"
        else:
            content = _truncate_at_line_boundary(content, max_chars)
            boundary_used = "line"
        # head/tail truncation already carries its own inline
        # "middle omitted" marker; don't stack a second marker on top of it.
        if boundary_used != "head_tail" and len(content) < len(item.content):
            content = content.rstrip() + "\n\n[truncated for budget]"
        logger.debug(
            "context_item_truncated source_type=%s tier=%s tokens_before=%d "
            "tokens_after=%d boundary_used=%s",
            item.source_type,
            item.tier,
            item.tokens,
            allowed_tokens,
            boundary_used,
        )
    reason = item.inclusion_reason.strip() or (
        f"Selected from {item.tier} via {item.retrieval_method} "
        f"(score={item.relevance_score:.3f})."
    )
    if allowed_tokens < item.tokens:
        reason = (
            f"{reason} Truncated to {allowed_tokens} tokens for tier budget "
            f"({boundary_used} boundary)."
        )
    return replace(
        item,
        content=content,
        tokens=max(1, allowed_tokens),
        inclusion_reason=reason,
        truncated=allowed_tokens < item.tokens,
        truncation_boundary=boundary_used,
    )


def compute_mid_declaration_truncation_pct(items: list[ContextItem]) -> float:
    """Fraction of truncated code items (file/symbol/symbol_skeleton) that
    could NOT be cut at a clean declaration boundary and fell back to a plain
    line-boundary cut - i.e. content that was likely cut mid-function/class.
    Used as a context-quality risk signal, not a hard pass/fail gate.
    """
    truncated_code_items = [
        item for item in items if item.truncated and item.source_type in _CODE_SOURCE_TYPES
    ]
    if not truncated_code_items:
        return 0.0
    fallback_count = sum(
        1 for item in truncated_code_items if item.truncation_boundary != "declaration"
    )
    return fallback_count / len(truncated_code_items)


def _source_to_module_name(source: str) -> str:
    cleaned = source.replace("\\", "/").strip()
    if not cleaned:
        return "unknown"
    last_part = cleaned.rsplit("/", 1)[-1]
    if "." in last_part:
        return last_part.rsplit(".", 1)[0]
    return last_part
