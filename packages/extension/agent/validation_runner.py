"""Validation command runner with per-command timeout handling."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .config import Config


class FailureCategory(str, Enum):
    ASSERTION_ERROR = "assertion_error"
    IMPORT_ERROR = "import_error"
    FIXTURE_ERROR = "fixture_error"
    TYPE_ERROR = "type_error"
    LINT_ERROR = "lint_error"
    TIMEOUT = "timeout"
    SYNTAX_ERROR = "syntax_error"
    UNKNOWN = "unknown"


FAILURE_HINTS: dict[FailureCategory, str] = {
    FailureCategory.ASSERTION_ERROR: (
        "Inspect {test_id} and update the assertion or the behavior under test to match the intended result."
    ),
    FailureCategory.IMPORT_ERROR: (
        "Check imports, module paths, and package exports needed by {test_id}."
    ),
    FailureCategory.FIXTURE_ERROR: (
        "Define or rename the missing fixture used by {test_id}, or update the test to request the correct fixture."
    ),
    FailureCategory.TYPE_ERROR: (
        "Review type usage around {test_id} and fix incompatible values, signatures, or annotations."
    ),
    FailureCategory.LINT_ERROR: (
        "Apply the reported lint fix for {test_id} and rerun validation."
    ),
    FailureCategory.TIMEOUT: (
        "Reduce runtime or unblock the hanging path before rerunning {test_id}."
    ),
    FailureCategory.SYNTAX_ERROR: (
        "Fix the syntax or indentation problem reported for {test_id}."
    ),
    FailureCategory.UNKNOWN: (
        "Review the failure output for {test_id} and determine the next targeted fix."
    ),
}


@dataclass(frozen=True)
class ValidationCommand:
    name: str
    argv: list[str]
    timeout: int | None = None
    cwd: Path | None = None
    display_name: str | None = None


@dataclass(frozen=True)
class ValidationCommandResult:
    name: str
    status: str
    message: str
    command: list[str] = field(default_factory=list)
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    timeout_seconds: int | None = None


@dataclass(frozen=True)
class ValidationResult:
    overall_status: str
    pre_existing_failures: list[tuple[str, str]] = field(default_factory=list)
    new_failures: list[tuple[str, str]] = field(default_factory=list)
    fixed_by_patch: list[tuple[str, str]] = field(default_factory=list)
    baseline_run: list[tuple[str, str]] = field(default_factory=list)
    post_patch_run: list[tuple[str, str]] = field(default_factory=list)
    autofix_available: bool = False
    autofix_candidates: list[dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True)
class CategorisedFailure:
    test_id: str
    category: FailureCategory
    short_output: str
    hint: str


def categorise_failure(test_id: str, output: str) -> FailureCategory:
    lowered = f"{test_id}\n{output}".lower()
    if "syntaxerror" in lowered or "indentationerror" in lowered:
        return FailureCategory.SYNTAX_ERROR
    if "modulenotfounderror" in lowered or "importerror" in lowered:
        return FailureCategory.IMPORT_ERROR
    if "fixture" in lowered and "not found" in lowered:
        return FailureCategory.FIXTURE_ERROR
    if "assertionerror" in lowered or "assert " in lowered:
        return FailureCategory.ASSERTION_ERROR
    if "timed out" in lowered or "timeout" in lowered:
        return FailureCategory.TIMEOUT
    if "typeerror" in lowered or "mypy" in lowered or "pyright" in lowered:
        return FailureCategory.TYPE_ERROR
    if "ruff" in lowered or "flake8" in lowered or "lint" in lowered:
        return FailureCategory.LINT_ERROR
    return FailureCategory.UNKNOWN


def _normalise_failed_tests(failed_tests: list[tuple[str, str]] | list[str]) -> list[tuple[str, str]]:
    normalised: list[tuple[str, str]] = []
    for item in failed_tests:
        if isinstance(item, tuple):
            normalised.append((item[0], item[1]))
        else:
            normalised.append((item, ""))
    return normalised


def _select_failures(
    source: list[tuple[str, str]],
    selected_ids: set[str],
) -> list[tuple[str, str]]:
    return [item for item in source if item[0] in selected_ids]


def classify_validation_diff(
    baseline_failed_tests: list[tuple[str, str]] | list[str],
    post_patch_failed_tests: list[tuple[str, str]] | list[str],
) -> ValidationResult:
    baseline_run = _normalise_failed_tests(baseline_failed_tests)
    post_patch_run = _normalise_failed_tests(post_patch_failed_tests)
    baseline_ids = {test_id for test_id, _ in baseline_run}
    post_patch_ids = {test_id for test_id, _ in post_patch_run}

    pre_existing = baseline_ids & post_patch_ids
    new_failures = post_patch_ids - baseline_ids
    fixed_by_patch = baseline_ids - post_patch_ids

    # Check if new failures are autofix-safe
    autofix_available = False
    autofix_candidates: list[dict[str, object]] = []
    if new_failures:
        from .autofix_classifier import classify_diagnostic, AutofixSafety
        for test_id, output in _select_failures(post_patch_run, new_failures):
            candidate = classify_diagnostic(code=test_id, message=output)
            if candidate.safety == AutofixSafety.SAFE:
                autofix_available = True
                autofix_candidates.append({
                    "file_path": candidate.file_path,
                    "line": candidate.line,
                    "code": test_id,
                    "message": output[:200],
                    "category": candidate.category,
                })

    return ValidationResult(
        overall_status="passed" if not new_failures else "failed",
        pre_existing_failures=_select_failures(baseline_run, pre_existing),
        new_failures=_select_failures(post_patch_run, new_failures),
        fixed_by_patch=_select_failures(baseline_run, fixed_by_patch),
        baseline_run=baseline_run,
        post_patch_run=post_patch_run,
        autofix_available=autofix_available,
        autofix_candidates=autofix_candidates,
    )


def _short_output(output: str, *, limit: int = 200) -> str:
    first_line = next((line.strip() for line in output.splitlines() if line.strip()), "")
    short = first_line or output.strip()
    if len(short) <= limit:
        return short
    return f"{short[: limit - 3].rstrip()}..."


def categorise_failures(failed_tests: list[tuple[str, str]]) -> list[CategorisedFailure]:
    failures: list[CategorisedFailure] = []
    for test_id, output in failed_tests:
        category = categorise_failure(test_id, output)
        short_output = _short_output(output)
        failures.append(
            CategorisedFailure(
                test_id=test_id,
                category=category,
                 short_output=short_output,
                hint=FAILURE_HINTS[category].format(
                    test_id=test_id,
                    short_output=short_output,
                ),
            )
        )
    return failures


def build_retry_context(failures: list[CategorisedFailure]) -> str:
    return "\n".join(
        f"{failure.test_id} [{failure.category.value}]\n{failure.short_output}\nHint: {failure.hint}"
        for failure in failures
    )


def should_escalate_after_retries(retry_count: int, *, max_retries: int = 2) -> bool:
    return retry_count >= max_retries


def build_escalation_message(
    retry_count: int,
    *,
    max_retries: int = 2,
    requires_approval: bool = False,
) -> str:
    if not should_escalate_after_retries(retry_count, max_retries=max_retries):
        return "Automatic retry still available."
    if requires_approval:
        return f"Escalation requires approval after {retry_count} retries."
    return f"Escalating automatically after {retry_count} retries."


class ValidationRunner:
    """Executes validation commands with bounded runtime."""

    def __init__(self, *, config: Config) -> None:
        self._config = config

    def resolve_timeout(self, requested_timeout: int | None = None) -> int:
        default_timeout = max(int(self._config.validation_default_timeout), 1)
        max_timeout = max(int(self._config.validation_max_timeout), default_timeout)
        if requested_timeout is None:
            return min(default_timeout, max_timeout)
        return max(1, min(int(requested_timeout), max_timeout))

    async def run_command(self, command: ValidationCommand) -> ValidationCommandResult:
        effective_timeout = self.resolve_timeout(command.timeout)
        display_name = command.display_name or command.name
        cwd = command.cwd or self._config.workspace_path

        try:
            process = await asyncio.create_subprocess_exec(
                *command.argv,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            return ValidationCommandResult(
                name=display_name,
                status="skipped",
                message=f"{display_name} could not be started: {exc}",
                command=list(command.argv),
            )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=effective_timeout,
            )
        except TimeoutError:
            process.kill()
            stdout_bytes, stderr_bytes = await process.communicate()
            stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
            stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
            return ValidationCommandResult(
                name=display_name,
                status="timeout",
                message=f"{display_name} timed out after {effective_timeout}s.",
                command=list(command.argv),
                stdout=stdout,
                stderr=stderr,
                timeout_seconds=effective_timeout,
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
        if process.returncode == 0:
            message = stdout or f"{display_name} passed."
            status = "pass"
        else:
            message = (
                stderr or stdout or f"{display_name} failed with exit code {process.returncode}."
            )
            status = "fail"

        return ValidationCommandResult(
            name=display_name,
            status=status,
            message=message,
            command=list(command.argv),
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
            timeout_seconds=effective_timeout,
        )

    async def run_commands(self, commands: list[ValidationCommand]) -> list[ValidationCommandResult]:
        results: list[ValidationCommandResult] = []
        for command in commands:
            results.append(await self.run_command(command))
        return results
