"""LLM-based task analyzer with fallback to heuristics.

Provides enhanced task analysis using LLM when available, with graceful
fallback to heuristic-based analysis.
"""

from __future__ import annotations

import json
from typing import Any

from .config import Config
from .llm_client import build_client


class LLMTaskAnalyzer:
    """Analyzes tasks using LLM with heuristic fallback."""

    _ANALYSIS_SYSTEM_PROMPT = (
        "You are a software engineering expert. Analyze the given task and provide a JSON response with: "
        "intent_summary (brief 1-line summary), complexity (low/medium/high), "
        "risk (low/medium/high), estimated_task_type (bug_fix/refactor/feature/test/document/other), "
        "and suggested_mode (fix/refactor/test/document/auto). "
        "Respond ONLY with valid JSON, no markdown or explanation."
    )

    def __init__(self, config: Config) -> None:
        self._config = config

    async def analyze_with_fallback(
        self,
        task_description: str,
        heuristic_result: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Analyze task using LLM, fall back to heuristics on failure.
        
        Args:
            task_description: The task description to analyze
            heuristic_result: Dict with keys: complexity, risk, task_type, suggested_mode
            
        Returns:
            Enhanced analysis dict with LLM results or original heuristics
        """
        # Try to get an LLM provider
        provider_chain = ["ollama", "anthropic", "openai", "lmstudio"]
        configured_order = self._config.get("fallback_order", provider_chain)

        for provider in configured_order:
            if provider == "host":
                # Skip host models for task analysis (no LLM_REQUEST events)
                continue

            try:
                if provider == "ollama":
                    client = build_client("ollama", self._config)
                elif provider == "anthropic":
                    if not self._config.get("anthropic_api_key"):
                        continue
                    client = build_client("anthropic", self._config)
                elif provider == "openai":
                    if not self._config.get("openai_api_key"):
                        continue
                    client = build_client("openai", self._config)
                elif provider == "lmstudio":
                    if not self._config.get("lmstudio_model"):
                        continue
                    client = build_client("lmstudio", self._config)
                else:
                    continue

                # Call LLM for analysis
                response = await client.complete(
                    self._ANALYSIS_SYSTEM_PROMPT,
                    f"Task: {task_description}",
                    max_tokens=256,
                )

                # Parse response
                llm_result = self._parse_llm_response(response.content)
                if llm_result:
                    return self._merge_results(heuristic_result, llm_result)

            except Exception:
                # Continue to next provider
                continue

        # All LLM providers failed or unavailable — return heuristic result
        return heuristic_result

    def _parse_llm_response(self, response_text: str) -> dict[str, Any] | None:
        """Parse LLM response JSON."""
        try:
            # Extract JSON if wrapped in markdown
            text = response_text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]

            result = json.loads(text)
            return result
        except (json.JSONDecodeError, IndexError, ValueError):
            return None

    def _merge_results(
        self,
        heuristic: dict[str, Any],
        llm: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge LLM results with heuristic fallback."""
        merged = heuristic.copy()

        # Use LLM results if valid, otherwise keep heuristic
        llm_complexity = llm.get("complexity", "").lower()
        if llm_complexity in ("low", "medium", "high"):
            merged["complexity"] = llm_complexity

        llm_risk = llm.get("risk", "").lower()
        if llm_risk in ("low", "medium", "high"):
            merged["risk"] = llm_risk

        llm_type = llm.get("estimated_task_type", "").lower()
        if llm_type and llm_type != "other":
            merged["task_type"] = llm_type

        llm_mode = llm.get("suggested_mode", "").lower()
        if llm_mode in ("fix", "refactor", "test", "document", "auto"):
            merged["suggested_mode"] = llm_mode

        llm_intent = llm.get("intent_summary", "").strip()
        if llm_intent and len(llm_intent) > 5:
            merged["intent_summary"] = llm_intent[:100]

        return merged
