"""Memory governance lifecycle helpers."""

from __future__ import annotations

_VALID_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "discovered": {"pending_review", "evidence_only", "confirmed"},
    "pending_review": {"confirmed", "rejected", "disputed"},
    "confirmed": {"stale", "superseded", "restricted"},
    "evidence_only": set(),
    "stale": {"confirmed"},
    "rejected": set(),
    "superseded": set(),
    "restricted": set(),
    "disputed": set(),
}


def validate_status_transition(current: str, new: str) -> bool:
    """Return True when a memory status transition is allowed."""
    current_status = (current or "discovered").strip().lower()
    new_status = (new or "discovered").strip().lower()
    return new_status in _VALID_STATUS_TRANSITIONS.get(current_status, set())
