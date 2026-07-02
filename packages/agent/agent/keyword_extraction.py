"""Shared keyword extraction for task-description-driven search/ranking.

Used by both file-level candidate selection (api.py) and symbol-level
relevance ranking (symbol_ranker.py) — kept in its own module so neither
has to import the other.
"""

from __future__ import annotations

import re

INDEX_KEYWORD_STOPWORDS = {
    "also",
    "about",
    "across",
    "after",
    "agent",
    "before",
    "build",
    "change",
    "code",
    "describe",
    "does",
    "explain",
    "find",
    "from",
    "have",
    "help",
    "into",
    "just",
    "list",
    "look",
    "make",
    "mode",
    "need",
    "project",
    "review",
    "search",
    "show",
    "tell",
    "that",
    "their",
    "them",
    "there",
    "these",
    "they",
    "this",
    "those",
    "task",
    "understand",
    "want",
    "what",
    "when",
    "where",
    "with",
    "work",
}


def _split_camel_case(token: str) -> list[str]:
    """Split a camelCase or PascalCase token into its constituent words.

    "ReservationService" -> ["reservation", "service"]
    "getById"            -> ["get", "by", "id"]  (but "id" is <4 so filtered later)
    """
    parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)", token)
    return [p.lower() for p in parts]


def extract_search_keywords(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]+", text.lower())
    raw_tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]+", text)
    keywords: list[str] = []
    seen: set[str] = set()

    for raw, lower in zip(raw_tokens, tokens):
        if lower in seen or len(lower) < 4 or lower in INDEX_KEYWORD_STOPWORDS:
            continue
        seen.add(lower)
        keywords.append(lower)
        for part in _split_camel_case(raw):
            if len(part) >= 4 and part not in seen and part not in INDEX_KEYWORD_STOPWORDS:
                seen.add(part)
                keywords.append(part)
        if len(keywords) >= 20:
            break
    return keywords
