"""Investigation mode services for evidence-aware analysis."""

from __future__ import annotations

import csv
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .config import Config
from .db import DatabaseManager
from .security_policy import CredentialRedactor


@dataclass(frozen=True)
class AttachedEvidence:
    evidence_id: str
    source_type: str
    trust_level: int
    extraction_method: str
    extraction_status: str
    findings: list[str]
    redacted_values: int
    source_path: str | None


@dataclass(frozen=True)
class EvidenceBoardEntry:
    evidence_id: str
    source_type: str
    source_path: str | None
    source_url: str | None
    trust_level: int
    extraction_method: str
    extraction_status: str
    redacted_values: int
    findings: list[str]


@dataclass(frozen=True)
class InvestigationResult:
    context_pack: str
    context_pack_path: str
    impacted_files: list[str]
    related_tests: list[str]
    missing_test_coverage: list[str]
    evidence_count: int


class InvestigationService:
    """Implements evidence attachment and investigation context pack generation."""

    def __init__(self, *, config: Config, db: DatabaseManager) -> None:
        self._config = config
        self._db = db
        self._redactor = CredentialRedactor()

    async def attach_evidence(
        self,
        *,
        evidence_path: str | None,
        source_url: str | None,
        task_run_id: str | None,
    ) -> AttachedEvidence:
        resolved_path = self._resolve_evidence_path(evidence_path) if evidence_path else None
        source_type = self._classify_source_type(resolved_path, source_url)
        trust_level = self._trust_level_for_source(source_type)
        extraction_method = self._extraction_method_for_source(source_type)

        findings, extraction_status = self._extract_findings(
            source_type=source_type,
            evidence_path=resolved_path,
        )
        redacted_findings, redacted_values = self._redact_findings(findings)

        evidence_id = uuid.uuid4().hex
        conn = await self._db.connect()
        await conn.execute(
            """
            INSERT INTO evidence_sources
            (
                id,
                task_run_id,
                source_type,
                source_path,
                source_url,
                trust_level,
                extraction_method,
                extracted_findings_json,
                approved
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                evidence_id,
                task_run_id,
                source_type,
                str(resolved_path) if resolved_path else None,
                source_url,
                trust_level,
                extraction_method,
                json.dumps(
                    {
                        "findings": redacted_findings,
                        "extraction_status": extraction_status,
                        "redacted_values": redacted_values,
                    }
                ),
            ),
        )
        await conn.commit()

        return AttachedEvidence(
            evidence_id=evidence_id,
            source_type=source_type,
            trust_level=trust_level,
            extraction_method=extraction_method,
            extraction_status=extraction_status,
            findings=redacted_findings,
            redacted_values=redacted_values,
            source_path=str(resolved_path) if resolved_path else None,
        )

    async def list_evidence_board(self, *, task_run_id: str | None) -> list[EvidenceBoardEntry]:
        conn = await self._db.connect()
        if task_run_id:
            cursor = await conn.execute(
                """
                SELECT
                    id, source_type, source_path, source_url,
                    trust_level, extraction_method, extracted_findings_json
                FROM evidence_sources
                WHERE task_run_id = ?
                ORDER BY created_at DESC
                """,
                (task_run_id,),
            )
        else:
            cursor = await conn.execute(
                """
                SELECT
                    id, source_type, source_path, source_url,
                    trust_level, extraction_method, extracted_findings_json
                FROM evidence_sources
                ORDER BY created_at DESC
                LIMIT 100
                """
            )
        rows = await cursor.fetchall()
        return [
            EvidenceBoardEntry(
                evidence_id=row["id"],
                source_type=row["source_type"],
                source_path=row["source_path"],
                source_url=row["source_url"],
                trust_level=int(row["trust_level"]),
                extraction_method=row["extraction_method"] or "unknown",
                extraction_status=self._parse_extraction_status(
                    row["source_type"],
                    row["extracted_findings_json"],
                ),
                redacted_values=self._parse_redacted_values(row["extracted_findings_json"]),
                findings=self._parse_findings(row["extracted_findings_json"]),
            )
            for row in rows
        ]

    async def run_investigation(
        self,
        *,
        title: str,
        description: str,
        acceptance_criteria: list[str],
        task_run_id: str | None,
    ) -> InvestigationResult:
        evidence_entries = await self.list_evidence_board(task_run_id=task_run_id)
        combined_findings = [finding for entry in evidence_entries for finding in entry.findings]
        impacted_files = await self._discover_impacted_files(combined_findings)
        related_tests = await self._discover_related_tests(impacted_files)
        missing_coverage = self._detect_missing_coverage(acceptance_criteria, related_tests)
        active_rules = await self._active_rules()

        context_pack = self._build_context_pack(
            title=title,
            description=description,
            acceptance_criteria=acceptance_criteria,
            evidence_entries=evidence_entries,
            impacted_files=impacted_files,
            related_tests=related_tests,
            missing_coverage=missing_coverage,
            active_rules=active_rules,
        )
        context_pack_path = self._write_context_pack(context_pack)
        return InvestigationResult(
            context_pack=context_pack,
            context_pack_path=context_pack_path,
            impacted_files=impacted_files,
            related_tests=related_tests,
            missing_test_coverage=missing_coverage,
            evidence_count=len(evidence_entries),
        )

    def _resolve_evidence_path(self, evidence_path: str) -> Path:
        candidate = Path(evidence_path)
        if not candidate.is_absolute():
            candidate = self._config.workspace_path / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self._config.workspace_path.resolve())
        except ValueError as exc:
            raise ValueError("Evidence path must be within the workspace") from exc
        if not resolved.exists():
            raise ValueError(f"Evidence path not found: {resolved}")
        return resolved

    def _classify_source_type(self, evidence_path: Path | None, source_url: str | None) -> str:
        if source_url:
            return "external_work_item"
        if evidence_path is None:
            return "text_note"

        suffix = evidence_path.suffix.lower()
        if suffix == ".md":
            return "markdown_doc"
        if suffix == ".csv":
            return "csv_data"
        if suffix in (".txt", ".log"):
            return "text_log"
        if suffix in (".json", ".xml"):
            return "api_payload"
        if suffix in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
            return "image"
        return "text_note"

    def _trust_level_for_source(self, source_type: str) -> int:
        mapping = {
            "markdown_doc": 2,
            "text_log": 3,
            "csv_data": 3,
            "api_payload": 3,
            "external_work_item": 3,
            "image": 5,
            "text_note": 2,
        }
        return mapping.get(source_type, 3)

    def _extraction_method_for_source(self, source_type: str) -> str:
        mapping = {
            "markdown_doc": "text_parsing",
            "text_log": "text_parsing",
            "csv_data": "column_parsing",
            "api_payload": "payload_parsing",
            "external_work_item": "work_item_summary",
            "image": "ocr_required",
            "text_note": "text_parsing",
        }
        return mapping.get(source_type, "text_parsing")

    def _extract_findings(
        self,
        *,
        source_type: str,
        evidence_path: Path | None,
    ) -> tuple[list[str], str]:
        if source_type == "external_work_item":
            return [
                "External work item attached. Fetch details via MCP in future iterations."
            ], "ok"
        if evidence_path is None:
            return ["No file evidence provided."], "ok"
        if source_type == "image":
            return ["Image evidence attached; OCR interpretation pending."], "requires_ocr"
        if source_type == "csv_data":
            return self._extract_csv_findings(evidence_path), "ok"

        text = evidence_path.read_text(encoding="utf-8", errors="replace")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return lines[:30], "ok"

    def _extract_csv_findings(self, evidence_path: Path) -> list[str]:
        findings: list[str] = []
        with evidence_path.open("r", encoding="utf-8", errors="replace", newline="") as file_obj:
            reader = csv.DictReader(file_obj)
            headers = reader.fieldnames or []
            if headers:
                findings.append(f"CSV headers: {', '.join(headers)}")
            for index, row in enumerate(reader):
                if index >= 10:
                    break
                pairs = [f"{key}={value}" for key, value in row.items() if value]
                if pairs:
                    findings.append("; ".join(pairs))
        if not findings:
            findings.append("CSV contained no parsed rows.")
        return findings

    def _redact_findings(self, findings: list[str]) -> tuple[list[str], int]:
        total = 0
        redacted: list[str] = []
        for finding in findings:
            result = self._redactor.redact(finding)
            redacted.append(result.redacted_text)
            total += result.redacted_count
        return redacted, total

    async def _discover_impacted_files(self, findings: list[str]) -> list[str]:
        if not findings:
            return []
        tokens = self._important_tokens(findings)
        if not tokens:
            return []

        conn = await self._db.connect()
        file_cursor = await conn.execute("SELECT file_path FROM file_index")
        symbol_cursor = await conn.execute("SELECT DISTINCT file_path, name FROM symbols")
        file_rows = await file_cursor.fetchall()
        symbol_rows = await symbol_cursor.fetchall()

        matched: set[str] = set()
        for row in file_rows:
            file_path = str(row["file_path"])
            lowered = file_path.lower()
            if any(token in lowered for token in tokens):
                matched.add(file_path)

        for row in symbol_rows:
            file_path = str(row["file_path"])
            symbol_name = str(row["name"]).lower()
            if any(token in symbol_name for token in tokens):
                matched.add(file_path)

        return sorted(matched)[:100]

    async def _discover_related_tests(self, impacted_files: list[str]) -> list[str]:
        conn = await self._db.connect()
        cursor = await conn.execute(
            "SELECT file_path FROM file_index WHERE file_path LIKE '%test%'"
        )
        rows = await cursor.fetchall()
        candidates = [str(row["file_path"]) for row in rows]
        impacted_tokens = {
            token
            for path in impacted_files
            for token in re.split(r"[^a-zA-Z0-9_]+", path.lower())
            if len(token) >= 4
        }
        related = [
            test_file
            for test_file in candidates
            if impacted_tokens.intersection(re.split(r"[^a-zA-Z0-9_]+", test_file.lower()))
        ]
        return sorted(set(related))[:100]

    def _detect_missing_coverage(
        self,
        acceptance_criteria: list[str],
        related_tests: list[str],
    ) -> list[str]:
        if not acceptance_criteria:
            return []
        if not related_tests:
            return acceptance_criteria
        joined_tests = " ".join(related_tests).lower()
        missing: list[str] = []
        generic_tokens = {
            "should",
            "must",
            "when",
            "then",
            "with",
            "from",
            "into",
            "test",
            "tests",
            "covered",
            "coverage",
            "behavior",
            "path",
            "case",
            "cases",
        }
        for criterion in acceptance_criteria:
            tokens = [
                token
                for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_]{3,}", criterion.lower())
                if token not in generic_tokens
            ]
            if tokens and not any(token in joined_tests for token in tokens):
                missing.append(criterion)
        return missing

    async def _active_rules(self) -> list[str]:
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT rule_text
            FROM rules
            WHERE enabled = 1
            ORDER BY priority DESC, updated_at DESC
            LIMIT 20
            """
        )
        rows = await cursor.fetchall()
        return [str(row["rule_text"]) for row in rows]

    def _build_context_pack(
        self,
        *,
        title: str,
        description: str,
        acceptance_criteria: list[str],
        evidence_entries: list[EvidenceBoardEntry],
        impacted_files: list[str],
        related_tests: list[str],
        missing_coverage: list[str],
        active_rules: list[str],
    ) -> str:
        evidence_lines = [
            "- "
            f"{entry.source_type} (trust={entry.trust_level}) — "
            f"{entry.source_path or entry.source_url or 'n/a'}"
            for entry in evidence_entries
        ] or ["- none"]
        findings_lines = [
            "- "
            f"[{entry.source_type}"
            f":{entry.source_path or entry.source_url or 'n/a'}] {finding}"
            for entry in evidence_entries
            for finding in entry.findings
        ] or ["- none"]
        impacted_lines = [f"- {path}" for path in impacted_files] or ["- none"]
        test_lines = [f"- {path}" for path in related_tests] or ["- none"]
        missing_lines = [f"- {line}" for line in missing_coverage] or ["- none"]
        rule_lines = [f"- {line}" for line in active_rules] or ["- none"]
        acceptance_lines = [f"- {line}" for line in acceptance_criteria] or ["- none"]

        return "\n".join(
            [
                f"# Investigation: {title}",
                "",
                "## Source Work Item",
                description or "No work item description provided.",
                "",
                "### Acceptance Criteria",
                *acceptance_lines,
                "",
                "## Evidence Sources",
                *evidence_lines,
                "",
                "## Extracted Findings",
                *findings_lines,
                "",
                "## Impacted Code Areas",
                *impacted_lines,
                "",
                "## Related Tests",
                *test_lines,
                "",
                "## Missing Test Coverage",
                *missing_lines,
                "",
                "## Active Rules",
                *rule_lines,
                "",
                "## Constraints",
                "- Do not modify unrelated modules.",
                "- Do not auto-apply generated patches.",
                "",
                "## Expected Output",
                "- Root-cause analysis.",
                "- Implementation plan.",
                "- Test plan.",
            ]
        )

    def _write_context_pack(self, context_pack: str) -> str:
        context_pack_dir = self._config.memopilot_dir / "context-packs"
        context_pack_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"investigation-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.md"
        path = context_pack_dir / file_name
        path.write_text(context_pack, encoding="utf-8")
        return str(path)

    def _parse_findings(self, raw: str | None) -> list[str]:
        if not raw:
            return []
        value = json.loads(raw)
        if isinstance(value, dict):
            findings = value.get("findings", [])
            if isinstance(findings, list):
                return [str(item) for item in findings]
            return []
        if isinstance(value, list):
            return [str(item) for item in value]
        return []

    def _parse_extraction_status(self, source_type: str, raw: str | None) -> str:
        if not raw:
            return self._default_extraction_status(source_type)
        value = json.loads(raw)
        if isinstance(value, dict):
            status = value.get("extraction_status")
            if isinstance(status, str) and status:
                return status
        return self._default_extraction_status(source_type)

    def _parse_redacted_values(self, raw: str | None) -> int:
        if not raw:
            return 0
        value = json.loads(raw)
        if isinstance(value, dict):
            redacted = value.get("redacted_values")
            if isinstance(redacted, int) and redacted >= 0:
                return redacted
        return 0

    def _default_extraction_status(self, source_type: str) -> str:
        if source_type == "image":
            return "requires_ocr"
        return "ok"

    def _important_tokens(self, findings: list[str]) -> list[str]:
        token_set: set[str] = set()
        for finding in findings:
            for token in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{3,}", finding.lower()):
                token_set.add(token)
        return sorted(token_set)[:80]
