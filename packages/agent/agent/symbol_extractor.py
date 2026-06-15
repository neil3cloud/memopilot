"""Python AST symbol extraction for indexed files."""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass

from .graph_retriever import SymbolRelationshipRecord, make_relationship_id


@dataclass(frozen=True)
class SymbolRecord:
    id: str
    file_path: str
    name: str
    kind: str
    start_line: int
    end_line: int
    signature: str | None
    content_hash: str


class SymbolExtractor:
    """Extracts classes, functions, methods, and imports from Python code."""

    def extract(self, *, file_path: str, source: str, content_hash: str) -> list[SymbolRecord]:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []
        symbols: list[SymbolRecord] = []

        for node in tree.body:
            if isinstance(node, ast.Import):
                symbols.extend(
                    self._import_records(
                        file_path=file_path,
                        node=node,
                        content_hash=content_hash,
                    )
                )
            elif isinstance(node, ast.ImportFrom):
                symbols.append(
                    self._build_record(
                        file_path=file_path,
                        name=self._import_from_name(node),
                        kind="import",
                        node=node,
                        signature=self._import_from_signature(node),
                        content_hash=content_hash,
                    )
                )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append(
                    self._build_record(
                        file_path=file_path,
                        name=node.name,
                        kind="function",
                        node=node,
                        signature=self._function_signature(node),
                        content_hash=content_hash,
                    )
                )
            elif isinstance(node, ast.ClassDef):
                symbols.append(
                    self._build_record(
                        file_path=file_path,
                        name=node.name,
                        kind="class",
                        node=node,
                        signature=None,
                        content_hash=content_hash,
                    )
                )
                symbols.extend(
                    self._class_method_records(
                        file_path=file_path,
                        class_node=node,
                        content_hash=content_hash,
                    )
                )

        return symbols

    def extract_relationships(
        self,
        *,
        file_path: str,
        source: str,
        symbols: list[SymbolRecord],
        workspace_root: str = "",
    ) -> list[SymbolRelationshipRecord]:
        """Extract call/import/inheritance relationships from a Python source file.

        Must be called after extract() so that symbol IDs are already known.
        Relationships with unknown targets (external/stdlib) are stored with
        to_symbol_id=None so the graph is still traversable by name.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []

        symbol_by_name = {s.name: s for s in symbols}
        relationships: list[SymbolRelationshipRecord] = []

        for node in ast.walk(tree):
            # Import relationships
            if isinstance(node, ast.ImportFrom) and node.module:
                module_file = node.module.replace(".", "/") + ".py"
                from_sym = self._find_enclosing_symbol(node, symbols)
                if from_sym is None:
                    continue
                for alias in node.names:
                    target_name = alias.name
                    rel_id = make_relationship_id(
                        from_sym.id, target_name, "imports", module_file
                    )
                    relationships.append(
                        SymbolRelationshipRecord(
                            id=rel_id,
                            from_symbol_id=from_sym.id,
                            to_symbol_id=symbol_by_name.get(target_name, None) and
                                symbol_by_name[target_name].id,
                            to_symbol_name=target_name,
                            to_file_path=module_file,
                            relation_type="imports",
                            workspace_root=workspace_root,
                        )
                    )

            # Class inheritance
            elif isinstance(node, ast.ClassDef):
                class_sym = symbol_by_name.get(node.name)
                if class_sym is None:
                    continue
                for base in node.bases:
                    base_name = self._name_of(base)
                    if not base_name:
                        continue
                    rel_id = make_relationship_id(
                        class_sym.id, base_name, "inherits", None
                    )
                    relationships.append(
                        SymbolRelationshipRecord(
                            id=rel_id,
                            from_symbol_id=class_sym.id,
                            to_symbol_id=symbol_by_name.get(base_name, None) and
                                symbol_by_name[base_name].id,
                            to_symbol_name=base_name,
                            to_file_path=None,
                            relation_type="inherits",
                            workspace_root=workspace_root,
                        )
                    )

            # Function/method call relationships
            elif isinstance(node, ast.Call):
                callee_name = self._name_of(node.func)
                if not callee_name:
                    continue
                from_sym = self._find_enclosing_symbol(node, symbols)
                if from_sym is None:
                    continue
                rel_id = make_relationship_id(
                    from_sym.id, callee_name, "calls", None
                )
                relationships.append(
                    SymbolRelationshipRecord(
                        id=rel_id,
                        from_symbol_id=from_sym.id,
                        to_symbol_id=symbol_by_name.get(callee_name, None) and
                            symbol_by_name[callee_name].id,
                        to_symbol_name=callee_name,
                        to_file_path=None,
                        relation_type="calls",
                        workspace_root=workspace_root,
                    )
                )

        # Deduplicate by id (keep first occurrence)
        seen: set[str] = set()
        deduped: list[SymbolRelationshipRecord] = []
        for rel in relationships:
            if rel.id not in seen:
                seen.add(rel.id)
                deduped.append(rel)
        return deduped

    def _find_enclosing_symbol(
        self, node: ast.AST, symbols: list[SymbolRecord]
    ) -> SymbolRecord | None:
        """Find the innermost symbol (function/method/class) containing this node."""
        lineno = getattr(node, "lineno", None)
        if lineno is None:
            return None
        best: SymbolRecord | None = None
        for sym in symbols:
            if sym.kind not in ("function", "method", "class"):
                continue
            if sym.start_line <= lineno <= sym.end_line:
                if best is None or (sym.end_line - sym.start_line) < (best.end_line - best.start_line):
                    best = sym
        return best

    @staticmethod
    def _name_of(node: ast.expr) -> str | None:
        """Extract a dotted name from an AST expression, or None."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            obj = SymbolExtractor._name_of(node.value)
            return f"{obj}.{node.attr}" if obj else node.attr
        return None
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []
        symbols: list[SymbolRecord] = []

        for node in tree.body:
            if isinstance(node, ast.Import):
                symbols.extend(
                    self._import_records(
                        file_path=file_path,
                        node=node,
                        content_hash=content_hash,
                    )
                )
            elif isinstance(node, ast.ImportFrom):
                symbols.append(
                    self._build_record(
                        file_path=file_path,
                        name=self._import_from_name(node),
                        kind="import",
                        node=node,
                        signature=self._import_from_signature(node),
                        content_hash=content_hash,
                    )
                )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append(
                    self._build_record(
                        file_path=file_path,
                        name=node.name,
                        kind="function",
                        node=node,
                        signature=self._function_signature(node),
                        content_hash=content_hash,
                    )
                )
            elif isinstance(node, ast.ClassDef):
                symbols.append(
                    self._build_record(
                        file_path=file_path,
                        name=node.name,
                        kind="class",
                        node=node,
                        signature=None,
                        content_hash=content_hash,
                    )
                )
                symbols.extend(
                    self._class_method_records(
                        file_path=file_path,
                        class_node=node,
                        content_hash=content_hash,
                    )
                )

        return symbols

    def _class_method_records(
        self, *, file_path: str, class_node: ast.ClassDef, content_hash: str
    ) -> list[SymbolRecord]:
        method_records: list[SymbolRecord] = []
        for node in class_node.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                method_records.append(
                    self._build_record(
                        file_path=file_path,
                        name=f"{class_node.name}.{node.name}",
                        kind="method",
                        node=node,
                        signature=self._function_signature(node),
                        content_hash=content_hash,
                    )
                )
        return method_records

    def _import_records(
        self, *, file_path: str, node: ast.Import, content_hash: str
    ) -> list[SymbolRecord]:
        records: list[SymbolRecord] = []
        for alias in node.names:
            records.append(
                self._build_record(
                    file_path=file_path,
                    name=alias.asname or alias.name,
                    kind="import",
                    node=node,
                    signature=alias.name,
                    content_hash=content_hash,
                )
            )
        return records

    def _import_from_name(self, node: ast.ImportFrom) -> str:
        module_name = node.module or "."
        imported = ",".join(alias.asname or alias.name for alias in node.names)
        return f"{module_name}:{imported}"

    def _import_from_signature(self, node: ast.ImportFrom) -> str:
        module_name = node.module or "."
        imported = ", ".join(alias.name for alias in node.names)
        return f"from {module_name} import {imported}"

    def _function_signature(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        return f"{node.name}({ast.unparse(node.args)})"

    def _build_record(
        self,
        *,
        file_path: str,
        name: str,
        kind: str,
        node: ast.AST,
        signature: str | None,
        content_hash: str,
    ) -> SymbolRecord:
        start_line = getattr(node, "lineno", 1)
        end_line = getattr(node, "end_lineno", start_line)
        symbol_id = hashlib.sha1(f"{file_path}:{name}:{kind}:{start_line}".encode()).hexdigest()
        return SymbolRecord(
            id=symbol_id,
            file_path=file_path,
            name=name,
            kind=kind,
            start_line=start_line,
            end_line=end_line,
            signature=signature,
            content_hash=content_hash,
        )
