from __future__ import annotations

from agent.context_budget import (
    ContextBudget,
    ContextItem,
    ExclusionReason,
    TIER_ORDER_BY_TASK_TYPE,
    build_budget_aware_context_pack,
)


def test_budget_allocation_respects_tier_caps():
    budget = ContextBudget.from_model_max_tokens(1000)
    large_file = ContextItem(
        content="x" * 4000,
        source="app.py",
        source_type="file",
        tokens=400,
        relevance_score=0.95,
        inclusion_reason="",
        retrieval_method="filesystem",
        trust_level=5,
        tier="current_file",
    )
    fts_item = ContextItem(
        content="memory",
        source="memo-1",
        source_type="memory",
        tokens=60,
        relevance_score=0.8,
        inclusion_reason="",
        retrieval_method="fts",
        trust_level=4,
        tier="fts",
    )

    included, _, summary = build_budget_aware_context_pack(
        tier_order=TIER_ORDER_BY_TASK_TYPE["default"],
        budget=budget,
        retrieval_results={"current_file": [large_file], "fts": [fts_item]},
    )

    current_file = next(item for item in included if item.tier == "current_file")
    assert current_file.tokens == budget.tier_token_caps["current_file"]
    assert any(item.tier == "fts" for item in included)
    assert summary["tiers"]["current_file"]["used_tokens"] == budget.tier_token_caps["current_file"]


def test_stale_exclusion_count_nonzero():
    budget = ContextBudget.from_model_max_tokens(1000)
    retrieval_results = {
        "fts": [
            {
                "content": f"memory {index}",
                "source": f"src\\module_{index}.py",
                "source_type": "memory",
                "tokens": 10,
                "relevance_score": 0.9,
                "retrieval_method": "fts",
                "trust_level": 4,
                "stale": True,
            }
            for index in range(3)
        ]
    }

    _, excluded, summary = build_budget_aware_context_pack(
        tier_order=TIER_ORDER_BY_TASK_TYPE["investigation"],
        budget=budget,
        retrieval_results=retrieval_results,
    )

    assert summary["stale_exclusions"]["count"] == 3
    assert sum(1 for item in excluded if item.exclusion_reason == ExclusionReason.STALE) == 3


def test_inclusion_reason_at_selection_time():
    budget = ContextBudget.from_model_max_tokens(1000)
    fts_item = ContextItem(
        content="Likely fix lives in retry handler.",
        source="memo-fts-1",
        source_type="memory",
        tokens=20,
        relevance_score=0.87,
        inclusion_reason="",
        retrieval_method="fts",
        trust_level=4,
        tier="fts",
    )

    included, _, _ = build_budget_aware_context_pack(
        tier_order=TIER_ORDER_BY_TASK_TYPE["investigation"],
        budget=budget,
        retrieval_results={"fts": [fts_item]},
    )

    assert included[0].retrieval_method == "fts"
    assert "score=0.870" in included[0].inclusion_reason


def test_exclusion_reason_uses_enum():
    budget = ContextBudget.from_model_max_tokens(1000)
    retrieval_results = {
        "fts": [
            {
                "content": "low score",
                "source": "memo-low",
                "source_type": "memory",
                "tokens": 10,
                "relevance_score": 0.01,
                "retrieval_method": "fts",
                "trust_level": 4,
            },
            {
                "content": "low trust",
                "source": "memo-trust",
                "source_type": "memory",
                "tokens": 10,
                "relevance_score": 0.8,
                "retrieval_method": "fts",
                "trust_level": 0,
            },
            {
                "content": "blocked",
                "source": "memo-blocked",
                "source_type": "memory",
                "tokens": 10,
                "relevance_score": 0.8,
                "retrieval_method": "fts",
                "trust_level": 4,
                "blocked_by_rule": True,
            },
        ]
    }

    _, excluded, _ = build_budget_aware_context_pack(
        tier_order=TIER_ORDER_BY_TASK_TYPE["investigation"],
        budget=budget,
        retrieval_results=retrieval_results,
        min_trust_level=1,
    )

    assert excluded
    assert all(isinstance(item.exclusion_reason, ExclusionReason) for item in excluded)


def test_bug_fix_reorders_stack_trace_before_current_file():
    budget = ContextBudget.from_model_max_tokens(1000, task_type="bug_fix")
    included, _, _ = build_budget_aware_context_pack(
        tier_order=TIER_ORDER_BY_TASK_TYPE["bug_fix"],
        budget=budget,
        retrieval_results={
            "current_file": [
                ContextItem(
                    content="current file",
                    source="app.py",
                    source_type="file",
                    tokens=20,
                    relevance_score=0.9,
                    inclusion_reason="",
                    retrieval_method="filesystem",
                    trust_level=5,
                    tier="current_file",
                )
            ],
            "stack_trace": [
                ContextItem(
                    content="Traceback...",
                    source="trace",
                    source_type="stack_trace",
                    tokens=20,
                    relevance_score=0.95,
                    inclusion_reason="",
                    retrieval_method="task_description",
                    trust_level=5,
                    tier="stack_trace",
                )
            ],
        },
    )

    assert TIER_ORDER_BY_TASK_TYPE["bug_fix"].index("stack_trace") < TIER_ORDER_BY_TASK_TYPE["bug_fix"].index("current_file")
    assert included[0].tier == "stack_trace"


def test_template_allocation_override():
    default_budget = ContextBudget.from_model_max_tokens(2000, task_type="bug_fix")
    override_budget = ContextBudget.from_model_max_tokens(
        2000,
        task_type="bug_fix",
        template_id="workspace:bug-fix",
    )

    assert override_budget.tier_token_caps["stack_trace"] > default_budget.tier_token_caps["stack_trace"]


def test_roll_forward_unused_budget():
    budget = ContextBudget.from_model_max_tokens(1000)
    included, _, summary = build_budget_aware_context_pack(
        tier_order=["stack_trace", "fts"],
        budget=budget,
        retrieval_results={
            "stack_trace": [
                ContextItem(
                    content="tiny trace",
                    source="trace",
                    source_type="stack_trace",
                    tokens=10,
                    relevance_score=0.9,
                    inclusion_reason="",
                    retrieval_method="task_description",
                    trust_level=5,
                    tier="stack_trace",
                )
            ],
            "fts": [
                ContextItem(
                    content="x" * 600,
                    source="memo",
                    source_type="memory",
                    tokens=120,
                    relevance_score=0.8,
                    inclusion_reason="",
                    retrieval_method="fts",
                    trust_level=4,
                    tier="fts",
                )
            ],
        },
    )

    assert summary["tiers"]["stack_trace"]["unused_tokens"] > 0
    assert summary["tiers"]["fts"]["available_tokens"] == (
        budget.tier_token_caps["fts"] + summary["tiers"]["stack_trace"]["unused_tokens"]
    )
    assert len(included) == 2
