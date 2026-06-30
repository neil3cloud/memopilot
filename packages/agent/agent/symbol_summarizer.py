"""LLM-based symbol summary generation with language/framework awareness."""

from __future__ import annotations

import logging
import re

from .llm_client import BaseLLMClient

logger = logging.getLogger(__name__)

# Language-specific prompts
PROMPTS_BY_LANGUAGE = {
    "python": {
        "system": "You summarize Python symbols in one sentence. Be concise. Output only the summary, no preamble.",
        "batch_system": (
            "You summarize Python symbols. "
            "You will receive multiple symbols numbered 1, 2, 3, etc. "
            "Reply with exactly one line per symbol in the format:\n"
            "1. <one sentence summary>\n"
            "2. <one sentence summary>\n"
            "Output only the numbered list, no preamble, no extra lines."
        ),
    },
    "typescript": {
        "system": (
            "You summarize TypeScript/JavaScript symbols in one sentence. "
            "Focus on purpose and role in the codebase. Be concise. Output only the summary, no preamble."
        ),
        "batch_system": (
            "You summarize TypeScript/JavaScript symbols. "
            "You will receive multiple symbols numbered 1, 2, 3, etc. "
            "Reply with exactly one line per symbol in the format:\n"
            "1. <one sentence summary>\n"
            "2. <one sentence summary>\n"
            "Output only the numbered list, no preamble, no extra lines."
        ),
    },
    "csharp": {
        "system": (
            "You summarize C# / ASP.NET Core symbols in one sentence. "
            "Focus on business purpose and interaction with services/repositories. Be concise. "
            "Output only the summary, no preamble."
        ),
        "batch_system": (
            "You summarize C# / ASP.NET Core symbols. "
            "You will receive multiple symbols numbered 1, 2, 3, etc. "
            "Reply with exactly one line per symbol in the format:\n"
            "1. <one sentence summary>\n"
            "2. <one sentence summary>\n"
            "Output only the numbered list, no preamble, no extra lines."
        ),
    },
}

# Framework-specific context hints (language-aware)
FRAMEWORK_HINTS = {
    # Python (generic)
    "python_class": " This is a class—describe its purpose, key attributes, and main responsibilities.",
    "python_function": " This is a function—describe what it computes or what side effect it performs.",
    
    # TypeScript/JavaScript
    "react_component": (
        " This is a React component. Describe: what it renders (DOM structure), "
        "what props it accepts, what hooks it uses, and what state shape."
    ),
    "react_hook": (
        " This is a React hook. Describe: what state or effects it manages, "
        "what values it returns, and any side effects it performs."
    ),
    "angular_component": (
        " This is an Angular component. Describe: its DOM template role, "
        "what @Input/@Output bindings it has, and what services it injects."
    ),
    "angular_service": (
        " This is an Angular service. Describe: what interface it implements, "
        "what HTTP calls it makes, and what data it exposes to components."
    ),
    "angular_module": (
        " This is an Angular NgModule. Describe: what components/services it declares/provides, "
        "what other modules it imports."
    ),
    "express_route": (
        " This is an Express route handler. Describe: HTTP method, path pattern, "
        "what parameters it accepts, and what response it returns."
    ),
    "api_client": (
        " This is an API client utility. Describe: what external APIs it wraps, "
        "how it constructs requests, and what data structures it returns."
    ),
    
    # C# / ASP.NET Core
    "endpoint": (
        " This is an ASP.NET Core controller action (endpoint). Describe: HTTP method, "
        "route pattern, what dependencies it injects, and what business operation it performs."
    ),
    "controller": (
        " This is an ASP.NET Core controller. Describe: what resource(s) it manages, "
        "what CRUD or custom operations it exposes, and what services it depends on."
    ),
    "service": (
        " This is an ASP.NET Core service class. Describe: what business logic it implements, "
        "what repositories/dependencies it uses, and what interface it implements."
    ),
    "repository": (
        " This is a repository class. Describe: what entity type it manages, "
        "what database operations it performs, and what queries it implements."
    ),
    "di_injectable": (
        " This class is registered in the DI container. Describe: what interface it implements, "
        "what concrete functionality it provides, and how it's registered (scoped/transient/singleton)."
    ),
}

