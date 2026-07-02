"""Tests for symbol source slicing and skeleton-line formatting."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.symbol_source import build_skeleton_line, read_symbol_source


@pytest.mark.asyncio
async def test_read_symbol_source_slices_inclusive_1_indexed(tmp_workspace: Path):
    (tmp_workspace / "sample.py").write_text(
        "line1\nline2\nline3\nline4\nline5\n", encoding="utf-8"
    )

    result = await read_symbol_source(
        workspace_root=tmp_workspace,
        file_path="sample.py",
        start_line=2,
        end_line=4,
    )

    assert result == "line2\nline3\nline4"


@pytest.mark.asyncio
async def test_read_symbol_source_single_line(tmp_workspace: Path):
    (tmp_workspace / "sample.py").write_text("only_line\n", encoding="utf-8")

    result = await read_symbol_source(
        workspace_root=tmp_workspace,
        file_path="sample.py",
        start_line=1,
        end_line=1,
    )

    assert result == "only_line"


def test_build_skeleton_line_with_summary():
    line = build_skeleton_line(
        name="OrderService.validate_payment",
        kind="method",
        signature="validate_payment(order)",
        summary="Validates a payment before checkout.",
    )
    assert line == (
        "- method OrderService.validate_payment — validate_payment(order): "
        "Validates a payment before checkout."
    )


def test_build_skeleton_line_pending_summary():
    line = build_skeleton_line(
        name="validate_payment",
        kind="function",
        signature="validate_payment(order)",
        summary=None,
    )
    assert line == "- function validate_payment — validate_payment(order) (summary pending)"


def test_build_skeleton_line_falls_back_without_signature():
    line = build_skeleton_line(name="Foo", kind="class", signature=None, summary=None)
    assert line == "- class Foo (summary pending)"
