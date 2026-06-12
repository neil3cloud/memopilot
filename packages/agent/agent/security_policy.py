"""Security policies for credential redaction and DB write blocking."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

WRITE_SQL_PATTERN = re.compile(
    r"^\s*(insert|update|delete|drop|alter|create|replace|truncate|attach|vacuum|reindex)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RedactionResult:
    redacted_text: str
    redacted_count: int


@dataclass(frozen=True)
class DBWriteCheckResult:
    blocked: bool
    reason: str | None


class CredentialRedactor:
    """Redacts common credential formats from input text."""

    def __init__(self) -> None:
        self._patterns = [
            re.compile(
                r"(?i)\b(api[_-]?key|token|password|secret)\b(\s*[:=]\s*)([^\s,;\"'}]+|\"[^\"]+\"|'[^']+')"
            ),
            re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]+"),
            re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
        ]

    def redact(self, text: str) -> RedactionResult:
        redacted = text
        total = 0
        for pattern in self._patterns:
            redacted, count = pattern.subn(self._replacement, redacted)
            total += count

        detect_secret_findings = self._scan_with_detect_secrets(redacted)
        redacted, detect_count = self._apply_detect_secrets_redaction(
            text=redacted,
            findings=detect_secret_findings,
        )
        total += detect_count
        return RedactionResult(redacted_text=redacted, redacted_count=total)

    def _replacement(self, match: re.Match[str]) -> str:
        groups = match.groups()
        marker = "[REDACTED:pattern-match]"
        if len(groups) >= 3:
            return f"{groups[0]}{groups[1]}{marker}"
        return marker

    def _scan_with_detect_secrets(self, text: str) -> dict[int, str]:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".txt",
            delete=False,
            encoding="utf-8",
        ) as temp_file:
            temp_file.write(text)
            temp_path = Path(temp_file.name)

        try:
            result = subprocess.run(
                [sys.executable, "-m", "detect_secrets", "scan", str(temp_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip())

            payload = json.loads(result.stdout or "{}")
            raw_results = payload.get("results", {})
            findings: dict[int, str] = {}
            for file_findings in raw_results.values():
                for finding in file_findings:
                    line_number = int(finding.get("line_number", 0))
                    secret_type = str(finding.get("type", "detected-secret")).lower()
                    if line_number > 0:
                        findings[line_number] = secret_type
            return findings
        finally:
            temp_path.unlink(missing_ok=True)

    def _apply_detect_secrets_redaction(
        self,
        *,
        text: str,
        findings: dict[int, str],
    ) -> tuple[str, int]:
        if not findings:
            return text, 0

        lines = text.splitlines()
        redacted_count = 0
        for line_number, secret_type in findings.items():
            index = line_number - 1
            if index < 0 or index >= len(lines):
                continue
            if "[REDACTED" in lines[index]:
                continue
            lines[index] = self._redact_detected_line(lines[index], secret_type)
            redacted_count += 1

        return "\n".join(lines), redacted_count

    def _redact_detected_line(self, line: str, secret_type: str) -> str:
        assignment = re.match(r"^(?P<key>[^:=\s]+)(?P<sep>\s*[:=]\s*)(?P<value>.+)$", line.strip())
        marker = f"[REDACTED:{secret_type}]"
        if assignment:
            return f"{assignment.group('key')}{assignment.group('sep')}{marker}"
        return marker


class DatabaseWriteBlocker:
    """Blocks SQL write operations from tool payloads."""

    def check_statement(self, statement: str) -> DBWriteCheckResult:
        fragments = [part.strip() for part in statement.split(";") if part.strip()]
        for fragment in fragments:
            if WRITE_SQL_PATTERN.match(fragment):
                return DBWriteCheckResult(
                    blocked=True,
                    reason="db_write_blocked_by_policy",
                )
        return DBWriteCheckResult(blocked=False, reason=None)

    def check_payload(self, payload_json: str) -> DBWriteCheckResult:
        try:
            parsed = json.loads(payload_json)
        except json.JSONDecodeError:
            return self.check_statement(payload_json)

        for value in self._iterate_strings(parsed):
            result = self.check_statement(value)
            if result.blocked:
                return result
        return DBWriteCheckResult(blocked=False, reason=None)

    def _iterate_strings(self, value: Any):
        if isinstance(value, str):
            yield value
            return
        if isinstance(value, list):
            for item in value:
                yield from self._iterate_strings(item)
            return
        if isinstance(value, dict):
            for item in value.values():
                yield from self._iterate_strings(item)
