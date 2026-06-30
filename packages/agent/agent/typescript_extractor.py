"""TypeScript/JavaScript symbol extractor using tree-sitter."""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

from tree_sitter import Language, Parser
from tree_sitter_typescript import language_typescript, language_tsx

from .base_extractor import LanguageExtractor
from .graph_retriever import SymbolRelationshipRecord, make_relationship_id
from .symbol_extractor import SymbolRecord

if TYPE_CHECKING:
    from .typescript_resolver import TypeScriptResolver

logger = logging.getLogger(__name__)


def _get_text(node, source: str) -> str:
    """Get text content from a tree-sitter node."""
    if hasattr(node, "text"):
        return node.text.decode("utf-8") if isinstance(node.text, bytes) else str(node.text)
    start = node.start_byte
    end = node.end_byte
    return source[start:end]


class TypeScriptExtractor:
    """Extracts symbols from TypeScript/JavaScript files using tree-sitter."""

    extensions = (".ts", ".tsx", ".js", ".jsx")
    language = "typescript"

    def __init__(self) -> None:
        try:
            # Initialize tree-sitter parser for TypeScript
            # Language must be wrapped in Language() for tree-sitter 0.25+
            self._parser_ts = Parser()
            self._parser_ts.language = Language(language_typescript())

            self._parser_tsx = Parser()
            self._parser_tsx.language = Language(language_tsx())
            self._tree_cache: dict[tuple[str, str], object] = {}

        except Exception as e:
            logger.error(f"Failed to initialize tree-sitter: {e}")
            self._parser_ts = None
            self._parser_tsx = None
            self._tree_cache = {}

    def extract(
        self,
        file_path: str,
        source: str,
        content_hash: str,
    ) -> list[SymbolRecord]:
        """Extract TypeScript/JavaScript symbols using tree-sitter."""
        if self._parser_ts is None:
            logger.warning("tree-sitter not available, skipping TypeScript extraction")
            return []

        tree = self._parse_tree(file_path=file_path, source=source, content_hash=content_hash)
        if tree is None:
            return []

        symbols: list[SymbolRecord] = []
        self._walk_tree(tree.root_node, file_path, source, content_hash, symbols)
        return symbols

    def extract_relationships(
        self,
        file_path: str,
        source: str,
        symbols: list[SymbolRecord],
        workspace_root: str,
    ) -> list[SymbolRelationshipRecord]:
        """Extract TypeScript/JavaScript relationships (calls, imports, inheritance).

        Phase 2b: Resolves import paths to target files using tsconfig.json path aliases
        and relative path resolution.
        """
        if self._parser_ts is None:
            return []

        tree = self._parse_tree(file_path=file_path, source=source)
        if tree is None:
            return []

        relationships: list[SymbolRelationshipRecord] = []
        symbol_by_name = {s.name: s for s in symbols}

        # Lazy-load resolver
        from .typescript_resolver import TypeScriptResolver
        resolver = TypeScriptResolver(workspace_root)

        # Extract import statements and resolve them
        self._extract_imports(
            tree.root_node,
            file_path,
            source,
            symbols,
            symbol_by_name,
            workspace_root,
            relationships,
            resolver,
        )

        # Deduplicate by ID
        seen = set()
        deduped = []
        for rel in relationships:
            if rel.id not in seen:
                seen.add(rel.id)
                deduped.append(rel)

        return deduped

    def _walk_tree(
        self,
        node,
        file_path: str,
        source: str,
        content_hash: str,
        symbols: list[SymbolRecord],
        depth: int = 0,
    ) -> None:
        """Recursively walk the AST and extract symbols."""
        if node.type in ("function_declaration", "async_function_declaration"):
            self._extract_function_symbol(node, file_path, source, content_hash, symbols)

        elif node.type == "variable_declarator":
            # Arrow functions assigned to variables: const foo = () => {}
            if self._is_arrow_function(node):
                self._extract_arrow_function_symbol(
                    node, file_path, source, content_hash, symbols
                )

        elif node.type == "class_declaration":
            self._extract_class_symbol(node, file_path, source, content_hash, symbols)

        elif node.type == "interface_declaration":
            # Include interfaces for context (not separately summarized)
            pass

        elif node.type == "import_statement":
            # Imports are handled in extract_relationships
            pass

        # Recursively process children
        for child in node.children:
            self._walk_tree(child, file_path, source, content_hash, symbols, depth + 1)

    def _extract_function_symbol(
        self,
        node,
        file_path: str,
        source: str,
        content_hash: str,
        symbols: list[SymbolRecord],
    ) -> None:
        """Extract function declaration symbol."""
        for child in node.children:
            if child.type == "identifier":
                name = _get_text(child, source)
                signature = self._extract_signature(node, source)
                sym_id = self._make_symbol_id(file_path, name, "function")

                symbols.append(
                    SymbolRecord(
                        id=sym_id,
                        file_path=file_path,
                        name=name,
                        kind="function",
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        signature=signature,
                        content_hash=content_hash,
                    )
                )
                break

    def _extract_arrow_function_symbol(
        self,
        node,
        file_path: str,
        source: str,
        content_hash: str,
        symbols: list[SymbolRecord],
    ) -> None:
        """Extract arrow function assigned to a variable."""
        # node is variable_declarator; name is first child
        if node.children:
            name_node = node.children[0]
            name = _get_text(name_node, source)
            signature = None
            sym_id = self._make_symbol_id(file_path, name, "function")

            symbols.append(
                SymbolRecord(
                    id=sym_id,
                    file_path=file_path,
                    name=name,
                    kind="function",
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    signature=signature,
                    content_hash=content_hash,
                )
            )

    def _extract_class_symbol(
        self,
        node,
        file_path: str,
        source: str,
        content_hash: str,
        symbols: list[SymbolRecord],
    ) -> None:
        """Extract class declaration and its methods."""
        class_name = None
        for child in node.children:
            if child.type in ("type_identifier", "identifier"):
                class_name = _get_text(child, source)
                break

        if not class_name:
            return

        sym_id = self._make_symbol_id(file_path, class_name, "class")
        symbols.append(
            SymbolRecord(
                id=sym_id,
                file_path=file_path,
                name=class_name,
                kind="class",
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                signature=None,
                content_hash=content_hash,
            )
        )

        # Extract methods from class body
        for child in node.children:
            if child.type == "class_body":
                for member in child.children:
                    if member.type in (
                        "method_definition",
                        "property_identifier",
                    ):
                        self._extract_method_symbol(
                            member,
                            file_path,
                            source,
                            content_hash,
                            symbols,
                            class_name,
                        )

    def _extract_method_symbol(
        self,
        node,
        file_path: str,
        source: str,
        content_hash: str,
        symbols: list[SymbolRecord],
        class_name: str,
    ) -> None:
        """Extract method from a class."""
        method_name = None
        for child in node.children:
            if child.type in ("property_identifier", "identifier"):
                method_name = _get_text(child, source)
                break

        if not method_name:
            return

        full_name = f"{class_name}.{method_name}"
        sym_id = self._make_symbol_id(file_path, full_name, "method")

        symbols.append(
            SymbolRecord(
                id=sym_id,
                file_path=file_path,
                name=full_name,
                kind="method",
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                signature=None,
                content_hash=content_hash,
            )
        )

    def _extract_imports(
        self,
        node,
        file_path: str,
        source: str,
        symbols: list[SymbolRecord],
        symbol_by_name: dict[str, SymbolRecord],
        workspace_root: str,
        relationships: list[SymbolRelationshipRecord],
        resolver=None,
    ) -> None:
        """Extract import statements and build relationships.

        Phase 2b: Uses resolver to find target files and potentially symbols.
        """
        if node.type == "import_statement":
            # Extract import source and imported names
            import_source = None
            imported_names = []

            for child in node.children:
                if child.type == "string":
                    # Extract the import path from string node
                    text = _get_text(child, source)
                    import_source = text.strip('\'"')

                elif child.type == "import_clause":
                    # Extract imported names
                    for subchild in child.children:
                        if subchild.type == "identifier":
                            imported_names.append(_get_text(subchild, source))
                        elif subchild.type in ("named_imports", "namespace_import"):
                            # Extract individual imports or * as name
                            self._extract_imported_names(subchild, source, imported_names)

            if import_source:
                # Resolve import path using resolver
                if resolver:
                    abs_file_path = f"{workspace_root}/{file_path}"
                    target_file = resolver.resolve_import(import_source, abs_file_path)
                else:
                    target_file = None

                # Create relationships for each imported name
                # Note: Imports are module-level, so we skip them or use file_path as source
                # For now, we omit import relationships to avoid violating FK constraints
                # ("import_statement" is not a valid symbol ID)
                # TODO: Create file-level symbols to capture module imports
                for imported_name in imported_names:
                    # Relationship tracking deferred until file-level symbols are introduced
                    pass

        # Recursively process children
        for child in node.children:
            self._extract_imports(
                child,
                file_path,
                source,
                symbols,
                symbol_by_name,
                workspace_root,
                relationships,
                resolver,
            )

    def _extract_imported_names(self, node, source: str, names: list[str]) -> None:
        """Extract imported identifiers from import clauses."""
        for child in node.children:
            if child.type == "identifier":
                names.append(_get_text(child, source))
            elif child.type in ("import_specifier", "shorthand_property_identifier_pattern"):
                # Extract name from import { X }, import { X as Y }, etc.
                for subchild in child.children:
                    if subchild.type == "identifier":
                        names.append(_get_text(subchild, source))
                        break

    def _is_arrow_function(self, node) -> bool:
        """Check if variable_declarator contains an arrow function."""
        for child in node.children:
            if child.type == "arrow_function":
                return True
        return False

    def _extract_signature(self, node, source: str) -> str | None:
        """Extract function signature (params)."""
        for child in node.children:
            if child.type == "formal_parameters":
                return _get_text(child, source)
        return None

    def _make_symbol_id(self, file_path: str, symbol_name: str, kind: str) -> str:
        """Generate unique symbol ID."""
        content = f"{file_path}:{symbol_name}:{kind}".encode()
        return hashlib.sha256(content).hexdigest()[:16]

    def _parse_tree(self, file_path: str, source: str, content_hash: str | None = None):
        """Parse source once and reuse the tree between symbol/relationship extraction."""
        source_hash = content_hash or hashlib.sha256(source.encode("utf-8")).hexdigest()
        cache_key = (file_path, source_hash)
        cached = self._tree_cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            # Use TSX parser for TSX/JSX files, TS parser for TS/JS files.
            parser = self._parser_tsx if file_path.endswith((".tsx", ".jsx")) else self._parser_ts
            tree = parser.parse(source.encode("utf-8"))
        except Exception as e:
            logger.debug(f"Failed to parse {file_path}: {e}")
            return None

        self._tree_cache[cache_key] = tree
        return tree
