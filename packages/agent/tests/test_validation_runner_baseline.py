from __future__ import annotations

from agent.validation_runner import (
    FAILURE_HINTS,
    FailureCategory,
    build_escalation_message,
    build_retry_context,
    categorise_failure,
    categorise_failures,
    classify_validation_diff,
    should_escalate_after_retries,
)


def test_pre_patch_baseline_identifies_pre_existing() -> None:
    baseline = [
        ("tests/test_auth.py::test_login", "AssertionError: login failed"),
        ("tests/test_auth.py::test_logout", "ModuleNotFoundError: auth.helpers"),
    ]
    result = classify_validation_diff(baseline, list(baseline))

    assert len(result.pre_existing_failures) == 2
    assert result.new_failures == []
    assert result.fixed_by_patch == []


def test_new_failures_isolated_from_pre_existing() -> None:
    baseline = [
        ("tests/test_auth.py::test_login", "AssertionError: login failed"),
        ("tests/test_auth.py::test_logout", "ModuleNotFoundError: auth.helpers"),
    ]
    post_patch = baseline + [
        ("tests/test_users.py::test_profile", "AssertionError: profile mismatch"),
    ]

    result = classify_validation_diff(baseline, post_patch)

    assert len(result.pre_existing_failures) == 2
    assert len(result.new_failures) == 1
    assert result.new_failures[0][0] == "tests/test_users.py::test_profile"


def test_fixed_by_patch_identified() -> None:
    baseline = [("tests/test_api.py::test_ping", "AssertionError: expected pong")]

    result = classify_validation_diff(baseline, [])

    assert result.fixed_by_patch == baseline
    assert result.new_failures == []


def test_auto_retry_includes_failure_in_context() -> None:
    failures = categorise_failures(
        [("tests/test_api.py::test_imports", "ModuleNotFoundError: No module named 'agent.utils'")]
    )

    context = build_retry_context(failures)

    assert "tests/test_api.py::test_imports" in context
    assert "ModuleNotFoundError" in context
    assert "Hint:" in context


def test_escalation_requires_approval_when_configured() -> None:
    message = build_escalation_message(
        retry_count=2,
        max_retries=2,
        requires_approval=True,
    )

    assert should_escalate_after_retries(2, max_retries=2) is True
    assert "requires approval" in message


def test_import_error_categorised() -> None:
    category = categorise_failure(
        "tests/test_api.py::test_imports",
        "ModuleNotFoundError: No module named 'agent.validation_runner'",
    )

    assert category is FailureCategory.IMPORT_ERROR


def test_assertion_error_hint_is_template_driven() -> None:
    failure = categorise_failures(
        [("tests/test_math.py::test_addition", "AssertionError: expected 2 but got 3")]
    )[0]

    assert failure.category is FailureCategory.ASSERTION_ERROR
    assert failure.hint == FAILURE_HINTS[FailureCategory.ASSERTION_ERROR].format(
        test_id=failure.test_id,
        short_output=failure.short_output,
    )


def test_classify_validation_diff_overall_status() -> None:
    passed = classify_validation_diff(
        [("tests/test_math.py::test_addition", "AssertionError")],
        [("tests/test_math.py::test_addition", "AssertionError")],
    )
    failed = classify_validation_diff(
        [("tests/test_math.py::test_addition", "AssertionError")],
        [
            ("tests/test_math.py::test_addition", "AssertionError"),
            ("tests/test_math.py::test_subtract", "AssertionError"),
        ],
    )

    assert passed.overall_status == "passed"
    assert failed.overall_status == "failed"
