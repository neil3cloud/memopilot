"""Context pack quality scorer.

Computes a quality score for a context pack BEFORE it is sent to the AI model.
A poor-quality context pack is surfaced to the developer with actionable
recovery options rather than silently sending a known-bad context.

Scoring weights:
  primary_symbol present  0.25   (most important — is the thing being modified indexed?)
  callers present         0.20   (blast-radius awareness)
  related tests present   0.20   (safety net for changes)
  active rules present    0.15   (governance compliance)
  recent commit history   0.10   (decision context)
  stale exclusion rate    0.10   (index freshness)

Verdict thresholds:
  good       >= 0.75
  acceptable >= 0.50
  poor       >= 0.30
  rebuild    <  0.30
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ContextQualityScore:
    total: float                    # 0.0 – 1.0
    has_primary_symbol: bool
    has_callers: bool
    has_related_tests: bool
    has_active_rules: bool
    has_recent_history: bool
    stale_exclusion_pct: float      # 0.0–1.0 fraction excluded as stale
    dedup_savings_pct: float        # 0.0–1.0 fraction removed as duplicates
    graph_expansion_files: int      # files added by call-graph expansion
    verdict: str                    # 'good' | 'acceptable' | 'poor' | 'rebuild'
    missing_signals: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "total": round(self.total, 3),
            "has_primary_symbol": self.has_primary_symbol,
            "has_callers": self.has_callers,
            "has_related_tests": self.has_related_tests,
            "has_active_rules": self.has_active_rules,
            "has_recent_history": self.has_recent_history,
            "stale_exclusion_pct": round(self.stale_exclusion_pct, 3),
            "dedup_savings_pct": round(self.dedup_savings_pct, 3),
            "graph_expansion_files": self.graph_expansion_files,
            "verdict": self.verdict,
            "missing_signals": self.missing_signals,
        }


@dataclass
class ContextPackSnapshot:
    """Minimal snapshot of a context pack for scoring — avoids circular deps."""
    files: list[str]                # file paths included
    rules: list[str]                # active rule texts
    source_types: list[str]         # list of source_type values for each item
    stale_exclusion_pct: float = 0.0
    dedup_savings_pct: float = 0.0
    graph_expansion_files: int = 0
    primary_symbol: str | None = None   # primary symbol name being modified


def score_context_pack(
    pack: ContextPackSnapshot,
    *,
    task_description: str = "",
) -> ContextQualityScore:
    """Compute a quality score for the given context pack snapshot."""

    # ── Signal detection ──────────────────────────────────────────────────────
    has_primary_symbol = _has_primary_symbol(pack)
    has_callers = "caller" in pack.source_types
    has_related_tests = _has_related_tests(pack)
    has_active_rules = bool(pack.rules)
    has_recent_history = "commit" in pack.source_types

    # ── Weighted score ────────────────────────────────────────────────────────
    score = 0.0
    score += 0.25 if has_primary_symbol else 0.0
    score += 0.20 if has_callers else 0.0
    score += 0.20 if has_related_tests else 0.0
    score += 0.15 if has_active_rules else 0.0
    score += 0.10 if has_recent_history else 0.0
    # stale penalty: 0.10 * (1 - stale_pct)
    score += 0.10 * max(0.0, 1.0 - pack.stale_exclusion_pct)

    # ── Verdict ───────────────────────────────────────────────────────────────
    if score >= 0.75:
        verdict = "good"
    elif score >= 0.50:
        verdict = "acceptable"
    elif score >= 0.30:
        verdict = "poor"
    else:
        verdict = "rebuild"

    # ── Missing signals ───────────────────────────────────────────────────────
    missing: list[str] = []
    if not has_primary_symbol:
        sym = pack.primary_symbol or "primary symbol"
        missing.append(f"'{sym}' not found in index")
    if not has_callers:
        missing.append("no callers identified (call graph not built yet)")
    if not has_related_tests:
        missing.append("no related test files found")
    if not has_active_rules:
        missing.append("no active rules loaded")
    if not has_recent_history:
        missing.append("no recent commit history for these files")
    if pack.stale_exclusion_pct > 0.3:
        pct = int(pack.stale_exclusion_pct * 100)
        missing.append(f"{pct}% of recalled memory was stale — consider rebuilding index")

    return ContextQualityScore(
        total=round(score, 3),
        has_primary_symbol=has_primary_symbol,
        has_callers=has_callers,
        has_related_tests=has_related_tests,
        has_active_rules=has_active_rules,
        has_recent_history=has_recent_history,
        stale_exclusion_pct=pack.stale_exclusion_pct,
        dedup_savings_pct=pack.dedup_savings_pct,
        graph_expansion_files=pack.graph_expansion_files,
        verdict=verdict,
        missing_signals=missing,
    )


def build_quality_warning(quality: ContextQualityScore, task_description: str) -> str:
    """Build the quality warning message shown to the developer."""
    score_pct = int(quality.total * 100)
    emoji = "🔴" if quality.verdict in ("poor", "rebuild") else "🟡"

    lines = [
        f"{emoji} Context quality: {quality.verdict.upper()} ({score_pct}/100)",
        "",
        "Missing from this context pack:",
    ]
    for signal in quality.missing_signals:
        lines.append(f"  ✗ {signal}")

    if not quality.missing_signals:
        lines.append("  (all signals present)")

    lines += [
        "",
        "Options:",
        "  [Rebuild Index Now]",
        "  [Continue Anyway — Lower Accuracy Expected]",
        "  [Cancel]",
    ]
    return "\n".join(lines)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _has_primary_symbol(pack: ContextPackSnapshot) -> bool:
    """Check whether the primary symbol is represented in the context files."""
    if not pack.primary_symbol:
        return bool(pack.files)  # no named symbol — presence of any file is enough
    name_lower = pack.primary_symbol.lower()
    return any(name_lower in fp.lower() for fp in pack.files) or "primary" in pack.source_types


def _has_related_tests(pack: ContextPackSnapshot) -> bool:
    """Check whether at least one test file is included."""
    return any(
        "test" in fp.lower() or fp.startswith("tests/")
        for fp in pack.files
    ) or "test" in pack.source_types
