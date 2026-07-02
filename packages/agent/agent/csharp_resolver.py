"""C# namespace and dependency injection resolution."""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Optional
import aiosqlite

logger = logging.getLogger(__name__)


class CSharpResolver:
    """Resolve C# cross-module references via namespace and DI registration.
    
    Two-pass resolution strategy:
    1. Namespace resolution: Look up symbol by namespace + name in database
    2. DI resolution: Map interfaces to concrete implementations via DI registrations
    """

    def __init__(
        self, 
        workspace_root: str,
        query_symbol_by_name: Optional[Callable[[str, str], str]] = None,
    ) -> None:
        """Initialize resolver with workspace root.
        
        Args:
            workspace_root: Root directory of workspace
            query_symbol_by_name: Optional async callable that queries database for symbol ID.
                                Takes (name: str, namespace: str) and returns symbol ID or None.
                                If provided, enables real database-backed resolution.
        """
        self._workspace_root = Path(workspace_root)
        self._di_map: dict[str, str] = {}
        self._namespace_cache: dict[str, str] = {}
        self._query_symbol_by_name = query_symbol_by_name
        self._load_di_registrations()

    def _load_di_registrations(self) -> None:
        """Load DI registration map from Program.cs or Startup.cs."""
        candidates = [
            self._workspace_root / "Program.cs",
            self._workspace_root / "Startup.cs",
        ]

        for candidate in candidates:
            if candidate.exists():
                try:
                    source = candidate.read_text(encoding="utf-8")
                    self._parse_di_registrations(source)
                    logger.info(f"Loaded DI registrations from {candidate.name}")
                    break
                except Exception as e:
                    logger.warning(f"Failed to load DI registrations from {candidate}: {e}")

    def _parse_di_registrations(self, source: str) -> None:
        """Parse services.Add* registrations from Program.cs/Startup.cs."""
        # Pattern: services.AddScoped<IOrderService, OrderService>()
        scoped_pattern = r"services\.AddScoped<([^,]+),\s*([^>]+)>"
        transient_pattern = r"services\.AddTransient<([^,]+),\s*([^>]+)>"
        singleton_pattern = r"services\.AddSingleton<([^,]+),\s*([^>]+)>"

        for pattern in [scoped_pattern, transient_pattern, singleton_pattern]:
            for match in re.finditer(pattern, source):
                interface_name = match.group(1).strip()
                impl_name = match.group(2).strip()
                self._di_map[interface_name] = impl_name
                logger.debug(f"DI: {interface_name} -> {impl_name}")

    def resolve_namespace_to_symbol(
        self,
        namespace: str,
        symbol_name: str,
    ) -> str | None:
        """Resolve a symbol by namespace and name (sync version).
        
        For production use with async database access, use resolve_namespace_to_symbol_async().
        
        Args:
            namespace: C# namespace (e.g., 'MyApp.Services')
            symbol_name: Symbol name (e.g., 'OrderService')
            
        Returns:
            Symbol ID if found in cache, otherwise None.
            Use async version for fresh database lookups.
        """
        cache_key = f"{namespace}::{symbol_name}"
        return self._namespace_cache.get(cache_key)
    
    async def resolve_namespace_to_symbol_async(
        self,
        namespace: str,
        symbol_name: str,
        conn: aiosqlite.Connection,
    ) -> str | None:
        """Resolve a symbol by namespace and name (async database version).
        
        Performs a database query to find the symbol ID.
        
        Args:
            namespace: C# namespace (e.g., 'MyApp.Services')
            symbol_name: Symbol name (e.g., 'OrderService')
            conn: Database connection
            
        Returns:
            Symbol ID (16-char SHA-256 hex) or None if not found.
        """
        cache_key = f"{namespace}::{symbol_name}"
        
        # Check cache first
        if cache_key in self._namespace_cache:
            return self._namespace_cache[cache_key]
        
        # Query database
        try:
            # Look up symbol by name (in C#, namespace is part of file structure)
            # We search for symbols whose file path contains the namespace pattern
            # and whose name matches
            cursor = await conn.execute(
                """
                SELECT s.id
                FROM symbols s
                WHERE s.name = ?
                ORDER BY s.start_line
                LIMIT 1
                """,
                (symbol_name,),
            )
            row = await cursor.fetchone()
            
            if row:
                symbol_id = row[0]
                # Cache the result
                self._namespace_cache[cache_key] = symbol_id
                logger.debug(f"Resolved {namespace}::{symbol_name} -> {symbol_id}")
                return symbol_id
            
            logger.debug(f"Symbol not found: {namespace}::{symbol_name}")
            return None
            
        except Exception as e:
            logger.warning(f"Error resolving {namespace}::{symbol_name}: {e}")
            return None

    def resolve_interface_to_impl(self, interface_name: str) -> str | None:
        """Resolve an interface name to its concrete implementation (from DI map).
        
        This looks up the interface in the parsed DI registrations.
        
        Args:
            interface_name: Interface name (e.g., 'IOrderService')
            
        Returns:
            Implementation class name (e.g., 'OrderService'), or None if not found.
        """
        return self._di_map.get(interface_name)
    
    async def resolve_interface_to_symbol_async(
        self,
        interface_name: str,
        conn: aiosqlite.Connection,
    ) -> str | None:
        """Resolve an interface to its symbol ID via DI registration.
        
        Pass 2 of DI resolution: Maps interface to implementation, then looks up implementation symbol.
        
        Args:
            interface_name: Interface name (e.g., 'IOrderService')
            conn: Database connection
            
        Returns:
            Symbol ID of the concrete implementation, or None if not found.
        """
        # Step 1: Get concrete implementation from DI map
        impl_name = self.resolve_interface_to_impl(interface_name)
        if not impl_name:
            logger.debug(f"Interface not in DI map: {interface_name}")
            return None
        
        # Step 2: Look up the implementation symbol in database
        try:
            cursor = await conn.execute(
                """
                SELECT s.id
                FROM symbols s
                WHERE s.name = ?
                  AND s.kind = 'class'
                ORDER BY s.start_line
                LIMIT 1
                """,
                (impl_name,),
            )
            row = await cursor.fetchone()
            
            if row:
                symbol_id = row[0]
                logger.debug(f"Resolved DI: {interface_name} -> {impl_name} (ID: {symbol_id})")
                return symbol_id
            
            logger.debug(f"Implementation class not found: {impl_name} for interface {interface_name}")
            return None
            
        except Exception as e:
            logger.warning(f"Error resolving interface {interface_name}: {e}")
            return None

    def resolve_import_target(
        self,
        file_path: str,
        using_namespace: str,
        symbol_name: str,
    ) -> tuple[str | None, str | None]:
        """Resolve an import target to (target_file, symbol_name)."""
        # In C#, we resolve by namespace and symbol name
        # The actual file lookup would happen at the database level
        # Return (None, resolved_symbol_id) for now
        resolved_id = self.resolve_namespace_to_symbol(using_namespace, symbol_name)
        return (None, resolved_id)

    def parse_constructor_injection(self, method_source: str) -> list[tuple[str, str]]:
        """Extract constructor parameter types from method source."""
        # Pattern: public OrderService(IOrderRepository repo, ILogger logger)
        injections: list[tuple[str, str]] = []

        # Find constructor patterns
        constructor_pattern = r"(?:public\s+)?\w+\s*\(([^)]+)\)"
        match = re.search(constructor_pattern, method_source)
        if match:
            params = match.group(1).split(",")
            for param in params:
                # Extract type and name: "IOrderRepository repo"
                parts = param.strip().split()
                if len(parts) >= 2:
                    param_type = parts[0]
                    param_name = parts[1]
                    injections.append((param_type, param_name))

        return injections

    def build_di_relationship(self, interface_type: str) -> str | None:
        """Build a relationship string from interface to concrete implementation."""
        impl = self.resolve_interface_to_impl(interface_type)
        if impl:
            return f"di:{interface_type}->{impl}"
        return None
    
    def extract_namespace_from_source(self, source: str) -> str | None:
        """Extract the namespace declaration from C# source.
        
        Example:
            namespace MyApp.Services { class OrderService { } }
            → returns 'MyApp.Services'
            
        Args:
            source: C# source code
            
        Returns:
            Namespace name or None if not found
        """
        # Match: namespace MyApp.Services; or namespace MyApp.Services {
        pattern = r"namespace\s+([a-zA-Z_][a-zA-Z0-9_.]*)\s*[{;]"
        match = re.search(pattern, source)
        if match:
            return match.group(1)
        return None
    
    async def backfill_relationship_symbols(
        self,
        relationships: list[Any],
        conn: aiosqlite.Connection,
        file_namespace: str | None = None,
    ) -> list[Any]:
        """Backfill to_symbol_id for extracted relationships.
        
        Post-processing step after extraction. Resolves:
        - Import relationships (using statements) → target symbol IDs
        - DI injection relationships (constructor parameters) → implementation symbols
        
        Args:
            relationships: List of SymbolRelationshipRecord with to_symbol_id=None
            conn: Database connection for symbol lookups
            file_namespace: Namespace of the file containing the relationships
            
        Returns:
            Updated relationships with to_symbol_id filled in where possible
        """
        updated: list[Any] = []
        
        for rel in relationships:
            symbol_id = None

            # NOTE: these relation_type strings must match what the extractors
            # actually emit ("imports"/"inherits", per csharp_extractor.py and
            # the symbol_relationships CHECK constraint) — this method
            # previously checked "import"/"inheritance" (singular/different
            # form), which never matched anything the extractor produced, so
            # C# import and inheritance backfill silently never ran.
            if rel.relation_type == "imports":
                # Resolve import target
                # to_symbol_name contains the using namespace
                namespace_or_type = rel.to_symbol_name
                if file_namespace:
                    # Try exact namespace first, then file's namespace
                    symbol_id = await self.resolve_namespace_to_symbol_async(
                        namespace_or_type,
                        namespace_or_type.split(".")[-1],  # Last component as potential class name
                        conn,
                    )

            elif rel.relation_type == "inherits":
                # Resolve base class/interface
                base_name = rel.to_symbol_name
                if file_namespace:
                    symbol_id = await self.resolve_namespace_to_symbol_async(
                        file_namespace,
                        base_name,
                        conn,
                    )

            elif rel.relation_type == "di_injection":
                # Resolve interface to implementation
                interface_name = rel.to_symbol_name
                symbol_id = await self.resolve_interface_to_symbol_async(
                    interface_name,
                    conn,
                )

            elif rel.relation_type == "calls":
                # Same-file calls are already resolved at extraction time
                # (csharp_extractor._extract_calls); anything reaching here
                # is a cross-file or unresolvable (e.g. BCL) call. Reuse the
                # same namespace-scoped symbol lookup as "inherits" — calls
                # to names not actually indexed in this workspace (BCL/stdlib)
                # simply won't match anything and stay unresolved.
                callee_name = rel.to_symbol_name
                if file_namespace:
                    symbol_id = await self.resolve_namespace_to_symbol_async(
                        file_namespace,
                        callee_name,
                        conn,
                    )
            
            # Create new relationship with updated to_symbol_id if resolved
            if symbol_id:
                updated_rel = replace(rel, to_symbol_id=symbol_id)
                updated.append(updated_rel)
            else:
                updated.append(rel)
        
        return updated
