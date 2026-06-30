"""Language-agnostic extractor interface for multi-language support."""

from __future__ import annotations

from typing import Protocol

from .graph_retriever import SymbolRelationshipRecord
from .symbol_extractor import SymbolRecord


class LanguageExtractor(Protocol):
    """Protocol for language-specific symbol extractors.

    All language extractors must implement this interface to be plugged
    into the ExtractorRegistry and WorkspaceIndexer.
    """

    extensions: tuple[str, ...]
    """File extensions handled by this extractor (e.g. ('.py',), ('.ts', '.tsx', '.js', '.jsx'))."""

    language: str
    """Language identifier (e.g. 'python', 'typescript', 'csharp')."""

    def extract(
        self,
        file_path: str,
        source: str,
        content_hash: str,
    ) -> list[SymbolRecord]:
        """Extract symbols from source code.

        Args:
            file_path: Relative path within workspace (e.g. 'src/main.py').
            source: Full source code content.
            content_hash: SHA256 hash of source for change detection.

        Returns:
            List of extracted symbols with IDs, kinds, line ranges, signatures.
            Empty list if parse fails (no exceptions).
        """
        ...

    def extract_relationships(
        self,
        file_path: str,
        source: str,
        symbols: list[SymbolRecord],
        workspace_root: str,
    ) -> list[SymbolRelationshipRecord]:
        """Extract relationships (calls, imports, inheritance) from source.

        Must be called after extract() so symbol IDs are known.
        Relationships with unknown targets are stored with to_symbol_id=None.

        Args:
            file_path: Relative path within workspace.
            source: Full source code content.
            symbols: Symbols already extracted from this file.
            workspace_root: Absolute path to workspace root.

        Returns:
            List of relationship records, deduplicated by ID.
            Empty list if parse fails.
        """
        ...
