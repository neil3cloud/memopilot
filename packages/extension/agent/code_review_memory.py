"""Code Review Memory Mode (Phase 18B).

Writes back maintainer-approved review lessons as reusable memory.
Never auto-promotes without maintainer approval.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ReviewLesson:
    """A lesson extracted from a code review."""

    summary: str
    context: str
    source_pr: str | None = None
    source_reviewer: str | None = None
    approved: bool = False


def extract_review_lessons(review_comments: list[dict]) -> list[ReviewLesson]:
    """Extract potential lessons from review comments.

    Looks for patterns indicating reusable knowledge:
    - "Always...", "Never...", "Prefer...", "Avoid..."
    - Repeated feedback patterns
    """

    lessons = []
    lesson_patterns = ["always", "never", "prefer", "avoid", "don't", "do not", "should", "must"]

    for comment in review_comments:
        body = comment.get("body", "").lower()
        if any(pattern in body for pattern in lesson_patterns):
            lessons.append(
                ReviewLesson(
                    summary=comment.get("body", "")[:200],
                    context=comment.get("path", ""),
                    source_pr=comment.get("pr_url"),
                    source_reviewer=comment.get("author"),
                )
            )
    logger.debug("Extracted %s candidate review lesson(s)", len(lessons))
    return lessons


def approve_lesson(lesson: ReviewLesson) -> dict:
    """Convert an approved lesson to a memory item dict for write-back."""

    return {
        "type": "decision",
        "title": f"Review lesson: {lesson.summary[:80]}",
        "body": lesson.summary,
        "source": f"code_review:{lesson.source_pr or 'unknown'}",
        "source_path": lesson.context,
        "trust_level": 4,
        "memory_class": "decision",
        "memory_status": "confirmed",
        "reusable": 1,
        "review_required": 0,
    }
