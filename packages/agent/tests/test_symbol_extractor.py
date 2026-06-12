"""Tests for AST symbol extraction."""

from __future__ import annotations

from agent.symbol_extractor import SymbolExtractor


def test_symbol_extractor_extracts_expected_symbols():
    source = """
import os
from pathlib import Path

class Example:
    def run(self, value: int) -> int:
        return value

def top_level(name: str) -> str:
    return name
"""
    extractor = SymbolExtractor()
    symbols = extractor.extract(
        file_path="src/example.py",
        source=source,
        content_hash="hash",
    )

    summary = {(symbol.kind, symbol.name) for symbol in symbols}
    assert ("import", "os") in summary
    assert ("import", "pathlib:Path") in summary
    assert ("class", "Example") in summary
    assert ("method", "Example.run") in summary
    assert ("function", "top_level") in summary
