"""Patch risk assessment and rule compliance scoring (v1 capability)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from .config import Config
from .db import DatabaseManager


@dataclass(frozen=True)
class PatchAssessmentResult:
    patch_attempt_id: str
    risk_level: str
    rule_compliance_score: float
    reasons: list[str]


class PatchAssessorService:
    """Deterministic patch risk classification and rule compliance scoring."""

    def __init__(self, *, config: Config, db: DatabaseManager) -> None:
        self._config = config
        self._db = db

    async def assess_patch(
        self,
        *,
        task_run_id: str,
        diff_text: str,
        files_changed: list[str],
        active_rules: list[str],
    ) -> PatchAssessmentResult:
        reasons: list[str] = []
        risk_level = "low"
        lowered_diff = diff_text.lower()
        lowered_files = [item.lower() for item in files_changed]

        if "drop table" in lowered_diff or "delete from" in lowered_diff:
            risk_level = "high"
            reasons.append("destructive_sql_detected")
        if any(item.endswith(".env") for item in lowered_files):
            risk_level = "high"
            reasons.append("sensitive_file_touched")
        if risk_level != "high" and (len(files_changed) > 10 or "migration" in lowered_diff):
            risk_level = "medium"
            reasons.append("wide_or_schema_change")

        score = 1.0
        if risk_level == "high":
            score -= 0.45
        elif risk_level == "medium":
            score -= 0.2

        for rule in active_rules:
            low_rule = rule.lower()
            if (
                "must include tests" in low_rule
                and not any("test" in item for item in lowered_files)
            ):
                score -= 0.25
                reasons.append("missing_test_file_for_rule")
            if "no hardcoded secrets" in low_rule and "api_key" in lowered_diff:
                score -= 0.35
                reasons.append("possible_secret_violation")

        score = max(0.0, min(1.0, score))
        patch_attempt_id = uuid.uuid4().hex
        conn = await self._db.connect()
        await conn.execute(
            """
            INSERT INTO patch_attempts
            (
                id, task_run_id, patch_path, files_changed_json,
                risk_level, rule_compliance_score, approved, applied, validation_status
            )
            VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?)
            """,
            (
                patch_attempt_id,
                task_run_id,
                "inline-diff",
                json.dumps(files_changed),
                risk_level,
                score,
                "pending",
            ),
        )
        await conn.commit()
        return PatchAssessmentResult(
            patch_attempt_id=patch_attempt_id,
            risk_level=risk_level,
            rule_compliance_score=score,
            reasons=sorted(set(reasons)),
        )
