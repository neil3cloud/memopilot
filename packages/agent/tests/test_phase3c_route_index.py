"""Tests for Phase 3c: ASP.NET Core route indexing."""

import pytest
from tempfile import TemporaryDirectory
from pathlib import Path

from agent.route_index import RouteIndex, RouteEntry, extract_routes_from_csharp


class TestRouteIndex:
    """Test route index creation and querying."""

    def test_add_single_route(self):
        """Test adding a single route to index."""
        index = RouteIndex()
        entry = RouteEntry(
            http_method="GET",
            path="/api/orders",
            controller_class="OrdersController",
            action_method="GetOrders",
            symbol_id="abc123",
            file_path="/app/Controllers/OrdersController.cs",
            start_line=10,
        )
        
        index.add_route(entry)
        
        assert len(index.all_routes()) == 1
        assert index.find_route("GET", "/api/orders") == entry

    def test_add_multiple_routes(self):
        """Test adding multiple routes."""
        index = RouteIndex()
        
        routes = [
            RouteEntry("GET", "/api/orders", "OrdersController", "GetOrders", "id1", "file.cs", 10),
            RouteEntry("GET", "/api/orders/{id}", "OrdersController", "GetOrder", "id2", "file.cs", 15),
            RouteEntry("POST", "/api/orders", "OrdersController", "CreateOrder", "id3", "file.cs", 20),
        ]
        
        for route in routes:
            index.add_route(route)
        
        assert len(index.all_routes()) == 3

    def test_find_route_by_method_and_path(self):
        """Test finding route by HTTP method and path."""
        index = RouteIndex()
        entry = RouteEntry(
            http_method="POST",
            path="/api/orders",
            controller_class="OrdersController",
            action_method="CreateOrder",
            symbol_id="post123",
            file_path="file.cs",
            start_line=20,
        )
        
        index.add_route(entry)
        
        assert index.find_route("POST", "/api/orders") == entry
        assert index.find_route("GET", "/api/orders") is None

    def test_find_routes_by_http_method(self):
        """Test finding all routes for an HTTP method."""
        index = RouteIndex()
        
        index.add_route(RouteEntry("GET", "/api/orders", "O", "GetOrders", "id1", "f.cs", 10))
        index.add_route(RouteEntry("GET", "/api/orders/{id}", "O", "GetOrder", "id2", "f.cs", 15))
        index.add_route(RouteEntry("POST", "/api/orders", "O", "Create", "id3", "f.cs", 20))
        
        get_routes = index.find_routes_by_method("GET")
        assert len(get_routes) == 2
        assert all(r.http_method == "GET" for r in get_routes)
        
        post_routes = index.find_routes_by_method("POST")
        assert len(post_routes) == 1

    def test_find_routes_by_controller(self):
        """Test finding all routes in a controller."""
        index = RouteIndex()
        
        index.add_route(RouteEntry("GET", "/api/orders", "OrdersController", "GetOrders", "id1", "f.cs", 10))
        index.add_route(RouteEntry("POST", "/api/orders", "OrdersController", "Create", "id2", "f.cs", 15))
        index.add_route(RouteEntry("GET", "/api/users", "UsersController", "GetUsers", "id3", "f.cs", 20))
        
        order_routes = index.find_routes_by_controller("OrdersController")
        assert len(order_routes) == 2
        assert all(r.controller_class == "OrdersController" for r in order_routes)
        
        user_routes = index.find_routes_by_controller("UsersController")
        assert len(user_routes) == 1

    def test_find_route_by_symbol_id(self):
        """Test finding route by symbol ID."""
        index = RouteIndex()
        entry = RouteEntry(
            http_method="GET",
            path="/api/orders/{id}",
            controller_class="OrdersController",
            action_method="GetOrder",
            symbol_id="specific123",
            file_path="file.cs",
            start_line=15,
        )
        
        index.add_route(entry)
        
        found = index.find_route_by_symbol_id("specific123")
        assert found == entry

    def test_route_entry_serialization(self):
        """Test serializing route index to JSON."""
        index = RouteIndex()
        
        entry1 = RouteEntry("GET", "/api/orders", "OrdersController", "GetOrders", "id1", "file.cs", 10)
        entry2 = RouteEntry("POST", "/api/orders", "OrdersController", "Create", "id2", "file.cs", 20)
        
        index.add_route(entry1)
        index.add_route(entry2)
        
        json_str = index.to_json()
        assert "GET" in json_str
        assert "POST" in json_str
        assert "/api/orders" in json_str
        assert "OrdersController" in json_str

    def test_route_index_deserialization(self):
        """Test deserializing route index from JSON."""
        # Create and serialize index
        index1 = RouteIndex()
        index1.add_route(RouteEntry("GET", "/api/orders", "OrdersController", "GetOrders", "id1", "file.cs", 10))
        index1.add_route(RouteEntry("POST", "/api/orders", "OrdersController", "Create", "id2", "file.cs", 20))
        
        json_str = index1.to_json()
        
        # Deserialize
        index2 = RouteIndex.from_json(json_str)
        
        assert len(index2.all_routes()) == 2
        assert index2.find_route("GET", "/api/orders") is not None
        assert index2.find_route("POST", "/api/orders") is not None


