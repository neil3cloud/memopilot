"""LLM-based symbol summary generation."""

from __future__ import annotations

import logging
import re

from .llm_client import BaseLLMClient

logger = logging.getLogger(__name__)

SYSTEM = (
    "You summarize Python symbols in one sentence. "
    "Be concise. Output only the summary, no preamble."
)

BATCH_SYSTEM = (
    "You summarize Python symbols. "
    "You will receive multiple symbols numbered 1, 2, 3, etc. "
    "Reply with exactly one line per symbol in the format:\n"
    "1. <one sentence summary>\n"
    "2. <one sentence summary>\n"
    "Output only the numbered list, no preamble, no extra lines."
)


class SymbolSummarizer:
    def __init__(self, client: BaseLLMClient) -> None:
        self._client = client

    async def summarize(
        self,
        *,
        name: str,
        kind: str,
        signature: str,
        source: str,
    ) -> str:
        user = f"Symbol: {kind} `{name}`\nSignature: {signature}\n\n```python\n{source[:1200]}\n```"
        response = await self._client.complete(SYSTEM, user, max_tokens=120)
        return response.content.strip()

    async def summarize_batch(
        self,
        symbols: list[dict],
    ) -> dict[int, str]:
        """Summarize multiple symbols in one LLM call.

        Args:
            symbols: list of dicts with keys: id, name, kind, signature, source

        Returns:
            dict mapping symbol id → summary string
        """
        if not symbols:
            return {}

        parts: list[str] = []
        for i, sym in enumerate(symbols, 1):
            source_snippet = sym["source"][:800]
            parts.append(
                f"Symbol {i}: {sym['kind']} `{sym['name']}`\n"
                f"Signature: {sym['signature']}\n"
                f"```python\n{source_snippet}\n```"
            )

        user = "\n\n".join(parts)
        max_tokens = 120 * len(symbols)
        logger.info("summarize_batch: sending %d symbols, prompt_len=%d", len(symbols), len(user))
        response = await self._client.complete(BATCH_SYSTEM, user, max_tokens=max_tokens)
        logger.info("summarize_batch: response_len=%d content_preview=%s", len(response.content), response.content[:100])
        result = _parse_batch_response(response.content, symbols)
        logger.info("summarize_batch: parsed %d/%d summaries", len(result), len(symbols))
        return result


def _parse_batch_response(content: str, symbols: list[dict]) -> dict[int, str]:
    """Parse numbered list response back to symbol id → summary."""
    result: dict[int, str] = {}
    for line in content.splitlines():
        line = line.strip()
        m = re.match(r"^(\d+)\.\s+(.+)$", line)
        if m:
            idx = int(m.group(1))
            summary = m.group(2).strip()
            if 1 <= idx <= len(symbols) and summary:
                result[symbols[idx - 1]["id"]] = summary
    return result
