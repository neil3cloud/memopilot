"""LLM-based context compression for context assembly."""

from __future__ import annotations

from .llm_client import BaseLLMClient

SYSTEM = (
    "You are a context synthesizer. Given a coding task and raw workspace context, "
    "return only the most relevant sections, compressed and reordered by relevance. "
    "Preserve all file paths and line numbers exactly. Output markdown only."
)


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
        # Cap at 6000 chars (~1500 tokens) so input + output fits within a 4096-token local model.
        # Larger models or cloud providers can handle bigger inputs.
        user = f"Task: {task}\n\n{raw_markdown[:6000]}"
        response = await self._client.complete(SYSTEM, user, max_tokens=min(max_tokens, 1000))
        return response.content.strip()
