"""Code Review Memory Mode.

Writes back maintainer-approved review lessons as reusable memory.
Part of Phase 18B.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from .config import Config
from .db import DatabaseManager


@dataclass(frozen=True)
class ReviewEvidence:
    evidence_id: str
    pr_number: int
    body: str
    path: str | None
    line: int | None
    approved: bool


@dataclass(frozen=True)
class ReviewLesson:
    memory_item_id: str
    evidence_id: str


class CodeReviewMemoryModeService:
    """Stores review evidence and promotes approved lessons to reusable memory."""

    def __init__(self, *, config: Config, db: DatabaseManager) -> None:
        self._config = config
        self._db = db

    async def submit_review_evidence(
        self,
        *,
        pr_number: int,
        body: str,
        path: str | None = None,
        line: int | None = None,
        workspace_root: str | None = None,
    ) -> ReviewEvidence:
        normalized_root = workspace_root or str(self._config.workspace_path.resolve())
        evidence_id = uuid.uuid4().hex
        findings = [f"PR #{pr_number} review comment: {body.strip()}"]
        if path:
            findings.append(f"File: {path}")
        if line is not None:
            findings.append(f"Line: {line}")

        conn = await self._db.connect()
        await conn.execute(
            """
            INSERT INTO evidence_sources
            (
                id,
                task_run_id,
                investigation_session_id,
                source_type,
                source_path,
                source_url,
                trust_level,
                extraction_method,
                extracted_findings_json,
                approved,
                workspace_root
            )
            VALUES (?, NULL, NULL, 'review_comment', ?, NULL, 2, 'review_comment', ?, 0, ?)
            """,
            (
                evidence_id,
                path,
                json.dumps(
                    {
                        "pr_number": pr_number,
                        "findings": findings,
                        "extraction_status": "pending_approval",
                        "redacted_values": 0,
                        "path": path,
                        "line": line,
                    }
                ),
                normalized_root,
            ),
        )
        await conn.commit()
        return ReviewEvidence(
            evidence_id=evidence_id,
            pr_number=pr_number,
            body=body.strip(),
            path=path,
            line=line,
            approved=False,
        )

    async def approve_review_lesson(
        self,
        *,
        evidence_id: str,
        lesson_title: str,
        lesson_body: str,
        workspace_root: str | None = None,
    ) -> ReviewLesson:
        normalized_root = workspace_root or str(self._config.workspace_path.resolve())
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT id, extracted_findings_json
            FROM evidence_sources
            WHERE id = ? AND source_type = 'review_comment'
              AND COALESCE(workspace_root, ?) = ?
            LIMIT 1
            """,
            (evidence_id, normalized_root, normalized_root),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError(f"Review evidence not found: {evidence_id}")

        memory_item_id = uuid.uuid4().hex
        payload = json.loads(row["extracted_findings_json"] or "{}")
        tags = {
            "approved_review_lesson": True,
            "review_evidence_id": evidence_id,
            "pr_number": payload.get("pr_number"),
        }
        provenance = [
            {
                "source_type": "review_comment",
                "source_ref": evidence_id,
                "source_path": payload.get("path"),
                "line_start": payload.get("line"),
                "line_end": payload.get("line"),
            }
        ]

        await conn.execute(
            """
            INSERT INTO memory_items
            (
                id, type, title, body, source, source_path, source_hash, trust_level,
                tags_json, stale, memory_class, memory_status, visibility_scope,
                reusable, review_required, use_policy_json, provenance_json, workspace_root
            )
            VALUES (?, 'review_lesson', ?, ?, 'review_memory_mode', ?, NULL, 4, ?, 0,
                    'decision', 'confirmed', 'workspace', 1, 0, NULL, ?, ?)
            """,
            (
                memory_item_id,
                lesson_title.strip(),
                lesson_body.strip(),
                payload.get("path"),
                json.dumps(tags),
                json.dumps(provenance),
                normalized_root,
            ),
        )
        await conn.execute(
            "UPDATE evidence_sources SET approved = 1 WHERE id = ?",
            (evidence_id,),
        )
        await conn.commit()
        return ReviewLesson(memory_item_id=memory_item_id, evidence_id=evidence_id)
