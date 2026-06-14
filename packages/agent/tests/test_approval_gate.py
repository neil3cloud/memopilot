"""Unit tests for approval gate tiering helpers."""

from __future__ import annotations

from agent.approval_gate import (
    ApprovalTier,
    build_compliance_warnings,
    determine_approval_tier,
    get_approval_config,
    rank_patch_files,
)


def test_low_risk_approval_button_enabled():
    config = get_approval_config(ApprovalTier.LOW, "README.md")

    assert config.button_initially_enabled is True



def test_high_risk_approval_button_disabled():
    config = get_approval_config(ApprovalTier.HIGH, "agent/auth/service.py")

    assert config.button_initially_enabled is False



def test_critical_type_confirm_required():
    config = get_approval_config(ApprovalTier.CRITICAL, "agent/migrations/014_approval_gate_tiers.sql")

    assert config.type_to_confirm is True



def test_migration_file_classified_critical():
    ranked_files = rank_patch_files(["alembic/versions/202401010101_add_table.py"])

    assert ranked_files[0][1] == "critical"



def test_files_sorted_by_risk_descending():
    ranked_files = rank_patch_files([
        "docs/guide.md",
        "agent/auth/service.py",
        "agent/migrations/014_approval_gate_tiers.sql",
    ])

    assert ranked_files[0][0] == "agent/migrations/014_approval_gate_tiers.sql"
    assert determine_approval_tier(ranked_files) is ApprovalTier.CRITICAL



def test_compliance_warning_includes_action():
    ranked_files = rank_patch_files(["agent/security/policies.py"])

    warnings = build_compliance_warnings(ranked_files)

    assert warnings
    assert warnings[0].actions
    assert warnings[0].actions[0].prefill_task_request
    assert warnings[0].actions[0].prefill_mode in {"review", "plan"}



def test_scroll_gate_config_for_high_tier():
    config = get_approval_config(ApprovalTier.HIGH, "agent/billing/ledger.py")

    assert config.scroll_gate_enabled is True



def test_docs_classified_low():
    ranked_files = rank_patch_files(["docs/approval-gate.md"])

    assert ranked_files[0][1] == "low"
