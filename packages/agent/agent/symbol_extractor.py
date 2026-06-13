"""Python AST symbol extraction for indexed files."""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass


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
