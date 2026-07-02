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

        # Extract import statements and resolve them. Also collect a plain
        # imported-name -> resolved-target-file map — used below to give
        # cross-file calls a known target file at extraction time, since the
        # extractor doesn't otherwise store import relationships yet.
        import_map: dict[str, str] = {}
        self._extract_imports(
            tree.root_node,
            file_path,
            source,
            symbols,
            symbol_by_name,
            workspace_root,
            relationships,
            resolver,
            import_map,
        )

        # Extract call expressions. Same-file calls resolve immediately via
        # symbol_by_name; calls to an imported name get their target file
        # attached now (from import_map) but leave to_symbol_id unset —
        # workspace_indexer._resolve_cross_module_calls fills that in later,
        # once every file's symbols are indexed (the target file may not be
        # indexed yet at the point this file is being processed).
        self._extract_calls(
            tree.root_node,
            file_path=file_path,
            source=source,
            symbol_by_name=symbol_by_name,
            import_map=import_map,
            relationships=relationships,
            enclosing_symbol=None,
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
        import_map: dict[str, str] | None = None,
    ) -> None:
        """Extract import statements and build relationships.

        Phase 2b: Uses resolver to find target files and potentially symbols.

        import_map (if given) is populated with imported_name -> resolved
        target file path, so call-expression extraction can attach a known
        target file to calls of an imported name without needing to store
        a separate import relationship (imports themselves aren't stored
        as relationship records yet — see the TODO below).
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
                    if import_map is not None and target_file:
                        import_map[imported_name] = target_file

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
                import_map,
            )

    def _extract_calls(
        self,
        node,
        *,
        file_path: str,
        source: str,
        symbol_by_name: dict[str, SymbolRecord],
        import_map: dict[str, str],
        relationships: list[SymbolRelationshipRecord],
        enclosing_symbol: SymbolRecord | None,
    ) -> None:
        """Walk the tree for call_expression nodes and record "calls" relationships.

        Tracks the enclosing function/method as it descends so each call can
        be attributed to its caller, mirroring symbol_extractor.py's Python
        walker. Resolution:
          - same-file target (symbol_by_name) -> resolved immediately.
          - name matches an imported name (import_map) -> to_file_path set,
            to_symbol_id left for the indexer's later batched pass.
          - anything else (builtins, unresolvable member-call receivers,
            etc.) -> left fully unresolved, same as Python's fallback.
        """
        next_enclosing = enclosing_symbol
        if node.type in ("function_declaration", "async_function_declaration"):
            name = self._function_name(node, source)
            if name and name in symbol_by_name:
                next_enclosing = symbol_by_name[name]
        elif node.type == "method_definition":
            name = self._method_name(node, source)
            if name:
                # Methods are stored qualified as "ClassName.method" — find
                # the matching symbol by suffix since we don't have the
                # class name in scope at this node in isolation.
                for sym in symbol_by_name.values():
                    if sym.kind == "method" and sym.name.endswith(f".{name}"):
                        next_enclosing = sym
                        break
        elif node.type == "variable_declarator" and self._is_arrow_function(node):
            if node.children:
                name = _get_text(node.children[0], source)
                if name in symbol_by_name:
                    next_enclosing = symbol_by_name[name]

        if node.type == "call_expression" and next_enclosing is not None:
            callee_name = self._extract_call_target_name(node, source)
            if callee_name:
                target_symbol = symbol_by_name.get(callee_name)
                if target_symbol is None:
                    # Bare method/property name from a member call
                    # (this.foo() / obj.foo()) — match against qualified
                    # "ClassName.method" symbol names stored for methods.
                    for sym in symbol_by_name.values():
                        if sym.name.endswith(f".{callee_name}"):
                            target_symbol = sym
                            break
                to_symbol_id = target_symbol.id if target_symbol else None
                to_file_path = file_path if target_symbol else import_map.get(callee_name)
                call_end = node.children[0].end_point if node.children else node.end_point
                relationships.append(
                    SymbolRelationshipRecord(
                        id=make_relationship_id(
                            next_enclosing.id, callee_name, "calls", to_file_path
                        ),
                        from_symbol_id=next_enclosing.id,
                        to_symbol_id=to_symbol_id,
                        to_symbol_name=callee_name,
                        to_file_path=to_file_path,
                        relation_type="calls",
                        workspace_root="",
                        call_line=call_end[0] + 1,
                        call_col=max(0, call_end[1] - 1),
                    )
                )

        for child in node.children:
            self._extract_calls(
                child,
                file_path=file_path,
                source=source,
                symbol_by_name=symbol_by_name,
                import_map=import_map,
                relationships=relationships,
                enclosing_symbol=next_enclosing,
            )

    def _function_name(self, node, source: str) -> str | None:
        for child in node.children:
            if child.type == "identifier":
                return _get_text(child, source)
        return None

    def _method_name(self, node, source: str) -> str | None:
        for child in node.children:
            if child.type in ("property_identifier", "identifier"):
                return _get_text(child, source)
        return None

    def _extract_call_target_name(self, call_node, source: str) -> str | None:
        """Return the bare callee name for a call_expression's `function` child.

        Plain calls (foo()) have an `identifier` function child. Member
        calls (obj.method(), this.foo()) have a `member_expression` whose
        `property` child is the name actually being invoked — the receiver
        is intentionally ignored since without type inference we can't
        resolve its class.
        """
        if not call_node.children:
            return None
        target = call_node.children[0]
        if target.type == "identifier":
            return _get_text(target, source)
        if target.type == "member_expression":
            for child in target.children:
                if child.type == "property_identifier":
                    return _get_text(child, source)
        return None

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
