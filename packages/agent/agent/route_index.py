"""ASP.NET Core route index for HTTP endpoint discovery."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional
from pathlib import Path
import re

logger = logging.getLogger(__name__)


@dataclass
class RouteEntry:
    """A single HTTP route entry."""
    http_method: str  # GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS
    path: str  # Full route path (e.g., /api/orders/{id})
    controller_class: str  # Name of controller class
    action_method: str  # Name of action method
    symbol_id: str  # SHA-256 symbol ID
    file_path: str  # File containing the action
    start_line: int  # Line number of method


class RouteIndex:
    """Index for fast route → action lookup.
    
    Enables queries like:
    - What action handles GET /api/orders/{id}?
    - What routes are in OrdersController?
    - Which routes require authorization?
    """

    def __init__(self):
        """Initialize route index."""
        self._routes: list[RouteEntry] = []
        self._by_path: dict[str, list[RouteEntry]] = {}
        self._by_method: dict[str, list[RouteEntry]] = {}
        self._by_controller: dict[str, list[RouteEntry]] = {}
        self._by_symbol_id: dict[str, RouteEntry] = {}

    def add_route(self, entry: RouteEntry) -> None:
        """Add a route to the index.
        
        Args:
            entry: RouteEntry to add
        """
        self._routes.append(entry)
        
        # Index by exact path
        if entry.path not in self._by_path:
            self._by_path[entry.path] = []
        self._by_path[entry.path].append(entry)
        
        # Index by HTTP method
        if entry.http_method not in self._by_method:
            self._by_method[entry.http_method] = []
        self._by_method[entry.http_method].append(entry)
        
        # Index by controller
        if entry.controller_class not in self._by_controller:
            self._by_controller[entry.controller_class] = []
        self._by_controller[entry.controller_class].append(entry)
        
        # Index by symbol ID (one-to-one)
        self._by_symbol_id[entry.symbol_id] = entry
        
        logger.debug(f"Indexed route: {entry.http_method} {entry.path} → {entry.controller_class}.{entry.action_method}")

    def find_route(self, http_method: str, path: str) -> Optional[RouteEntry]:
        """Find a route by HTTP method and path (exact match).
        
        Args:
            http_method: HTTP method (GET, POST, etc.)
            path: Full path (e.g., /api/orders/{id})
            
        Returns:
            RouteEntry or None if not found
        """
        http_method = http_method.upper()
        
        # Try exact match
        for route in self._by_path.get(path, []):
            if route.http_method == http_method:
                return route
        
        # Try parameter substitution (e.g., {id} matches :id or {id})
        normalized_path = self._normalize_path(path)
        for route_path, routes in self._by_path.items():
            if self._normalize_path(route_path) == normalized_path:
                for route in routes:
                    if route.http_method == http_method:
                        return route
        
        return None

    def find_routes_by_method(self, http_method: str) -> list[RouteEntry]:
        """Find all routes for an HTTP method.
        
        Args:
            http_method: HTTP method (GET, POST, etc.)
            
        Returns:
            List of RouteEntry objects
        """
        return self._by_method.get(http_method.upper(), [])

    def find_routes_by_controller(self, controller_name: str) -> list[RouteEntry]:
        """Find all routes in a controller.
        
        Args:
            controller_name: Controller class name (e.g., 'OrdersController')
            
        Returns:
            List of RouteEntry objects
        """
        return self._by_controller.get(controller_name, [])

    def find_route_by_symbol_id(self, symbol_id: str) -> Optional[RouteEntry]:
        """Find a route by symbol ID.
        
        Args:
            symbol_id: SHA-256 symbol ID
            
        Returns:
            RouteEntry or None if not found
        """
        return self._by_symbol_id.get(symbol_id)

    def all_routes(self) -> list[RouteEntry]:
        """Get all routes in the index.
        
        Returns:
            List of all RouteEntry objects
        """
        return self._routes.copy()

    def _normalize_path(self, path: str) -> str:
        """Normalize path for comparison.
        
        Converts {id} style params to * for loose matching.
        
        Args:
            path: Route path
            
        Returns:
            Normalized path
        """
        # Convert {param} to * for pattern matching
        return re.sub(r"\{[^}]+\}", "*", path)

    def to_json(self) -> str:
        """Serialize route index to JSON.
        
        Returns:
            JSON string representation
        """
        routes_data = [
            {
                "http_method": route.http_method,
                "path": route.path,
                "controller_class": route.controller_class,
                "action_method": route.action_method,
                "symbol_id": route.symbol_id,
                "file_path": route.file_path,
                "start_line": route.start_line,
            }
            for route in self._routes
        ]
        return json.dumps(routes_data, indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> RouteIndex:
        """Deserialize route index from JSON.
        
        Args:
            json_str: JSON string
            
        Returns:
            RouteIndex instance
        """
        index = cls()
        routes_data = json.loads(json_str)
        
        for route_dict in routes_data:
            entry = RouteEntry(
                http_method=route_dict["http_method"],
                path=route_dict["path"],
                controller_class=route_dict["controller_class"],
                action_method=route_dict["action_method"],
                symbol_id=route_dict["symbol_id"],
                file_path=route_dict["file_path"],
                start_line=route_dict["start_line"],
            )
            index.add_route(entry)
        
        return index


def extract_routes_from_csharp(
    source: str,
    symbols: dict[str, tuple[str, int]],  # symbol_name -> (symbol_id, line_number)
    file_path: str,
    current_controller: str = "",
) -> list[RouteEntry]:
    """Extract ASP.NET Core routes from C# source.
    
    Args:
        source: C# source code
        symbols: Dict of symbol names to (id, line_number) tuples
        file_path: Path to the C# file
        current_controller: Current controller class name being processed
        
    Returns:
        List of RouteEntry objects
    """
    routes: list[RouteEntry] = []

    # Pattern: [HttpGet("path")] or [HttpPost] or [HttpGet]
    # Group 1: Get|Post|Put|Delete|etc., Group 2: optional path
    http_pattern = r"\[Http(Get|Post|Put|Delete|Patch|Head|Options)(?:\(\"?([^\")\]]*)?\"?\))?\]"
    
    # Pattern: class XyzController
    controller_pattern = r"public\s+(?:abstract\s+)?class\s+([a-zA-Z_][a-zA-Z0-9_]*Controller)"
    
    # Find controller base route [Route("api/[controller]")] or [Route("path")]
    controller_route_pattern = r"\[Route\(\"([^\"]+)\"\)\]"
    
    # Track current controller
    current_controller = ""
    controller_base_route = ""
    
    lines = source.split("\n")
    
    # First pass: find controller definitions and routes
    for i, line in enumerate(lines, 1):
        # Check for controller class definition
        controller_match = re.search(controller_pattern, line)
        if controller_match:
            current_controller = controller_match.group(1)
            # Look backward for [Route] attribute
            for j in range(i - 2, max(0, i - 10), -1):
                route_match = re.search(controller_route_pattern, lines[j - 1])
                if route_match:
                    controller_base_route = route_match.group(1)
                    break
            else:
                controller_base_route = ""
        
        # Check for HTTP method attributes
        for http_match in re.finditer(http_pattern, line):
            http_verb = http_match.group(1)  # Get, Post, Put, Delete, etc.
            http_method = http_verb.upper()  # GET, POST, PUT, DELETE, etc.
            route_part = http_match.group(2) or ""
            
            # Find the method name on this line or next few lines
            for j in range(i, min(i + 5, len(lines) + 1)):
                method_line = lines[j - 1]
                method_match = re.search(
                    r"public\s+(?:async\s+)?(?:[a-zA-Z<>_\[\]]+\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s*\(",
                    method_line
                )
                if method_match:
                    method_name = method_match.group(1)
                    
                    # Look up symbol ID
                    if method_name in symbols:
                        symbol_id, start_line = symbols[method_name]
                        
                        # Construct full route
                        full_route = controller_base_route
                        if route_part:
                            if full_route:
                                full_route = f"{full_route}/{route_part}"
                            else:
                                full_route = f"/{route_part}"
                        
                        # Clean up route (remove double slashes, etc.)
                        full_route = "/" + full_route.lstrip("/").replace("//", "/")
                        
                        entry = RouteEntry(
                            http_method=http_method,
                            path=full_route,
                            controller_class=current_controller,
                            action_method=method_name,
                            symbol_id=symbol_id,
                            file_path=file_path,
                            start_line=start_line,
                        )
                        routes.append(entry)
                        logger.debug(f"Extracted route: {http_method} {full_route}")
                    break

    return routes
