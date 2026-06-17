"""Memory suggestion ranking and decay helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class MemorySuggestion:
    id: str
    title: str
    body: str
    source_path: str | None
    memory_class: str
    derived_from: str | None
    contradicts_memory_id: str | None
    rank_score: float = 0.0


RankedMemorySuggestion = MemorySuggestion


def rank_memory_suggestions(
    suggestions: Iterable[MemorySuggestion],
    file_changed_checker: Callable[[str | None], bool],
    module_task_frequency_getter: Callable[[str | None], float],
) -> list[RankedMemorySuggestion]:
    ranked: list[RankedMemorySuggestion] = []
    for suggestion in suggestions:
        score = 0.0
        if file_changed_checker(suggestion.source_path):
            score += 0.40
        if suggestion.memory_class in {"instruction", "decision"}:
            score += 0.25
        score += min(max(float(module_task_frequency_getter(suggestion.source_path)), 0.0), 0.20)
        if suggestion.derived_from == "validation_result":
            score += 0.15
        if suggestion.contradicts_memory_id is not None:
            score += 0.30
        ranked.append(
            MemorySuggestion(
                id=suggestion.id,
                title=suggestion.title,
                body=suggestion.body,
                source_path=suggestion.source_path,
                memory_class=suggestion.memory_class,
                derived_from=suggestion.derived_from,
                contradicts_memory_id=suggestion.contradicts_memory_id,
                rank_score=score,
            )
        )
    return sorted(ranked, key=lambda suggestion: suggestion.rank_score, reverse=True)


def get_decay_status(memory_status: str, created_at: str, source_changed: bool) -> bool:
    if memory_status != "pending_review" or not source_changed:
        return False

    try:
        parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return False

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed <= datetime.now(UTC) - timedelta(days=14)