class TestCSharpRouteExtraction:
    """Test extracting routes from C# ASP.NET Core code."""

    def test_extract_simple_get_route(self):
        """Test extracting a simple GET route."""
        source = """
using Microsoft.AspNetCore.Mvc;

[ApiController]
[Route("api/[controller]")]
public class OrdersController : ControllerBase
{
    [HttpGet]
    public IActionResult GetOrders()
    {
        return Ok();
    }
}
"""
        symbols = {"GetOrders": ("sym123", 10)}
        routes = extract_routes_from_csharp(source, symbols, "OrdersController.cs")
        
        assert len(routes) >= 1
        assert any(r.http_method == "GET" for r in routes)

    def test_extract_route_with_path(self):
        """Test extracting route with custom path."""
        source = """
[ApiController]
[Route("api/[controller]")]
public class OrdersController : ControllerBase
{
    [HttpGet("{id}")]
    public IActionResult GetOrder(int id)
    {
        return Ok();
    }
}
"""
        symbols = {"GetOrder": ("sym456", 8)}
        routes = extract_routes_from_csharp(source, symbols, "OrdersController.cs")
        
        # Should have extracted the route with {id} parameter
        assert any(r.action_method == "GetOrder" for r in routes)

    def test_extract_post_route(self):
        """Test extracting POST route."""
        source = """
[ApiController]
[Route("api/[controller]")]
public class OrdersController : ControllerBase
{
    [HttpPost]
    public IActionResult CreateOrder([FromBody] CreateOrderDto dto)
    {
        return CreatedAtAction(nameof(GetOrder), new { id = 1 }, result);
    }
}
"""
        symbols = {"CreateOrder": ("sym789", 8)}
        routes = extract_routes_from_csharp(source, symbols, "OrdersController.cs")
        
        assert any(r.http_method == "POST" and r.action_method == "CreateOrder" for r in routes)

    def test_extract_multiple_http_methods(self):
        """Test extracting routes with different HTTP methods."""
        source = """
[ApiController]
[Route("api/[controller]")]
public class OrdersController : ControllerBase
{
    [HttpGet]
    public IActionResult GetOrders()
    {
        return Ok();
    }

    [HttpPost]
    public IActionResult CreateOrder()
    {
        return Created();
    }

    [HttpPut("{id}")]
    public IActionResult UpdateOrder(int id)
    {
        return Ok();
    }

    [HttpDelete("{id}")]
    public IActionResult DeleteOrder(int id)
    {
        return Ok();
    }
}
"""
        symbols = {
            "GetOrders": ("id1", 8),
            "CreateOrder": ("id2", 14),
            "UpdateOrder": ("id3", 20),
            "DeleteOrder": ("id4", 26),
        }
        routes = extract_routes_from_csharp(source, symbols, "OrdersController.cs")
        
        methods = {r.http_method for r in routes}
        assert "GET" in methods
        assert "POST" in methods
        assert "PUT" in methods
        assert "DELETE" in methods

    def test_extract_async_routes(self):
        """Test extracting async route handlers."""
        source = """
[ApiController]
[Route("api/[controller]")]
public class OrdersController : ControllerBase
{
    [HttpGet("{id}")]
    public async Task<IActionResult> GetOrderAsync(int id)
    {
        return Ok();
    }
}
"""
        symbols = {"GetOrderAsync": ("sym999", 8)}
        routes = extract_routes_from_csharp(source, symbols, "OrdersController.cs")
        
        assert any(r.action_method == "GetOrderAsync" for r in routes)

    def test_extract_route_with_authorize_attribute(self):
        """Test extracting route with [Authorize] attribute."""
        source = """
[ApiController]
[Route("api/[controller]")]
public class OrdersController : ControllerBase
{
    [Authorize]
    [HttpGet("{id}")]
    public IActionResult GetOrder(int id)
    {
        return Ok();
    }
}
"""
        symbols = {"GetOrder": ("sym555", 9)}
        routes = extract_routes_from_csharp(source, symbols, "OrdersController.cs")
        
        # Should still extract the route even with [Authorize]
        assert any(r.action_method == "GetOrder" for r in routes)

    def test_extract_multiple_controllers(self):
        """Test extracting routes from multiple controllers in one file."""
        source = """
[ApiController]
[Route("api/[controller]")]
public class OrdersController : ControllerBase
{
    [HttpGet]
    public IActionResult GetOrders()
    {
        return Ok();
    }
}

[ApiController]
[Route("api/[controller]")]
public class UsersController : ControllerBase
{
    [HttpGet]
    public IActionResult GetUsers()
    {
        return Ok();
    }
}
"""
        symbols = {
            "GetOrders": ("id1", 8),
            "GetUsers": ("id2", 18),
        }
        routes = extract_routes_from_csharp(source, symbols, "mixed.cs")
        
        # Should extract routes from both controllers
        assert len(routes) >= 2
        assert any(r.controller_class == "OrdersController" for r in routes)
        assert any(r.controller_class == "UsersController" for r in routes)

    def test_extract_route_with_custom_path(self):
        """Test extracting route with custom absolute path."""
        source = """
[ApiController]
[Route("api/v1/orders")]
public class OrdersController : ControllerBase
{
    [HttpGet]
    public IActionResult GetOrders()
    {
        return Ok();
    }

    [HttpGet("{id}")]
    public IActionResult GetOrder(int id)
    {
        return Ok();
    }
}
"""
        symbols = {
            "GetOrders": ("id1", 8),
            "GetOrder": ("id2", 13),
        }
        routes = extract_routes_from_csharp(source, symbols, "OrdersController.cs")
        
        # Should use custom route instead of [controller]
        assert len(routes) >= 2


class TestRouteIndexIntegration:
    """Test route index integration with symbol metadata."""

    def test_index_with_full_workflow(self):
        """Test complete route indexing workflow."""
        index = RouteIndex()
        
        # Simulate extracting routes from a controller
        source = """
[ApiController]
[Route("api/orders")]
public class OrdersController
{
    [HttpGet]
    public IActionResult List() { }
    
    [HttpGet("{id}")]
    public IActionResult GetById(int id) { }
    
    [HttpPost]
    public IActionResult Create() { }
}
"""
        
        symbols = {
            "List": ("list_id", 7),
            "GetById": ("get_id", 10),
            "Create": ("create_id", 13),
        }
        
        routes = extract_routes_from_csharp(source, symbols, "OrdersController.cs")
        for route in routes:
            index.add_route(route)
        
        # Query the index
        assert len(index.find_routes_by_controller("OrdersController")) >= 3
        assert index.find_route_by_symbol_id("list_id") is not None
        
        # Serialize and deserialize
        json_str = index.to_json()
        index2 = RouteIndex.from_json(json_str)
        
        assert len(index2.all_routes()) == len(index.all_routes())
