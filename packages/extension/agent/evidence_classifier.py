"""Dedicated evidence source classifier for investigation mode."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EvidenceClassification:
    source_type: str
    trust_level: int
    extraction_method: str


class EvidenceSourceClassifier:
    """Classifies evidence sources and assigns trust/extraction defaults."""

    def classify(
        self,
        *,
        evidence_path: Path | None,
        source_url: str | None,
    ) -> EvidenceClassification:
        source_type = self._source_type(evidence_path=evidence_path, source_url=source_url)
        return EvidenceClassification(
            source_type=source_type,
            trust_level=self._trust_level(source_type),
            extraction_method=self._extraction_method(source_type),
        )

    def _source_type(self, *, evidence_path: Path | None, source_url: str | None) -> str:
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
        if suffix in (".xlsx", ".xlsm", ".xltx"):
            return "excel_sheet"
        if suffix == ".pdf":
            return "pdf_doc"
        if suffix in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
            return "image"
        return "text_note"

    def _trust_level(self, source_type: str) -> int:
        mapping = {
            "markdown_doc": 2,
            "text_log": 3,
            "csv_data": 3,
            "api_payload": 3,
            "excel_sheet": 3,
            "pdf_doc": 2,
            "external_work_item": 3,
            "image": 5,
            "text_note": 2,
        }
        return mapping.get(source_type, 3)

    def _extraction_method(self, source_type: str) -> str:
        mapping = {
            "markdown_doc": "text_parsing",
            "text_log": "text_parsing",
            "csv_data": "column_parsing",
            "api_payload": "payload_parsing",
            "excel_sheet": "excel_parsing",
            "pdf_doc": "pdf_text_parsing",
            "external_work_item": "work_item_summary",
            "image": "ocr_required",
            "text_note": "text_parsing",
        }
        return mapping.get(source_type, "text_parsing")
