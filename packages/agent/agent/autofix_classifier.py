"""Autofix classifier: determine which diagnostics are safe for automatic fixing.

Safe diagnostics are low-risk lint/type errors that can be fixed with cheap_cloud
at LOW approval tier. Unsafe diagnostics require normal Patch mode.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class AutofixSafety(str, Enum):
    SAFE = "safe"
    REQUIRES_MANUAL = "requires_manual"


@dataclass(frozen=True)
class AutofixCandidate:
    file_path: str
    line: int
    code: str
    message: str
    safety: AutofixSafety
    category: str


# Patterns that are safe for automatic fixing (lint/format/type annotation issues)
AUTOFIX_SAFE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("unused_import", re.compile(r"(?i)(unused|unresolved)\s+import|F401|W0611")),
    ("line_too_long", re.compile(r"(?i)line\s+too\s+long|E501|C0301")),
    ("trailing_comma", re.compile(r"(?i)missing\s+trailing\s+comma|COM812")),
    ("trailing_whitespace", re.compile(r"(?i)trailing\s+whitespace|W291|W293|C0303")),
    ("blank_line", re.compile(r"(?i)(too\s+many|expected)\s+blank\s+lines?|E301|E302|E303")),
    ("missing_type_annotation", re.compile(r"(?i)type\s+annotation\s+missing|ANN")),
    ("unreachable_code", re.compile(r"(?i)unreachable\s+(code|statement)")),
    ("deprecated_call", re.compile(r"(?i)deprecated\s+(function|method|call)|W0deprecated")),
    ("undefined_variable_simple", re.compile(r"(?i)undefined\s+(name|variable)|F821")),
    ("missing_return_type", re.compile(r"(?i)missing\s+return\s+type")),
    ("redundant_pass", re.compile(r"(?i)redundant\s+pass|PIE790")),
    ("unnecessary_semicolon", re.compile(r"(?i)unnecessary\s+semicolon|E703")),
    ("isort_violation", re.compile(r"(?i)import.*(sort|order)|I001|I002|isort")),
    ("formatting", re.compile(r"(?i)formatting|black|prettier")),
]

# Patterns that MUST NOT be auto-fixed (require human judgment)
AUTOFIX_UNSAFE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("logic_error", re.compile(r"(?i)logic\s+error|incorrect\s+(result|output)")),
    ("test_failure", re.compile(r"(?i)test\s+(failure|failed)|assert(ion)?\s+(error|failed)")),
    ("security_vulnerability", re.compile(
        r"(?i)security|vulnerab|injection|xss|csrf|auth(entication|orization)\s+bypass"
    )),
    ("breaking_api_change", re.compile(r"(?i)breaking\s+(change|api)|backward.?compat")),
    ("data_loss", re.compile(r"(?i)data\s+loss|truncat|corrupt")),
    ("race_condition", re.compile(r"(?i)race\s+condition|deadlock|thread.?safe")),
    ("type_incompatible", re.compile(
        r"(?i)incompatible\s+type|argument.*has\s+incompatible|mypy.*error"
    )),
]


def classify_diagnostic(
    *,
    code: str,
    message: str,
    file_path: str = "",
    line: int = 0,
) -> AutofixCandidate:
    """Classify a single diagnostic as safe or requiring manual intervention.

    Checks against unsafe patterns first (deny-list takes priority),
    then checks safe patterns. If neither matches, defaults to requires_manual.
    """
    combined = f"{code} {message}"

    # Check unsafe patterns first (deny-list wins)
    for category, pattern in AUTOFIX_UNSAFE_PATTERNS:
        if pattern.search(combined):
            return AutofixCandidate(
                file_path=file_path,
                line=line,
                code=code,
                message=message,
                safety=AutofixSafety.REQUIRES_MANUAL,
                category=category,
            )

    # Check safe patterns
    for category, pattern in AUTOFIX_SAFE_PATTERNS:
        if pattern.search(combined):
            return AutofixCandidate(
                file_path=file_path,
                line=line,
                code=code,
                message=message,
                safety=AutofixSafety.SAFE,
                category=category,
            )

    # Unknown diagnostic — default to manual
    return AutofixCandidate(
        file_path=file_path,
        line=line,
        code=code,
        message=message,
        safety=AutofixSafety.REQUIRES_MANUAL,
        category="unknown",
    )


def classify_diagnostics(
    diagnostics: list[dict[str, object]],
) -> tuple[list[AutofixCandidate], list[AutofixCandidate]]:
    """Classify a list of diagnostics into safe and manual groups.

    Args:
        diagnostics: List of dicts with keys: file_path, line, code, message

    Returns:
        Tuple of (safe_candidates, manual_candidates)
    """
    safe: list[AutofixCandidate] = []
    manual: list[AutofixCandidate] = []

    for diag in diagnostics:
        candidate = classify_diagnostic(
            code=str(diag.get("code", "")),
            message=str(diag.get("message", "")),
            file_path=str(diag.get("file_path", "")),
            line=int(diag.get("line", 0)),
        )
        if candidate.safety == AutofixSafety.SAFE:
            safe.append(candidate)
        else:
            manual.append(candidate)

    return safe, manual
