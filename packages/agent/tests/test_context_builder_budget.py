from __future__ import annotations

from agent.context_budget import (
    ContextBudget,
    ContextItem,
    ExclusionReason,
    TIER_ORDER_BY_TASK_TYPE,
    _truncate_at_declaration_boundary,
    _truncate_at_line_boundary,
    _truncate_head_tail,
    build_budget_aware_context_pack,
    compute_mid_declaration_truncation_pct,
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


def test_truncate_at_line_boundary_never_splits_a_line():
    content = "line one\nline two\nline three\nline four"
    # Cut mid-way through "line three" - should back off to end of "line two".
    truncated = _truncate_at_line_boundary(content, max_chars=22)
    assert truncated == "line one\nline two"
    assert not content.startswith(truncated + "l")  # sanity: didn't include a partial line


def test_truncate_at_line_boundary_returns_full_content_when_it_fits():
    content = "short content"
    assert _truncate_at_line_boundary(content, max_chars=1000) == content


def test_truncate_at_declaration_boundary_keeps_whole_functions():
    content = (
        "def first():\n    return 1\n\n\n"
        "def second():\n    return 2\n\n\n"
        "def third():\n    return 3\n"
    )
    # Budget lands partway into "def third" - should cut before it, keeping
    # first() and second() whole rather than slicing third() in half.
    cut_point = content.index("def third")
    truncated = _truncate_at_declaration_boundary(content, max_chars=cut_point + 5)
    assert truncated is not None
    assert truncated.endswith("return 2")
    assert "def third" not in truncated


def test_truncate_at_declaration_boundary_none_when_boundary_too_far_back():
    # Only one declaration, right at the start - honoring it would discard
    # almost the entire allowed budget, so the caller should fall back to a
    # plain line-boundary cut instead.
    content = "def only():\n" + ("    x = 1\n" * 200)
    truncated = _truncate_at_declaration_boundary(content, max_chars=len(content) - 10)
    assert truncated is None


def test_truncate_at_declaration_boundary_none_when_content_fits():
    content = "def small():\n    return 1\n"
    assert _truncate_at_declaration_boundary(content, max_chars=len(content) + 50) is None


def test_truncate_at_declaration_boundary_handles_async_def():
    content = (
        "async def first():\n    return 1\n\n\n"
        "async def second():\n    return 2\n\n\n"
        "async def third():\n    return 3\n"
    )
    cut_point = content.index("async def third")
    truncated = _truncate_at_declaration_boundary(content, max_chars=cut_point + 5)
    assert truncated is not None
    assert truncated.endswith("return 2")
    assert "async def third" not in truncated


def test_truncate_at_declaration_boundary_handles_indented_method():
    content = (
        "class Foo:\n"
        "    def first(self):\n        return 1\n\n"
        "    def second(self):\n        return 2\n\n"
        "    def third(self):\n        return 3\n"
    )
    cut_point = content.index("    def third")
    truncated = _truncate_at_declaration_boundary(content, max_chars=cut_point + 5)
    assert truncated is not None
    assert truncated.startswith("class Foo:")
    assert truncated.endswith("return 2")
    assert "def third" not in truncated


def test_truncate_at_declaration_boundary_excludes_decorator_of_cut_declaration():
    content = (
        "def first():\n    return 1\n\n\n"
        "def second():\n    return 2\n\n\n"
        "def third():\n    return 3\n\n\n"
        "@decorator\n"
        "def fourth():\n    return 4\n"
    )
    cut_point = content.index("    return 4")
    truncated = _truncate_at_declaration_boundary(content, max_chars=cut_point + 3)
    assert truncated is not None
    assert truncated.endswith("return 3")
    assert "def fourth" not in truncated
    assert "@decorator" not in truncated


def test_declaration_boundary_regex_matches_common_forms():
    from agent.context_budget import _DECLARATION_BOUNDARY_RE

    matching = [
        "def foo():\n",
        "    def method(self):\n",
        "async def foo():\n",
        "    async def method(self):\n",
        "class Foo:\n",
        "function foo() {\n",
        "async function foo() {\n",
        "export function foo() {\n",
        "export async function foo() {\n",
        "export default function foo() {\n",
        "export class Foo {\n",
        "export default class Foo {\n",
    ]
    for line in matching:
        assert _DECLARATION_BOUNDARY_RE.match(line), f"expected match: {line!r}"

    non_matching = [
        "    return 1\n",
        "x = 1\n",
        "# def foo commented out\n",
        "definitely_not_a_def = 1\n",
    ]
    for line in non_matching:
        assert not _DECLARATION_BOUNDARY_RE.match(line), f"unexpected match: {line!r}"


def test_truncate_at_declaration_boundary_handles_js_export_default_function():
    content = (
        "export default function first() {\n  return 1;\n}\n\n\n"
        "export default function second() {\n  return 2;\n}\n\n\n"
        "export default function third() {\n  return 3;\n}\n"
    )
    cut_point = content.index("export default function third")
    truncated = _truncate_at_declaration_boundary(content, max_chars=cut_point + 5)
    assert truncated is not None
    assert truncated.endswith("}")
    assert "third" not in truncated


def test_truncate_head_tail_keeps_start_and_end():
    content = "HEAD" + ("x" * 500) + "TAIL"
    truncated = _truncate_head_tail(content, max_chars=100)
    assert truncated.startswith("HEAD")
    assert truncated.endswith("TAIL")
    assert "middle omitted" in truncated
    assert len(truncated) <= 100 + len("\n\n[... middle omitted for budget ...]\n\n")


def test_truncate_head_tail_returns_full_content_when_it_fits():
    content = "short traceback"
    assert _truncate_head_tail(content, max_chars=1000) == content


def test_fit_item_to_budget_file_prefers_declaration_boundary():
    budget = ContextBudget.from_model_max_tokens(1000)
    content = (
        "def first():\n    return 1\n\n\n"
        "def second():\n    return 2\n\n\n"
        "def third():\n    return 3\n\n\n"
        "def fourth():\n    return 4\n"
    )
    large_file = ContextItem(
        content=content,
        source="app.py",
        source_type="file",
        tokens=max(1, (len(content) + 3) // 4),
        relevance_score=0.95,
        inclusion_reason="",
        retrieval_method="filesystem",
        trust_level=5,
        tier="current_file",
    )
    # Tight budget forces truncation partway into "def third" - large enough
    # that the declaration-boundary cut clears the min-keep-ratio threshold.
    tight_budget = ContextBudget(
        model_max_tokens=budget.model_max_tokens,
        total_budget_tokens=budget.total_budget_tokens,
        tier_token_caps={**budget.tier_token_caps, "current_file": 20},
        task_type=budget.task_type,
    )
    included, _, _ = build_budget_aware_context_pack(
        tier_order=TIER_ORDER_BY_TASK_TYPE["default"],
        budget=tight_budget,
        retrieval_results={"current_file": [large_file]},
    )
    result = included[0]
    body = result.content.rsplit("\n\n[truncated for budget]", 1)[0]
    assert body.endswith("return 2")
    assert "def third" not in body
    assert "def fourth" not in body
    assert "declaration boundary" in result.inclusion_reason


def test_fit_item_to_budget_stack_trace_uses_head_tail():
    budget = ContextBudget.from_model_max_tokens(1000)
    content = "Traceback (most recent call last):\n" + ("  File x, line 1\n" * 100) + "ValueError: boom"
    stack_item = ContextItem(
        content=content,
        source="task_description",
        source_type="stack_trace",
        tokens=max(1, (len(content) + 3) // 4),
        relevance_score=1.0,
        inclusion_reason="",
        retrieval_method="task_description",
        trust_level=5,
        tier="stack_trace",
    )
    tight_budget = ContextBudget(
        model_max_tokens=budget.model_max_tokens,
        total_budget_tokens=budget.total_budget_tokens,
        tier_token_caps={**budget.tier_token_caps, "stack_trace": 20},
        task_type=budget.task_type,
    )
    included, _, _ = build_budget_aware_context_pack(
        tier_order=["stack_trace"],
        budget=tight_budget,
        retrieval_results={"stack_trace": [stack_item]},
    )
    result = included[0]
    # Head/tail truncation isn't guaranteed to land on a word boundary (same
    # as context_synthesizer's own head/tail slicing) - just confirm both
    # ends of the signal (the traceback start and the actual exception)
    # survive, with the middle dropped.
    assert "Traceback" in result.content[:20]
    assert result.content.endswith("ValueError: boom")
    assert "middle omitted" in result.content
    assert "head_tail boundary" in result.inclusion_reason


def test_compute_mid_declaration_truncation_pct_empty_when_nothing_truncated():
    items = [
        ContextItem(
            content="short",
            source="app.py",
            source_type="file",
            tokens=5,
            relevance_score=0.9,
            inclusion_reason="",
            retrieval_method="filesystem",
            trust_level=5,
            tier="current_file",
        )
    ]
    assert compute_mid_declaration_truncation_pct(items) == 0.0


def test_compute_mid_declaration_truncation_pct_ignores_non_code_types():
    # A truncated rule/text item shouldn't count toward the code-truncation
    # risk signal even though it was truncated via a plain line cut.
    items = [
        ContextItem(
            content="rule text",
            source="rule-1",
            source_type="rule",
            tokens=5,
            relevance_score=0.9,
            inclusion_reason="",
            retrieval_method="rules",
            trust_level=5,
            tier="rules",
            truncated=True,
            truncation_boundary="line",
        )
    ]
    assert compute_mid_declaration_truncation_pct(items) == 0.0


def test_compute_mid_declaration_truncation_pct_counts_fallback_cuts():
    clean = ContextItem(
        content="def a(): pass",
        source="a.py",
        source_type="file",
        tokens=5,
        relevance_score=0.9,
        inclusion_reason="",
        retrieval_method="filesystem",
        trust_level=5,
        tier="current_file",
        truncated=True,
        truncation_boundary="declaration",
    )
    risky = ContextItem(
        content="def b(): pas",
        source="b.py",
        source_type="file",
        tokens=5,
        relevance_score=0.9,
        inclusion_reason="",
        retrieval_method="filesystem",
        trust_level=5,
        tier="current_file",
        truncated=True,
        truncation_boundary="line",
    )
    assert compute_mid_declaration_truncation_pct([clean, risky]) == 0.5
    assert compute_mid_declaration_truncation_pct([risky, risky]) == 1.0
    assert compute_mid_declaration_truncation_pct([clean, clean]) == 0.0


def test_fit_item_to_budget_marks_truncated_flag_and_boundary():
    budget = ContextBudget.from_model_max_tokens(1000)
    content = "def first():\n    return 1\n\n\ndef second():\n    return 2\n"
    item = ContextItem(
        content=content,
        source="app.py",
        source_type="file",
        tokens=max(1, (len(content) + 3) // 4),
        relevance_score=0.9,
        inclusion_reason="",
        retrieval_method="filesystem",
        trust_level=5,
        tier="current_file",
    )
    tight_budget = ContextBudget(
        model_max_tokens=budget.model_max_tokens,
        total_budget_tokens=budget.total_budget_tokens,
        tier_token_caps={**budget.tier_token_caps, "current_file": 8},
        task_type=budget.task_type,
    )
    included, _, _ = build_budget_aware_context_pack(
        tier_order=TIER_ORDER_BY_TASK_TYPE["default"],
        budget=tight_budget,
        retrieval_results={"current_file": [item]},
    )
    assert included[0].truncated is True
    assert included[0].truncation_boundary in ("declaration", "line")


def test_context_item_defaults_not_truncated():
    item = ContextItem(
        content="x",
        source="a",
        source_type="file",
        tokens=1,
        relevance_score=1.0,
        inclusion_reason="",
        retrieval_method="filesystem",
        trust_level=5,
        tier="current_file",
    )
    assert item.truncated is False
    assert item.truncation_boundary == "none"
