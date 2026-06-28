"""LLM-based symbol summary generation."""

from __future__ import annotations

from .llm_client import BaseLLMClient

SYSTEM = (
    "You summarize Python symbols in one sentence. "
    "Be concise. Output only the summary, no preamble."
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
