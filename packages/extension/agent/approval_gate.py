"""Tiered approval-gate helpers for patch review flows."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class ApprovalTier(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


RISK_ORDER = {
    ApprovalTier.CRITICAL.value: 0,
    ApprovalTier.HIGH.value: 1,
    ApprovalTier.MEDIUM.value: 2,
    ApprovalTier.LOW.value: 3,
}

FILE_RISK_SIGNALS: list[tuple[str, str, str]] = [
    (r"alembic/versions/", ApprovalTier.CRITICAL.value, "database migration"),
    (r"migrations/", ApprovalTier.CRITICAL.value, "database migration"),
    (r"\.env", ApprovalTier.CRITICAL.value, "environment config"),
    (r"auth/", ApprovalTier.HIGH.value, "authentication"),
    (r"security/", ApprovalTier.HIGH.value, "security"),
    (r"billing/", ApprovalTier.HIGH.value, "billing logic"),
    (r"payment/", ApprovalTier.HIGH.value, "payment logic"),
    (r"tenant/", ApprovalTier.HIGH.value, "tenant isolation"),
    (r"test_", ApprovalTier.LOW.value, "test coverage"),
    (r"_test\.py$", ApprovalTier.LOW.value, "test coverage"),
    (r"\.md$", ApprovalTier.LOW.value, "documentation"),
]

_DEFAULT_RISK_LEVEL = ApprovalTier.MEDIUM.value
_DEFAULT_RISK_CATEGORY = "application logic"


@dataclass(frozen=True)
class ApprovalConfig:
    diff_auto_expanded: bool
    scroll_gate_enabled: bool
    confirmation_dialog: str | None
    button_label: str
    button_initially_enabled: bool
    type_to_confirm: bool


@dataclass(frozen=True)
class ComplianceAction:
    label: str
    action_type: str
    prefill_task_request: str
    prefill_mode: str
    prefill_context_hints: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ComplianceWarning:
    rule_id: str
    rule_text: str
    warning_message: str
    actions: list[ComplianceAction] = field(default_factory=list)


def _normalize_path(file_path: str) -> str:
    return file_path.replace("\\", "/").lower()


def _classify_file(file_path: str) -> tuple[str, str, str]:
    normalized_path = _normalize_path(file_path)
    for pattern, risk_level, risk_category in FILE_RISK_SIGNALS:
        if re.search(pattern, normalized_path):
            return file_path, risk_level, risk_category
    return file_path, _DEFAULT_RISK_LEVEL, _DEFAULT_RISK_CATEGORY


def rank_patch_files(changed_files: list[str]) -> list[tuple[str, str, str]]:
    ranked_files = [_classify_file(file_path) for file_path in changed_files]
    return sorted(
        ranked_files,
        key=lambda item: (RISK_ORDER[item[1]], _normalize_path(item[0])),
    )


def determine_approval_tier(ranked_files: list[tuple[str, str, str]]) -> ApprovalTier:
    if not ranked_files:
        return ApprovalTier.LOW
    return ApprovalTier(ranked_files[0][1])


def get_approval_config(
    tier: ApprovalTier,
    highest_risk_file: str | None,
) -> ApprovalConfig:
    file_suffix = f" for {highest_risk_file}" if highest_risk_file else ""
    configs = {
        ApprovalTier.LOW: ApprovalConfig(
            diff_auto_expanded=False,
            scroll_gate_enabled=False,
            confirmation_dialog=None,
            button_label="Approve patch",
            button_initially_enabled=True,
            type_to_confirm=False,
        ),
        ApprovalTier.MEDIUM: ApprovalConfig(
            diff_auto_expanded=True,
            scroll_gate_enabled=False,
            confirmation_dialog=(
                f"Please review the proposed changes{file_suffix} before approval."
            ),
            button_label="Review and approve",
            button_initially_enabled=True,
            type_to_confirm=False,
        ),
        ApprovalTier.HIGH: ApprovalConfig(
            diff_auto_expanded=True,
            scroll_gate_enabled=True,
            confirmation_dialog=(
                f"High-risk changes detected{file_suffix}. Confirm after reviewing the full diff."
            ),
            button_label="Approve high-risk patch",
            button_initially_enabled=False,
            type_to_confirm=False,
        ),
        ApprovalTier.CRITICAL: ApprovalConfig(
            diff_auto_expanded=True,
            scroll_gate_enabled=True,
            confirmation_dialog=(
                f"Critical changes detected{file_suffix}. Manual review is required before approval."
            ),
            button_label="Approve critical patch",
            button_initially_enabled=False,
            type_to_confirm=True,
        ),
    }
    return configs[tier]


def build_compliance_warnings(
    ranked_files: list[tuple[str, str, str]],
) -> list[ComplianceWarning]:
    warnings: list[ComplianceWarning] = []
    seen_rule_ids: set[str] = set()

    for file_path, risk_level, risk_category in ranked_files:
        if risk_level not in {ApprovalTier.HIGH.value, ApprovalTier.CRITICAL.value}:
            continue

        rule_id = f"manual-review-{risk_category.replace(' ', '-')}"
        if rule_id in seen_rule_ids:
            continue
        seen_rule_ids.add(rule_id)

        actions = [
            ComplianceAction(
                label="Open focused review task",
                action_type="create_task",
                prefill_task_request=(
                    f"Review the proposed changes in {file_path} for {risk_category} risks before approval."
                ),
                prefill_mode="review",
                prefill_context_hints=[file_path, risk_category, risk_level],
            ),
            ComplianceAction(
                label="Generate validation checklist",
                action_type="create_task",
                prefill_task_request=(
                    f"List the validation steps required before approving changes to {file_path}."
                ),
                prefill_mode="plan",
                prefill_context_hints=[file_path, "validation", risk_category],
            ),
        ]
        warnings.append(
            ComplianceWarning(
                rule_id=rule_id,
                rule_text=(
                    f"Changes affecting {risk_category} require explicit human review before approval."
                ),
                warning_message=(
                    f"{file_path} was classified as {risk_level} risk because it touches {risk_category}."
                ),
                actions=actions,
            )
        )

    return warnings
