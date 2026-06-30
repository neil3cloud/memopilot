"""Test Phase 1: Foundation — ExtractorRegistry and multi-language support."""

import pytest
from pathlib import Path
from agent.base_extractor import LanguageExtractor
from agent.extractor_registry import ExtractorRegistry
from agent.python_extractor import PythonExtractor
from agent.project_scanner import WorkspaceScanner


@pytest.fixture
def registry():
    """Create a fresh registry for each test."""
    reg = ExtractorRegistry()
    reg.register(PythonExtractor())
    return reg


def test_python_extractor_registered(registry):
    """Python extractor should be auto-registered."""
    assert registry.get(".py") is not None
    assert registry.get_by_language("python") is not None
    assert ".py" in registry.all_extensions()
    assert "python" in registry.all_languages()


def test_registry_get_with_dot(registry):
    """Registry lookup should handle .ext format."""
    extractor = registry.get(".py")
    assert extractor is not None
    assert extractor.language == "python"


def test_registry_get_without_dot(registry):
    """Registry lookup should handle ext format (auto-add dot)."""
    extractor = registry.get("py")
    assert extractor is not None
    assert extractor.language == "python"


def test_registry_returns_none_for_unknown(registry):
    """Lookup of unknown extension returns None."""
    assert registry.get(".ts") is None
    assert registry.get_by_language("typescript") is None


def test_registry_prevent_duplicate_extension(registry):
    """Cannot register same extension to different language."""
    class FakeExtractor:
        extensions = (".py",)
        language = "fake"

    with pytest.raises(ValueError, match="already registered"):
        registry.register(FakeExtractor())


def test_workspace_scanner_multi_extension(tmp_path):
    """Scanner should accept multiple file extensions."""
    # Create test files
    (tmp_path / "main.py").write_text("print('hello')")
    (tmp_path / "app.ts").write_text("console.log('hello')")
    (tmp_path / "controller.cs").write_text("public class Test {}")

    scanner = WorkspaceScanner(tmp_path, file_extensions=[".py", ".ts", ".cs"])
    result = scanner.scan()

    # All files should be returned
    file_names = {p.name for p in result.python_files}
    assert file_names == {"main.py", "app.ts", "controller.cs"}


def test_workspace_scanner_default_python_only(tmp_path):
    """Scanner defaults to Python only for backward compatibility."""
    (tmp_path / "main.py").write_text("print('hello')")
    (tmp_path / "app.ts").write_text("console.log('hello')")

    scanner = WorkspaceScanner(tmp_path)
    result = scanner.scan()

    # Only Python file
    file_names = {p.name for p in result.python_files}
    assert file_names == {"main.py"}


def test_scan_result_includes_languages_detected(tmp_path):
    """ScanResult should include detected languages."""
    (tmp_path / "main.py").write_text("print('hello')")
    (tmp_path / "app.ts").write_text("console.log('hello')")
    (tmp_path / "controller.cs").write_text("public class Test {}")

    scanner = WorkspaceScanner(tmp_path, file_extensions=[".py", ".ts", ".cs"])
    result = scanner.scan()

    assert result.languages_detected == {"python", "typescript", "csharp"}


def test_python_extractor_protocol(registry):
    """Python extractor should have required protocol attributes."""
    extractor = registry.get(".py")
    assert hasattr(extractor, "extensions")
    assert hasattr(extractor, "language")
    assert hasattr(extractor, "extract")
    assert hasattr(extractor, "extract_relationships")
    assert callable(extractor.extract)
    assert callable(extractor.extract_relationships)
