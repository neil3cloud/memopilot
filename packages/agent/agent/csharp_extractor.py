"""C# symbol extraction using tree-sitter."""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from tree_sitter import Language, Parser

from .graph_retriever import SymbolRelationshipRecord
from .symbol_extractor import SymbolRecord

logger = logging.getLogger(__name__)


class CSharpExtractor:
    """Extract C# symbols from source code using tree-sitter."""

    extensions = (".cs",)
    language = "csharp"

    def __init__(self) -> None:
        """Initialize C# parser with tree-sitter."""
        try:
            # Import is deferred to avoid circular imports
            from tree_sitter_c_sharp import language as language_csharp

            self._parser = Parser()
            self._parser.language = Language(language_csharp())
            self._content_hash = ""
        except Exception as e:
            logger.error(f"Failed to initialize C# parser: {e}")
            self._parser = None
            self._content_hash = ""

    def extract(
        self,
        file_path: str,
        source: str,
        content_hash: str,
    ) -> list[SymbolRecord]:
        """Extract C# symbols (classes, methods, properties, enums, etc.) from source."""
        if not self._parser:
            return []

        try:
            tree = self._parser.parse(source.encode("utf-8"))
            symbols: list[SymbolRecord] = []
            self._content_hash = content_hash

            self._walk_tree(tree.root_node, source, file_path, symbols)
            return symbols
        except Exception as e:
            logger.warning(f"C# extraction failed for {file_path}: {e}")
            return []

    def extract_relationships(
        self,
        file_path: str,
        source: str,
        symbols: list[SymbolRecord],
        workspace_root: str,
    ) -> list[SymbolRelationshipRecord]:
        """Extract C# relationships (using statements, inheritance, DI injection)."""
        from .graph_retriever import make_relationship_id

        relationships: list[SymbolRelationshipRecord] = []

        # Extract using directives for namespace imports
        # Note: Create one relationship per using statement, not per (symbol × using) pair
        using_statements = self._extract_using_statements(source)
        # Use the first symbol (usually the class/primary type) as the source
        if symbols and using_statements:
            primary_symbol = symbols[0]
            for using_namespace in using_statements:
                rel_id = make_relationship_id(
                    primary_symbol.id, using_namespace, "import", None
                )
                relationships.append(
                    SymbolRelationshipRecord(
                        id=rel_id,
                        from_symbol_id=primary_symbol.id,
                        to_symbol_id=None,
                        to_symbol_name=using_namespace,
                        to_file_path=None,
                        relation_type="imports",
                        workspace_root=workspace_root,
                    )
                )

        # Extract base class/interface implementations (inheritance)
        inheritance = self._extract_inheritance(source, symbols)
        for from_id, to_name in inheritance:
            rel_id = make_relationship_id(from_id, to_name, "inherits", None)
            relationships.append(
                SymbolRelationshipRecord(
                    id=rel_id,
                    from_symbol_id=from_id,
                    to_symbol_id=None,
                    to_symbol_name=to_name,
                    to_file_path=None,
                    relation_type="inherits",
                    workspace_root=workspace_root,
                )
            )

        # Extract HTTP route handlers (ASP.NET Core attributes)
        routes = self._extract_http_routes(source, symbols)
        for symbol_id, http_method, route_pattern in routes:
            route_name = f"{http_method} {route_pattern}"
            rel_id = make_relationship_id(symbol_id, route_name, "references", None)
            relationships.append(
                SymbolRelationshipRecord(
                    id=rel_id,
                    from_symbol_id=symbol_id,
                    to_symbol_id=None,
                    to_symbol_name=route_name,
                    to_file_path=None,
                    relation_type="references",
                    workspace_root=workspace_root,
                )
            )

        return relationships

    def _walk_tree(self, node, source: str, file_path: str, symbols: list[SymbolRecord]) -> None:
        """Recursively walk AST tree to extract symbols."""
        if node.type == "class_declaration":
            self._extract_class_symbol(node, source, file_path, symbols)
        elif node.type == "method_declaration":
            parent_class = self._find_parent_class_name(node, source)
            self._extract_method_symbol(node, source, file_path, symbols, parent_class=parent_class)
        elif node.type == "property_declaration":
            self._extract_property_symbol(node, source, file_path, symbols)
        elif node.type == "enum_declaration":
            self._extract_enum_symbol(node, source, file_path, symbols)
        elif node.type == "interface_declaration":
            self._extract_interface_symbol(node, source, file_path, symbols)
        elif node.type == "struct_declaration":
            self._extract_struct_symbol(node, source, file_path, symbols)

        for child in node.children:
            self._walk_tree(child, source, file_path, symbols)

    def _extract_class_symbol(
        self,
        node,
        source: str,
        file_path: str,
        symbols: list[SymbolRecord],
    ) -> None:
        """Extract class symbol and its methods."""
        name = self._get_node_text(node, source, "identifier")
        if not name:
            return

        signature = self._extract_signature(node, source)
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        symbol_id = hashlib.sha256(f"{file_path}:{name}:{start_line}".encode()).hexdigest()[:16]

        record = SymbolRecord(
            id=symbol_id,
            file_path=file_path,
            name=name,
            kind="class",
            start_line=start_line,
            end_line=end_line,
            signature=signature,
            content_hash=self._content_hash,
        )
        symbols.append(record)

    def _extract_method_symbol(
        self,
        node,
        source: str,
        file_path: str,
        symbols: list[SymbolRecord],
        parent_class: str | None = None,
    ) -> None:
        """Extract method symbol."""
        # Extract method name from the method_declaration node
        # The method name is typically after 'public/private/protected' and return type
        method_text = source[node.start_byte : node.end_byte]
        
        # Find method name: look for pattern after modifiers and return type
        # Pattern: (public|private|protected|internal|static|async|virtual|override)? <returnType> <methodName>(
        match = re.search(
            r"(?:public|private|protected|internal|static|async|virtual|override|sealed)?\s+"
            r"(?:async\s+)?"
            r"(?:[a-zA-Z_][a-zA-Z0-9_<>,\[\]]*\s+)?"  # return type
            r"([a-zA-Z_][a-zA-Z0-9_]*)\s*\(",
            method_text
        )
        if not match:
            return
        
        name = match.group(1)
        
        # Skip if the name looks like a type or keyword
        if name in ("async", "public", "private", "protected", "internal", "static", "virtual", "abstract", "override", "sealed"):
            return

        signature = self._extract_signature(node, source)
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        symbol_id = hashlib.sha256(f"{file_path}:{name}:{start_line}".encode()).hexdigest()[:16]

        kind = "method" if parent_class else "function"

        record = SymbolRecord(
            id=symbol_id,
            file_path=file_path,
            name=name,
            kind=kind,
            start_line=start_line,
            end_line=end_line,
            signature=signature,
            content_hash=self._content_hash,
        )
        symbols.append(record)

    def _extract_property_symbol(
        self,
        node,
        source: str,
        file_path: str,
        symbols: list[SymbolRecord],
    ) -> None:
        """Extract property symbol."""
        name = self._get_node_text(node, source, "identifier")
        if not name:
            return

        signature = self._extract_signature(node, source)
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        symbol_id = hashlib.sha256(f"{file_path}:{name}:{start_line}".encode()).hexdigest()[:16]

        record = SymbolRecord(
            id=symbol_id,
            file_path=file_path,
            name=name,
            kind="property",
            start_line=start_line,
            end_line=end_line,
            signature=signature,
            content_hash=self._content_hash,
        )
        symbols.append(record)

    def _extract_enum_symbol(
        self,
        node,
        source: str,
        file_path: str,
        symbols: list[SymbolRecord],
    ) -> None:
        """Extract enum symbol."""
        name = self._get_node_text(node, source, "identifier")
        if not name:
            return

        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        symbol_id = hashlib.sha256(f"{file_path}:{name}:{start_line}".encode()).hexdigest()[:16]

        record = SymbolRecord(
            id=symbol_id,
            file_path=file_path,
            name=name,
            kind="enum",
            start_line=start_line,
            end_line=end_line,
            signature=f"enum {name}",
            content_hash=self._content_hash,
        )
        symbols.append(record)

    def _extract_interface_symbol(
        self,
        node,
        source: str,
        file_path: str,
        symbols: list[SymbolRecord],
    ) -> None:
        """Extract interface symbol."""
        name = self._get_node_text(node, source, "identifier")
        if not name:
            return

        signature = self._extract_signature(node, source)
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        symbol_id = hashlib.sha256(f"{file_path}:{name}:{start_line}".encode()).hexdigest()[:16]

        record = SymbolRecord(
            id=symbol_id,
            file_path=file_path,
            name=name,
            kind="interface",
            start_line=start_line,
            end_line=end_line,
            signature=signature,
            content_hash=self._content_hash,
        )
        symbols.append(record)

    def _extract_struct_symbol(
        self,
        node,
        source: str,
        file_path: str,
        symbols: list[SymbolRecord],
    ) -> None:
        """Extract struct/record symbol."""
        name = self._get_node_text(node, source, "identifier")
        if not name:
            return

        signature = self._extract_signature(node, source)
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        symbol_id = hashlib.sha256(f"{file_path}:{name}:{start_line}".encode()).hexdigest()[:16]

        record = SymbolRecord(
            id=symbol_id,
            file_path=file_path,
            name=name,
            kind="struct",
            start_line=start_line,
            end_line=end_line,
            signature=signature,
            content_hash=self._content_hash,
        )
        symbols.append(record)

    def _extract_namespace_from_file(self, source: str) -> str:
        """Extract namespace declaration from file."""
        match = re.match(r"namespace\s+([^;{\s]+)", source)
        return match.group(1) if match else ""

    def _extract_signature(self, node, source: str) -> str:
        """Extract method/class signature."""
        return source[node.start_byte : min(node.start_byte + 200, node.end_byte)].split("\n")[0]

    def _get_node_text(self, node, source: str, node_type: str = "") -> str:
        """Extract text from specific node type."""
        if node_type:
            for child in node.children:
                if child.type == node_type:
                    return source[child.start_byte : child.end_byte].strip()
        return source[node.start_byte : node.end_byte].strip()

    def _extract_using_statements(self, source: str) -> list[str]:
        """Extract all using statements (namespace imports).
        Excludes 'using static' and 'using X = Y' alias directives.
        """
        result = []
        # Match: using <namespace> ;
        # Exclude: using static <type> ; and using <alias> = <namespace> ;
        pattern = r"using\s+(?!static\b)(?!=)([a-zA-Z_][a-zA-Z0-9_.]*?)\s*;"
        for match in re.finditer(pattern, source):
            namespace = match.group(1).strip()
            if namespace:  # Ensure non-empty
                result.append(namespace)
        return result

    def _find_parent_class_name(self, node, source: str) -> str | None:
        """Walk up ancestors to find the enclosing class for method kind detection."""
        parent = getattr(node, "parent", None)
        while parent is not None:
            if parent.type == "class_declaration":
                return self._get_node_text(parent, source, "identifier")
            parent = getattr(parent, "parent", None)
        return None

    def _extract_inheritance(self, source: str, symbols: list[SymbolRecord]) -> list[tuple[str, str]]:
        """Extract inheritance relationships (base class/interface implementations)."""
        relationships: list[tuple[str, str]] = []

        # Look for class/interface inheritance patterns in the source
        for symbol in symbols:
            if symbol.kind in ("class", "interface", "struct"):
                # Pattern: ClassName : BaseClass or IInterface, IAnother
                pattern = rf"{re.escape(symbol.name)}\s*:\s*([^{{]+)"
                match = re.search(pattern, source)
                if match:
                    bases = match.group(1).split(",")
                    for base in bases:
                        base_name = base.strip().split("<")[0].strip()
                        if base_name:
                            relationships.append((symbol.id, base_name))

        return relationships

    def _extract_http_routes(
        self,
        source: str,
        symbols: list[SymbolRecord],
    ) -> list[tuple[str, str, str]]:
        """Extract HTTP route handlers from ASP.NET Core attributes."""
        routes: list[tuple[str, str, str]] = []

        # Find all [HttpGet], [HttpPost], etc. decorators
        http_pattern = r"\[(Http(Get|Post|Put|Delete|Patch|Head|Options)(?:\(([^)]*)\))?)\]"
        controller_pattern = r"\[Route\(\"([^\"]+)\"\)\]"

        controller_route = ""
        match = re.search(controller_pattern, source)
        if match:
            controller_route = match.group(1)

        for match in re.finditer(http_pattern, source):
            http_method = match.group(2)
            route_part = match.group(3) or ""
            route_part = route_part.strip('"')

            # Find the method name after the attribute
            remaining_source = source[match.end() :]
            method_match = re.search(r"public\s+(?:async\s+)?[a-zA-Z<>_\[\]]+\s+([a-zA-Z_][a-zA-Z0-9_]*)", remaining_source)

            if method_match:
                method_name = method_match.group(1)
                full_route = f"{controller_route}/{route_part}".replace("//", "/")

                # Find the symbol ID for this method
                for symbol in symbols:
                    if symbol.name == method_name and symbol.kind in ("method", "function"):
                        routes.append((symbol.id, http_method.upper(), full_route))
                        break

        return routes
