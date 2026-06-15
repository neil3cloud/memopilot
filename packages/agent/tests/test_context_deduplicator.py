"""Tests for context_deduplicator — 5-gram shingling near-duplicate removal."""
from __future__ import annotations

import pytest

from agent.context_deduplicator import (
    DeduplicatableItem,
    DeduplicationResult,
    ExclusionReason,
    deduplicate_context_items,
    deduplicate_text_list,
)


def _item(id: str, content: str, trust: int = 3, source_type: str = "memory") -> DeduplicatableItem:
    return DeduplicatableItem(id=id, content=content, trust_level=trust, source_type=source_type)


def test_identical_content_deduped():
    text = "Always validate user input before persisting to the database. " * 5
    items = [_item("a", text, trust=2), _item("b", text, trust=5)]
    result = deduplicate_context_items(items)

    assert result.kept_count == 1
    assert result.excluded[0].id == "a"  # lower trust excluded
    assert result.kept[0].id == "b"


def test_dissimilar_content_both_kept():
    a = "Validate all user inputs. Never trust external data."
    b = "The deployment pipeline requires approval for production changes to billing modules."
    items = [_item("x", a), _item("y", b)]
    result = deduplicate_context_items(items)

    assert result.kept_count == 2
    assert result.dedup_savings_pct == 0.0


def test_near_duplicate_above_threshold_excluded():
    # Use a shorter, more distinctive near-duplicate that stays within shingle sample
    base = "Always run automated tests before merging any pull request to main branch. " * 3
    near_dup = base[:-5]  # trim slightly — high shingle overlap guaranteed
    items = [_item("orig", base, trust=4), _item("near", near_dup, trust=2)]
    result = deduplicate_context_items(items)

    # One of them must be excluded (they're almost identical)
    assert result.kept_count == 1
    assert result.kept[0].id == "orig"  # higher trust kept


def test_dedup_savings_pct_computed():
    text = "Use dependency injection for all service constructors. " * 6
    items = [_item("p", text, trust=3), _item("q", text, trust=3), _item("r", "Completely different content here.")]
    result = deduplicate_context_items(items)

    assert result.original_count == 3
    assert result.kept_count < 3
    assert 0.0 < result.dedup_savings_pct <= 1.0


def test_empty_list_returns_empty_result():
    result = deduplicate_context_items([])
    assert result.kept == []
    assert result.excluded == []
    assert result.dedup_savings_pct == 0.0


def test_single_item_never_excluded():
    item = _item("solo", "Only one item in the context pack.")
    result = deduplicate_context_items([item])
    assert result.kept_count == 1
    assert result.excluded == []


def test_deduplicate_text_list_returns_deduped_and_pct():
    text = "Always validate inputs before saving. " * 10
    texts = [text, text, "A completely different rule about deployments."]
    deduped, pct = deduplicate_text_list(texts)

    assert len(deduped) < len(texts)
    assert pct > 0.0
    assert any("completely different" in t for t in deduped)


def test_deduplicate_text_list_empty():
    deduped, pct = deduplicate_text_list([])
    assert deduped == []
    assert pct == 0.0
