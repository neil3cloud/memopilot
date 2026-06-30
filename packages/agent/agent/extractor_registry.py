"""Registry for managing language-specific extractors."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base_extractor import LanguageExtractor

logger = logging.getLogger(__name__)


class ExtractorRegistry:
    """Maps file extensions to language extractors.

    Supports multi-language indexing by dispatching to the correct extractor
    based on file extension.
    """

    def __init__(self) -> None:
        self._by_extension: dict[str, LanguageExtractor] = {}
        self._by_language: dict[str, LanguageExtractor] = {}

    def register(self, extractor: LanguageExtractor) -> None:
        """Register an extractor for its language and extensions.

        Args:
            extractor: Must have `extensions` and `language` attributes.

        Raises:
            ValueError: If an extension is already registered to a different language.
        """
        for ext in extractor.extensions:
            if ext in self._by_extension:
                existing_lang = self._by_extension[ext].language
                if existing_lang != extractor.language:
                    raise ValueError(
                        f"Extension {ext} already registered to {existing_lang}, "
                        f"cannot register to {extractor.language}"
                    )
            self._by_extension[ext] = extractor

        self._by_language[extractor.language] = extractor
        logger.debug(f"Registered {extractor.language} extractor: {extractor.extensions}")

    def get(self, file_extension: str) -> LanguageExtractor | None:
        """Lookup extractor by file extension (with or without dot).

        Args:
            file_extension: e.g. '.py', 'py', '.ts', 'ts'

        Returns:
            Extractor if registered, None otherwise.
        """
        ext = file_extension if file_extension.startswith(".") else f".{file_extension}"
        return self._by_extension.get(ext)

    def get_by_language(self, language: str) -> LanguageExtractor | None:
        """Lookup extractor by language name.

        Args:
            language: e.g. 'python', 'typescript', 'csharp'

        Returns:
            Extractor if registered, None otherwise.
        """
        return self._by_language.get(language)

    def all_extensions(self) -> list[str]:
        """Return sorted list of all registered file extensions."""
        return sorted(self._by_extension.keys())

    def all_languages(self) -> list[str]:
        """Return sorted list of all registered languages."""
        return sorted(self._by_language.keys())
