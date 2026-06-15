"""Tests for context_quality_scorer — verdict thresholds and signal detection."""
from __future__ import annotations

import pytest

from agent.context_quality_scorer import (
    ContextPackSnapshot,
    ContextQualityScore,
    build_quality_warning,
    score_context_pack,
)


def _make_pack(**kwargs) -> ContextPackSnapshot:
    defaults = dict(
        files=["app/service.py"],
        rules=["Do not modify billing logic without approval"],
        source_types=["file"],
        stale_exclusion_pct=0.0,
        dedup_savings_pct=0.0,
        graph_expansion_files=0,
        primary_symbol="UserService",
    )
    defaults.update(kwargs)
    return ContextPackSnapshot(**defaults)


def test_verdict_good_all_signals():
    pack = _make_pack(
        source_types=["file", "caller", "test", "commit"],
        files=["app/service.py", "tests/test_service.py"],
    )
    result = score_context_pack(pack)
    assert result.verdict == "good"
    assert result.total >= 0.75


def test_verdict_acceptable_partial_signals():
    # has primary symbol (file path matches) + rules + tests — no callers, no history
    pack = _make_pack(
        source_types=["file", "test"],
        files=["app/user_service.py", "tests/test_service.py"],
        primary_symbol="user_service",  # matches file path → has_primary_symbol=True
    )
    result = score_context_pack(pack)
    # 0.25 (primary) + 0.20 (tests) + 0.15 (rules) + 0.10 (stale=0.0→ 0.10) = 0.70 → "acceptable"
    assert result.verdict in ("acceptable", "good")
    assert result.total >= 0.50


def test_verdict_poor_minimal_signals():
    pack = _make_pack(
        files=["app/service.py"],
        rules=[],
        source_types=["file"],
        primary_symbol=None,
    )
    result = score_context_pack(pack)
    assert result.verdict in ("poor", "rebuild")
    assert result.total < 0.50


def test_verdict_rebuild_no_signals():
    pack = _make_pack(
        files=[],
        rules=[],
        source_types=[],
        primary_symbol=None,
        stale_exclusion_pct=1.0,
    )
    result = score_context_pack(pack)
    assert result.verdict == "rebuild"
    assert result.total < 0.30


def test_missing_signals_populated():
    pack = _make_pack(
        files=[],
        rules=[],
        source_types=[],
        primary_symbol="UnindexedSymbol",
    )
    result = score_context_pack(pack)
    assert any("UnindexedSymbol" in s or "not found" in s for s in result.missing_signals)
    assert any("callers" in s.lower() for s in result.missing_signals)
    assert any("rules" in s.lower() for s in result.missing_signals)


def test_stale_penalty_applied():
    pack_fresh = _make_pack(stale_exclusion_pct=0.0)
    pack_stale = _make_pack(stale_exclusion_pct=1.0)
    result_fresh = score_context_pack(pack_fresh)
    result_stale = score_context_pack(pack_stale)
    assert result_fresh.total > result_stale.total


def test_build_quality_warning_includes_verdict():
    pack = _make_pack(files=[], rules=[], source_types=[], primary_symbol=None)
    quality = score_context_pack(pack)
    warning = build_quality_warning(quality, "Fix user service")
    assert quality.verdict.upper() in warning
    assert "✗" in warning or "Missing" in warning


def test_as_dict_round_trip():
    pack = _make_pack()
    quality = score_context_pack(pack)
    d = quality.as_dict()
    assert "total" in d
    assert "verdict" in d
    assert "missing_signals" in d
    assert isinstance(d["missing_signals"], list)
