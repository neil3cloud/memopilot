"""Tests for autofix classifier."""

from __future__ import annotations

import pytest

from agent.autofix_classifier import (
    AutofixSafety,
    classify_diagnostic,
    classify_diagnostics,
)


class TestClassifyDiagnostic:
    """Test individual diagnostic classification."""

    def test_unused_import_is_safe(self):
        result = classify_diagnostic(
            code="F401", message="'os' imported but unused", file_path="main.py", line=1
        )
        assert result.safety == AutofixSafety.SAFE
        assert result.category == "unused_import"

    def test_line_too_long_is_safe(self):
        result = classify_diagnostic(
            code="E501", message="line too long (127 > 79 characters)", file_path="main.py", line=42
        )
        assert result.safety == AutofixSafety.SAFE
        assert result.category == "line_too_long"

    def test_trailing_whitespace_is_safe(self):
        result = classify_diagnostic(
            code="W291", message="trailing whitespace", file_path="main.py", line=10
        )
        assert result.safety == AutofixSafety.SAFE
        assert result.category == "trailing_whitespace"

    def test_isort_violation_is_safe(self):
        result = classify_diagnostic(
            code="I001", message="import sort order violation", file_path="main.py", line=3
        )
        assert result.safety == AutofixSafety.SAFE
        assert result.category == "isort_violation"

    def test_security_vulnerability_is_manual(self):
        result = classify_diagnostic(
            code="B301", message="Possible security vulnerability with pickle",
            file_path="api.py", line=15
        )
        assert result.safety == AutofixSafety.REQUIRES_MANUAL
        assert result.category == "security_vulnerability"

    def test_test_failure_is_manual(self):
        result = classify_diagnostic(
            code="test_login", message="AssertionError: test failed",
            file_path="test_auth.py", line=50
        )
        assert result.safety == AutofixSafety.REQUIRES_MANUAL
        assert result.category == "test_failure"

    def test_type_incompatible_is_manual(self):
        result = classify_diagnostic(
            code="mypy", message="Argument 1 to sell_item() has incompatible type",
            file_path="inventory.py", line=20
        )
        assert result.safety == AutofixSafety.REQUIRES_MANUAL
        assert result.category == "type_incompatible"

    def test_unknown_diagnostic_defaults_to_manual(self):
        result = classify_diagnostic(
            code="CUSTOM001", message="Something unusual happened",
            file_path="foo.py", line=1
        )
        assert result.safety == AutofixSafety.REQUIRES_MANUAL
        assert result.category == "unknown"

    def test_unsafe_takes_priority_over_safe(self):
        """If a message matches both safe and unsafe, unsafe wins."""
        result = classify_diagnostic(
            code="E501",
            message="line too long but also contains security vulnerability",
            file_path="main.py", line=1
        )
        assert result.safety == AutofixSafety.REQUIRES_MANUAL


class TestClassifyDiagnostics:
    """Test batch classification."""

    def test_splits_into_safe_and_manual(self):
        diagnostics = [
            {"file_path": "a.py", "line": 1, "code": "F401", "message": "unused import os"},
            {"file_path": "a.py", "line": 5, "code": "E501", "message": "line too long"},
            {"file_path": "b.py", "line": 10, "code": "B301", "message": "security vulnerability"},
        ]
        safe, manual = classify_diagnostics(diagnostics)
        assert len(safe) == 2
        assert len(manual) == 1
        assert all(c.safety == AutofixSafety.SAFE for c in safe)
        assert manual[0].safety == AutofixSafety.REQUIRES_MANUAL

    def test_empty_input(self):
        safe, manual = classify_diagnostics([])
        assert safe == []
        assert manual == []
