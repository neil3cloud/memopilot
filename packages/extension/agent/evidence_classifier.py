"""Deterministic evidence source classifier for investigation mode."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_STACK_TRACE_ERROR_PATTERN = re.compile(r"(?ms)^Error:\s+.+(?:\r?\n[ \t]+.+)+")
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
_SCREENSHOT_TOKENS = ("screenshot", "screen", "capture", "snip", "ui", "modal", "dialog")


@dataclass(frozen=True)
class EvidenceClassification:
    source_type: str
    trust_level: int
    extraction_method: str


def classify_evidence(file_path: str, content_preview: str | None = None) -> tuple[str, int]:
    """Returns (source_type, trust_level)."""
    path = Path(file_path)
    suffix = path.suffix.lower()
    name = path.name.lower()
    preview = content_preview or ""

    if suffix in {".log", ".txt"}:
        if _looks_like_stack_trace(preview):
            return "stack_trace", 4
        return "text_log", 3
    if suffix == ".md":
        return "markdown_doc", 4
    if suffix == ".csv":
        return "csv_data", 3
    if suffix in {".xlsx", ".xls", ".xlsm", ".xltx"}:
        return "spreadsheet", 3
    if suffix == ".pdf":
        return "pdf_doc", 3
    if suffix in {".json", ".xml"}:
        return "api_payload", 3
    if suffix in {".sql", ".ddl"}:
        return "database_schema", 4
    if suffix in {".py", ".ts", ".cs", ".java"}:
        return "existing_code", 5
    if suffix == ".docx":
        return "word_doc", 3
    if suffix == ".pptx":
        return "powerpoint_doc", 3
    if suffix in _IMAGE_SUFFIXES:
        if any(token in name for token in _SCREENSHOT_TOKENS):
            return "screenshot", 4
        return "image", 5
    return "unknown", 2


class EvidenceSourceClassifier:
    """Classifies evidence sources and assigns trust/extraction defaults."""

    def classify(
        self,
        *,
        evidence_path: Path | None,
        source_url: str | None,
        content_preview: str | None = None,
    ) -> EvidenceClassification:
        if source_url:
            source_type = "external_work_item"
            trust_level = 3
        else:
            source_type, trust_level = classify_evidence(
                str(evidence_path) if evidence_path is not None else "",
                content_preview=content_preview,
            )
        return EvidenceClassification(
            source_type=source_type,
            trust_level=trust_level,
            extraction_method=self._extraction_method(source_type),
        )

    def _extraction_method(self, source_type: str) -> str:
        mapping = {
            "stack_trace": "stack_trace_parsing",
            "text_log": "text_parsing",
            "markdown_doc": "text_parsing",
            "csv_data": "column_parsing",
            "spreadsheet": "excel_parsing",
            "pdf_doc": "pdf_text_parsing",
            "api_payload": "payload_parsing",
            "database_schema": "schema_parsing",
            "existing_code": "code_parsing",
            "word_doc": "word_text_parsing",
            "powerpoint_doc": "slide_text_parsing",
            "screenshot": "image_analysis",
            "external_work_item": "work_item_summary",
            "image": "ocr_required",
            "unknown": "text_parsing",
        }
        return mapping.get(source_type, "text_parsing")


def _looks_like_stack_trace(content_preview: str) -> bool:
    if not content_preview:
        return False
    if "Traceback (most recent call last):" in content_preview:
        return True
    if "Exception" in content_preview and "  at " in content_preview:
        return True
    return bool(_STACK_TRACE_ERROR_PATTERN.search(content_preview))
