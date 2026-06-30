"""Python language extractor — wraps existing SymbolExtractor."""

from __future__ import annotations

from .base_extractor import LanguageExtractor
from .graph_retriever import SymbolRelationshipRecord
from .symbol_extractor import SymbolExtractor, SymbolRecord


class PythonExtractor:
    """Python symbol extractor implementing LanguageExtractor protocol."""

    extensions = (".py",)
    language = "python"

    def __init__(self) -> None:
        self._extractor = SymbolExtractor()

    def extract(
        self,
        file_path: str,
        source: str,
        content_hash: str,
    ) -> list[SymbolRecord]:
        """Extract Python symbols using AST."""
        return self._extractor.extract(
            file_path=file_path,
            source=source,
            content_hash=content_hash,
        )

    def extract_relationships(
        self,
        file_path: str,
        source: str,
        symbols: list[SymbolRecord],
        workspace_root: str,
    ) -> list[SymbolRelationshipRecord]:
        """Extract Python relationships (calls, imports, inheritance)."""
        return self._extractor.extract_relationships(
            file_path=file_path,
            source=source,
            symbols=symbols,
            workspace_root=workspace_root,
        )
