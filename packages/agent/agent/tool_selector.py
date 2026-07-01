"""Tool and Skill Selection Optimizer.

Pre-filters tools and skills based on task type before context pack assembly.
Reduces token waste from irrelevant tool descriptions.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolSelection:
    selected_tools: list[str] = field(default_factory=list)
    excluded_tools: list[str] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)


ALWAYS_INCLUDE = ["fts_search", "rule_resolver"]

TASK_TOOL_MAP = {
    "test_generation": ["pytest", "ruff"],
    "bug_fix": ["pytest"],
    "bounded_refactor": ["pytest", "ruff"],
    "security_change": ["mypy"],
    "billing_change": ["mypy"],
    "schema_change": ["mypy"],
    "documentation": ["ruff"],
}


def select_tools(
    task_type: str,
    available_tools: list[str],
    task_text: str = "",
) -> ToolSelection:
    """Select relevant tools for a task.

    Returns which tools to include/exclude with reasons.
    """
    selected: list[str] = []
    excluded: list[str] = []
    reasons: dict[str, str] = {}

    def include(tool: str, reason: str) -> None:
        if tool not in selected and (not available_tools or tool in available_tools):
            selected.append(tool)
            reasons[tool] = reason

    def exclude(tool: str, reason: str) -> None:
        if tool in selected:
            selected.remove(tool)
        if tool not in excluded and (
            not available_tools or tool in available_tools or tool.endswith("_mcp")
        ):
            excluded.append(tool)
        reasons[tool] = reason

    for tool in ALWAYS_INCLUDE:
        include(tool, "Always included baseline tool")

    task_tools = TASK_TOOL_MAP.get(task_type, [])
    for tool in task_tools:
        include(tool, f"Required for task_type={task_type}")

    task_text_lower = task_text.lower()
    if "ado_mcp" in available_tools:
        if (
            any(pat in task_text_lower for pat in ["ab#", "work item", "azure devops"])
            or "#" in task_text
        ):
            include("ado_mcp", "Work item reference detected in task text")
        else:
            exclude("ado_mcp", "No work item reference found")

    for tool in available_tools:
        if tool not in selected and tool not in excluded:
            exclude(tool, "Not relevant for this task type")

    return ToolSelection(selected_tools=selected, excluded_tools=excluded, reasons=reasons)