# Default prompts for backward compatibility
SYSTEM = PROMPTS_BY_LANGUAGE["python"]["system"]
BATCH_SYSTEM = PROMPTS_BY_LANGUAGE["python"]["batch_system"]


class SymbolSummarizer:
    def __init__(self, client: BaseLLMClient) -> None:
        self._client = client

    def _get_system_prompt(self, language: str = "python") -> str:
        """Get language-specific system prompt."""
        return PROMPTS_BY_LANGUAGE.get(language, PROMPTS_BY_LANGUAGE["python"])["system"]

    def _get_batch_system_prompt(self, language: str = "python") -> str:
        """Get language-specific batch system prompt."""
        return PROMPTS_BY_LANGUAGE.get(language, PROMPTS_BY_LANGUAGE["python"])["batch_system"]

    def _build_framework_context(
        self, 
        tags: list[str] | None,
        language: str = "python",
        kind: str = "function",
    ) -> str:
        """Build language and framework-specific context from tags.
        
        Args:
            tags: List of framework/type tags
            language: Programming language (python, typescript, csharp)
            kind: Symbol kind (class, function, method, interface, etc.)
            
        Returns:
            Formatted context string to append to prompt
        """
        if not tags:
            # No explicit tags—provide generic language-based hint
            if language == "csharp" and kind in ("class", "method"):
                if kind == "class":
                    return FRAMEWORK_HINTS.get("service", "")
                else:
                    return FRAMEWORK_HINTS.get("endpoint", "")
            return ""
        
        # Look for most specific tag first (language-aware)
        for tag in tags:
            # Try exact tag match
            if tag in FRAMEWORK_HINTS:
                return FRAMEWORK_HINTS[tag]
        
        # Fallback: provide hint based on language and kind
        if language == "csharp":
            if "endpoint" in " ".join(tags) or "route" in " ".join(tags):
                return FRAMEWORK_HINTS.get("endpoint", "")
            if "controller" in " ".join(tags):
                return FRAMEWORK_HINTS.get("controller", "")
            if "service" in " ".join(tags):
                return FRAMEWORK_HINTS.get("service", "")
            if "repository" in " ".join(tags):
                return FRAMEWORK_HINTS.get("repository", "")
        
        return ""

    async def summarize(
        self,
        *,
        name: str,
        kind: str,
        signature: str,
        source: str,
        language: str = "python",
        tags: list[str] | None = None,
    ) -> str:
        system_prompt = self._get_system_prompt(language)
        framework_context = self._build_framework_context(tags, language, kind)
        
        user = f"Symbol: {kind} `{name}`\nSignature: {signature}\n\n```{language}\n{source[:1200]}\n```{framework_context}"
        response = await self._client.complete(system_prompt, user, max_tokens=120)
        return response.content.strip()

    async def summarize_batch(
        self,
        symbols: list[dict],
        language: str = "python",
    ) -> dict[int, str]:
        """Summarize multiple symbols in one LLM call.

        Args:
            symbols: list of dicts with keys: id, name, kind, signature, source, tags (optional)
            language: programming language (default: "python")

        Returns:
            dict mapping symbol id → summary string
        """
        if not symbols:
            return {}

        batch_system = self._get_batch_system_prompt(language)
        parts: list[str] = []
        for i, sym in enumerate(symbols, 1):
            source_snippet = sym["source"][:800]
            tags = sym.get("tags", [])
            kind = sym.get("kind", "function")
            framework_context = self._build_framework_context(tags, language, kind)
            
            parts.append(
                f"Symbol {i}: {kind} `{sym['name']}`\n"
                f"Signature: {sym['signature']}\n"
                f"```{language}\n{source_snippet}\n```{framework_context}"
            )

        user = "\n\n".join(parts)
        max_tokens = 120 * len(symbols)
        logger.info("summarize_batch: sending %d symbols (language=%s), prompt_len=%d", len(symbols), language, len(user))
        response = await self._client.complete(batch_system, user, max_tokens=max_tokens)
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
