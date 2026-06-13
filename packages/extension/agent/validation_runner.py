"""Validation command runner with per-command timeout handling."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config


@dataclass(frozen=True)
class ValidationCommand:
    name: str
    argv: list[str]
    timeout: int | None = None
    cwd: Path | None = None
    display_name: str | None = None


@dataclass(frozen=True)
class ValidationResult:
    name: str
    status: str
    message: str
    command: list[str] = field(default_factory=list)
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    timeout_seconds: int | None = None


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

    async def run_command(self, command: ValidationCommand) -> ValidationResult:
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
            return ValidationResult(
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
            return ValidationResult(
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

        return ValidationResult(
            name=display_name,
            status=status,
            message=message,
            command=list(command.argv),
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
            timeout_seconds=effective_timeout,
        )

    async def run_commands(self, commands: list[ValidationCommand]) -> list[ValidationResult]:
        results: list[ValidationResult] = []
        for command in commands:
            results.append(await self.run_command(command))
        return results
