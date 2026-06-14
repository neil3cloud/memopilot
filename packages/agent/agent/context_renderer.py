"""Renders a context pack as bounded Markdown for LLM consumption (tool mode)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# Rough token estimator: ~4 chars per token
def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass
class RenderedSection:
    header: str
    content: str
    tokens: int


class ContextPackRenderer:
    """Produces bounded Markdown from context pack data for LLM tool output."""

    def render(
        self,
        *,
        caller: str,
        task_description: str,
        active_rules: list[dict[str, Any]] | None = None,
        active_skills: list[dict[str, Any]] | None = None,
        memory_items: list[dict[str, Any]] | None = None,
        file_snippets: list[dict[str, Any]] | None = None,
        stale_exclusion_count: int = 0,
        stale_affected_modules: list[str] | None = None,
        redacted_values_count: int = 0,
        max_tokens: int = 8000,
    ) -> str:
        """Render a context pack as Markdown bounded to max_tokens."""
        sections: list[str] = []
        token_count = 0

        # Governance note (always first, always included)
        governance = self._render_governance_note(caller)
        if governance:
            sections.append(governance)
            token_count += estimate_tokens(governance)

        # Task section (always included)
        task_section = f"## Task\n\n{task_description}\n"
        sections.append(task_section)
        token_count += estimate_tokens(task_section)

        # Active rules
        if active_rules:
            section = self._render_rules(active_rules)
            section_tokens = estimate_tokens(section)
            if token_count + section_tokens <= max_tokens:
                sections.append(section)
                token_count += section_tokens

        # Active skills
        if active_skills:
            section = self._render_skills(active_skills)
            section_tokens = estimate_tokens(section)
            if token_count + section_tokens <= max_tokens:
                sections.append(section)
                token_count += section_tokens

        # Memory items
        if memory_items:
            section = self._render_memory(memory_items)
            section_tokens = estimate_tokens(section)
            if token_count + section_tokens <= max_tokens:
                sections.append(section)
                token_count += section_tokens

        # File snippets (truncated to fit remaining budget)
        truncated_files = 0
        if file_snippets:
            remaining = max_tokens - token_count
            section, truncated_files = self._render_files(file_snippets, remaining)
            if section:
                sections.append(section)
                token_count += estimate_tokens(section)

        # Stale memory notice
        if stale_exclusion_count > 0:
            modules = ", ".join(stale_affected_modules or [])
            notice = (
                f"\n## Memory Health Notice\n\n"
                f"⚠ {stale_exclusion_count} memory items were excluded because "
                f"they are stale (source files changed since last index). "
            )
            if modules:
                notice += f"Affected modules: {modules}. "
            notice += "Run `MemoPilot: Rebuild Memory` to refresh.\n"
            sections.append(notice)

        # Redaction notice
        if redacted_values_count > 0:
            sections.append(
                f"\n_Note: {redacted_values_count} value(s) were redacted before "
                f"this context was generated (detected secrets)._\n"
            )

        # Truncation notice
        if truncated_files > 0:
            sections.append(
                f"\n_Note: Context pack truncated to {max_tokens:,} tokens. "
                f"{truncated_files} additional files were available but excluded "
                f"to stay within the output limit. For full context, use the "
                f"MemoPilot native task flow._\n"
            )

        return "\n".join(sections)

    def render_rules_only(
        self,
        *,
        caller: str,
        active_rules: list[dict[str, Any]] | None = None,
        active_skills: list[dict[str, Any]] | None = None,
    ) -> str:
        """Render only active rules and skills (for memopilot_rules tool)."""
        sections: list[str] = []

        if caller in ("copilot_lm_tool", "cursor_mcp_tool"):
            sections.append(
                "## MemoPilot Active Rules\n\n"
                "These rules and constraints apply to the current workspace. "
                "Follow them when generating or modifying code.\n"
            )

        if active_rules:
            sections.append(self._render_rules(active_rules))
        else:
            sections.append("_No active rules configured for this workspace._\n")

        if active_skills:
            sections.append(self._render_skills(active_skills))

        return "\n".join(sections)

    def render_workspace_profile(
        self,
        *,
        caller: str,
        profile: dict[str, Any],
    ) -> str:
        """Render workspace profile as Markdown."""
        lines = ["## MemoPilot Workspace Profile\n"]

        if profile.get("language"):
            lines.append(f"**Language:** {profile['language']}")
        if profile.get("frameworks"):
            lines.append(f"**Frameworks:** {', '.join(profile['frameworks'])}")
        if profile.get("test_command"):
            lines.append(f"**Test Command:** `{profile['test_command']}`")
        if profile.get("lint_command"):
            lines.append(f"**Lint Command:** `{profile['lint_command']}`")
        if profile.get("active_rule_files"):
            lines.append(f"**Active Rule Files:** {len(profile['active_rule_files'])}")
        if profile.get("active_skills"):
            lines.append(f"**Active Skills:** {len(profile['active_skills'])}")
        if profile.get("memory_item_count") is not None:
            lines.append(f"**Memory Items:** {profile['memory_item_count']}")
        if profile.get("stale_count") is not None:
            lines.append(f"**Stale Items:** {profile['stale_count']}")

        return "\n".join(lines) + "\n"

    def render_memory_search(
        self,
        *,
        caller: str,
        items: list[dict[str, Any]],
        query: str,
    ) -> str:
        """Render memory search results as Markdown."""
        if not items:
            return f"## MemoPilot Memory Search\n\nNo results found for: \"{query}\"\n"

        lines = [f"## MemoPilot Memory Search — \"{query}\"\n"]
        lines.append(f"_{len(items)} result(s)_\n")

        for item in items:
            trust = item.get("trust_level", 0)
            stars = "★" * trust + "☆" * (5 - trust)
            title = item.get("title", "Untitled")
            mem_class = item.get("memory_class", "unknown")
            body = item.get("body", "")[:400]
            if len(item.get("body", "")) > 400:
                body += "..."
            source = item.get("source", "")

            lines.append(f"### {title} [{mem_class}] {stars}")
            lines.append(body)
            if source:
                lines.append(f"_Source: {source}_\n")

        return "\n".join(lines)

    def _render_governance_note(self, caller: str) -> str:
        if caller in ("copilot_lm_tool", "cursor_mcp_tool"):
            return (
                "## MemoPilot Context — Tool Mode\n\n"
                "This context was generated by MemoPilot's local memory and rule system. "
                "It contains governed, trust-filtered, secret-redacted project context. "
                "**Note:** MemoPilot did not generate this patch and cannot govern its "
                "application. After applying any changes, call `memopilot_patch_review` "
                "to check rule compliance and risk level.\n"
            )
        return ""

    def _render_rules(self, rules: list[dict[str, Any]]) -> str:
        lines = ["## Active Rules (Must Follow)\n"]
        for rule in rules:
            scope = rule.get("scope", "workspace")
            text = rule.get("rule_text", rule.get("text", ""))
            rule_id = rule.get("rule_id", "")
            prefix = f"[{rule_id}] " if rule_id else f"[{scope}] "
            lines.append(f"- {prefix}{text}")
        return "\n".join(lines) + "\n"

    def _render_skills(self, skills: list[dict[str, Any]]) -> str:
        lines = ["## Active Skills and Constraints\n"]
        for skill in skills:
            name = skill.get("name", "unnamed")
            desc = skill.get("description", "")
            lines.append(f"- **{name}**: {desc}")
        return "\n".join(lines) + "\n"

    def _render_memory(self, items: list[dict[str, Any]]) -> str:
        lines = ["## Relevant Project Memory\n"]
        for item in items:
            trust = item.get("trust_level", 0)
            stars = "★" * trust + "☆" * (5 - trust)
            title = item.get("title", "Untitled")
            mem_class = item.get("memory_class", "unknown")
            body = item.get("body", "")[:500]
            if len(item.get("body", "")) > 500:
                body += "..."
            source = item.get("source", "")

            lines.append(f"### {title} [{mem_class}] {stars}")
            lines.append(body)
            if source:
                lines.append(f"_Source: {source}_\n")

        return "\n".join(lines)

    def _render_files(
        self,
        files: list[dict[str, Any]],
        max_tokens: int,
    ) -> tuple[str, int]:
        """Render file snippets, truncating to fit max_tokens."""
        if max_tokens <= 100:
            return ("", len(files))

        lines = ["## Relevant Code\n"]
        token_count = estimate_tokens(lines[0])
        included = 0
        truncated = 0

        for file_item in files:
            path = file_item.get("path", "unknown")
            content = file_item.get("content", "")
            entry = f"### `{path}`\n```\n{content}\n```\n"
            entry_tokens = estimate_tokens(entry)

            if token_count + entry_tokens <= max_tokens:
                lines.append(entry)
                token_count += entry_tokens
                included += 1
            else:
                truncated += 1

        if included == 0:
            return ("", len(files))

        return ("\n".join(lines), truncated)
