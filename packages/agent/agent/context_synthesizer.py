"""LLM-based context compression for context assembly."""

from __future__ import annotations

from .llm_client import BaseLLMClient

SYSTEM = (
    "You are a context synthesizer. Given a coding task and raw workspace context, "
    "return only the most relevant sections, compressed and reordered by relevance. "
    "Preserve all file paths and line numbers exactly. Output markdown only."
)


def build_synthesis_user_prompt(task: str, raw_markdown: str, max_chars: int = 6000) -> str:
    """Build a bounded synthesis prompt while preserving both head and tail context."""
    if max_chars <= 0:
        return f"Task: {task}\n\n"

    if len(raw_markdown) <= max_chars:
        bounded = raw_markdown
    else:
        head = max_chars // 2
        tail = max_chars - head
        bounded = (
            f"{raw_markdown[:head]}\n\n"
            "[... middle content omitted for synthesis budget ...]\n\n"
            f"{raw_markdown[-tail:]}"
        )
    return f"Task: {task}\n\n{bounded}"


class ContextSynthesizer:
    def __init__(self, client: BaseLLMClient) -> None:
        self._client = client

    async def synthesize(
        self,
        *,
        task: str,
        raw_markdown: str,
        max_tokens: int = 4000,
    ) -> str:
        # Cap synthesis payload while preserving both top and tail sections.
        user = build_synthesis_user_prompt(task=task, raw_markdown=raw_markdown, max_chars=6000)
        response = await self._client.complete(SYSTEM, user, max_tokens=min(max_tokens, 1000))
        return response.content.strip()
