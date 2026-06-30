"""Framework detection and tagging for TypeScript/JavaScript symbols."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .symbol_extractor import SymbolRecord

logger = logging.getLogger(__name__)


class FrameworkDetector:
    """Detects React, Angular, and other framework patterns in TypeScript code."""

    @staticmethod
    def detect_react_component(name: str, source: str) -> bool:
        """Check if symbol is likely a React component.

        Heuristics:
        - Starts with capital letter (PascalCase)
        - Function/arrow function in .tsx file
        - Returns JSX (hard to detect without AST traversal, skip for now)
        """
        return name[0].isupper() if name else False

    @staticmethod
    def detect_react_hook(name: str) -> bool:
        """Check if function is a React hook.

        React hooks start with 'use' followed by capital letter.
        """
        return name.startswith("use") and len(name) > 3 and name[3].isupper()

    @staticmethod
    def detect_angular_component(source: str, symbol_name: str) -> bool:
        """Check if class is an Angular component.

        Look for @Component decorator before class definition.
        """
        return FrameworkDetector._has_decorator_for_class(source, symbol_name, "Component")

    @staticmethod
    def detect_angular_service(source: str, symbol_name: str) -> bool:
        """Check if class is an Angular service.

        Look for @Injectable decorator.
        """
        return FrameworkDetector._has_decorator_for_class(source, symbol_name, "Injectable")

    @staticmethod
    def detect_angular_module(source: str, symbol_name: str) -> bool:
        """Check if class is an Angular module.

        Look for @NgModule decorator.
        """
        return FrameworkDetector._has_decorator_for_class(source, symbol_name, "NgModule")

    @staticmethod
    def _has_decorator_for_class(source: str, symbol_name: str, decorator: str) -> bool:
        """Return True only when the decorator applies to the target class."""
        if not symbol_name:
            return False

        decorator_pattern = rf"@{re.escape(decorator)}\b"
        class_pattern = r"\bclass\s+([a-zA-Z_][a-zA-Z0-9_]*)\b"

        for decorator_match in re.finditer(decorator_pattern, source):
            class_match = re.search(class_pattern, source[decorator_match.end() :])
            if class_match and class_match.group(1) == symbol_name:
                return True

        return False

    @staticmethod
    def detect_api_client(source: str) -> bool:
        """Check if file contains HTTP client calls.

        Look for common HTTP libraries: fetch, axios, HttpClient, got
        """
        patterns = [
            "fetch(",
            "axios",
            "HttpClient",
            "got(",
        ]
        return any(pattern in source for pattern in patterns)

    @staticmethod
    def detect_route_handler(source: str, symbol_name: str) -> bool:
        """Check if function is an Express.js route handler.

        Look for app.get(), app.post(), etc. decorators or calls.
        """
        route_patterns = [
            "@post(", "@get(", "@put(", "@delete(", "@patch(",  # Decorator style
            "app.get(", "app.post(", "app.put(", "app.delete(",  # Express style
        ]
        return symbol_name and any(pattern in source for pattern in route_patterns)

    @staticmethod
    def get_tags(symbol_name: str, kind: str, source: str, file_path: str) -> list[str]:
        """Determine framework tags for a symbol.

        Args:
            symbol_name: Name of the symbol
            kind: Symbol kind (function, class, method)
            source: Full source code (for pattern matching)
            file_path: File path (for extension detection)

        Returns:
            List of tags like ["react_component", "api_client"]
        """
        tags: list[str] = []

        # React patterns
        if (".tsx" in file_path or ".jsx" in file_path) and kind == "function":
            if FrameworkDetector.detect_react_component(symbol_name, source):
                tags.append("react_component")

            if FrameworkDetector.detect_react_hook(symbol_name):
                tags.append("react_hook")

        # Angular patterns
        if kind == "class":
            if FrameworkDetector.detect_angular_component(source, symbol_name):
                tags.append("angular_component")

            if FrameworkDetector.detect_angular_service(source, symbol_name):
                tags.append("angular_service")

            if FrameworkDetector.detect_angular_module(source, symbol_name):
                tags.append("angular_module")

        # API patterns (file-level)
        if FrameworkDetector.detect_api_client(source):
            tags.append("api_client")

        # Route handler (function-level)
        if FrameworkDetector.detect_route_handler(source, symbol_name):
            tags.append("route_handler")

        return tags
