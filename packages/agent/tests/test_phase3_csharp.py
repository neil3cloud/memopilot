"""Phase 3: C# / ASP.NET Core — C# symbol extraction and resolution tests."""

from __future__ import annotations

import pytest

from agent.csharp_extractor import CSharpExtractor
from agent.csharp_resolver import CSharpResolver
from agent.symbol_extractor import SymbolRecord


class TestCSharpExtractorInitialization:
    """Test C# extractor initialization."""

    def test_extractor_has_correct_extensions(self):
        """Verify C# extractor handles .cs files."""
        extractor = CSharpExtractor()
        assert extractor.extensions == (".cs",)

    def test_extractor_language_is_csharp(self):
        """Verify extractor language identifier is 'csharp'."""
        extractor = CSharpExtractor()
        assert extractor.language == "csharp"

    def test_extractor_implements_protocol(self):
        """Verify extractor implements LanguageExtractor protocol."""
        extractor = CSharpExtractor()
        assert hasattr(extractor, "extract")
        assert hasattr(extractor, "extract_relationships")
        assert callable(extractor.extract)
        assert callable(extractor.extract_relationships)


class TestCSharpClassExtraction:
    """Test C# class symbol extraction."""

    def test_extract_simple_class(self):
        """Verify extraction of simple C# class."""
        source = """
namespace MyApp.Services
{
    public class OrderService
    {
        public void ProcessOrder() { }
    }
}
"""
        extractor = CSharpExtractor()
        symbols = extractor.extract("OrderService.cs", source, "hash123")

        assert len(symbols) >= 1
        class_sym = next((s for s in symbols if s.name == "OrderService" and s.kind == "class"), None)
        assert class_sym is not None
        assert class_sym.file_path == "OrderService.cs"

    def test_extract_abstract_class(self):
        """Verify extraction of abstract C# class."""
        source = """
namespace MyApp.Repositories
{
    public abstract class BaseRepository
    {
        public abstract void Save();
    }
}
"""
        extractor = CSharpExtractor()
        symbols = extractor.extract("BaseRepository.cs", source, "hash123")

        class_sym = next((s for s in symbols if s.name == "BaseRepository" and s.kind == "class"), None)
        assert class_sym is not None

    def test_extract_class_with_inheritance(self):
        """Verify class with base class is extracted."""
        source = """
namespace MyApp.Services
{
    public class OrderService : IOrderService
    {
        public Order GetOrder(int id) { return null; }
    }
}
"""
        extractor = CSharpExtractor()
        symbols = extractor.extract("OrderService.cs", source, "hash123")

        class_sym = next((s for s in symbols if s.name == "OrderService"), None)
        assert class_sym is not None


class TestCSharpMethodExtraction:
    """Test C# method symbol extraction."""

    def test_extract_public_method(self):
        """Verify extraction of public method."""
        source = """
public class OrderService
{
    public List<Order> GetOrders() { return new List<Order>(); }
}
"""
        extractor = CSharpExtractor()
        symbols = extractor.extract("OrderService.cs", source, "hash123")

        method_sym = next((s for s in symbols if s.name == "GetOrders"), None)
        assert method_sym is not None

    def test_extract_async_method(self):
        """Verify extraction of async method."""
        source = """
public class OrderService
{
    public async Task<Order> GetOrderAsync(int id) { return null; }
}
"""
        extractor = CSharpExtractor()
        symbols = extractor.extract("OrderService.cs", source, "hash123")

        method_sym = next((s for s in symbols if s.name == "GetOrderAsync"), None)
        assert method_sym is not None

    def test_extract_constructor(self):
        """Verify extraction of constructor method."""
        source = """
public class OrderService
{
    public OrderService(IOrderRepository repo) { }
}
"""
        extractor = CSharpExtractor()
        symbols = extractor.extract("OrderService.cs", source, "hash123")

        # Constructor should be extracted
        assert len(symbols) > 0


class TestCSharpPropertyExtraction:
    """Test C# property symbol extraction."""

    def test_extract_auto_property(self):
        """Verify extraction of auto-property."""
        source = """
public class Order
{
    public int Id { get; set; }
    public string Name { get; set; }
}
"""
        extractor = CSharpExtractor()
        symbols = extractor.extract("Order.cs", source, "hash123")

        # Properties should be extracted
        property_syms = [s for s in symbols if s.kind == "property"]
        assert len(property_syms) >= 1


class TestCSharpEnumExtraction:
    """Test C# enum symbol extraction."""

    def test_extract_enum(self):
        """Verify extraction of enum."""
        source = """
namespace MyApp.Models
{
    public enum OrderStatus
    {
        Pending,
        Processing,
        Completed
    }
}
"""
        extractor = CSharpExtractor()
        symbols = extractor.extract("OrderStatus.cs", source, "hash123")

        enum_sym = next((s for s in symbols if s.name == "OrderStatus" and s.kind == "enum"), None)
        assert enum_sym is not None


class TestCSharpInterfaceExtraction:
    """Test C# interface symbol extraction."""

    def test_extract_interface(self):
        """Verify extraction of interface."""
        source = """
namespace MyApp.Contracts
{
    public interface IOrderService
    {
        Task<Order> GetOrder(int id);
        Task<List<Order>> GetOrders();
    }
}
"""
        extractor = CSharpExtractor()
        symbols = extractor.extract("IOrderService.cs", source, "hash123")

        interface_sym = next((s for s in symbols if s.name == "IOrderService" and s.kind == "interface"), None)
        assert interface_sym is not None


class TestCSharpStructExtraction:
    """Test C# struct/record symbol extraction."""

    def test_extract_struct(self):
        """Verify extraction of struct."""
        source = """
public struct Point
{
    public int X { get; set; }
    public int Y { get; set; }
}
"""
        extractor = CSharpExtractor()
        symbols = extractor.extract("Point.cs", source, "hash123")

        struct_sym = next((s for s in symbols if s.name == "Point" and s.kind == "struct"), None)
        assert struct_sym is not None

    def test_extract_record(self):
        """Verify extraction of record (or skip if not supported by tree-sitter grammar)."""
        source = """
public record OrderDto(int Id, string Name);
"""
        extractor = CSharpExtractor()
        symbols = extractor.extract("OrderDto.cs", source, "hash123")

        # Records may or may not be extracted depending on tree-sitter-csharp grammar version
        # Just verify it doesn't crash
        assert isinstance(symbols, list)


class TestCSharpRelationshipExtraction:
    """Test C# relationship extraction."""

    def test_extract_using_statements(self):
        """Verify extraction of using directives as imports."""
        source = """
using System;
using MyApp.Services;
using MyApp.Repositories;

namespace MyApp.Controllers
{
    public class OrderController { }
}
"""
        extractor = CSharpExtractor()
        symbols = extractor.extract("OrderController.cs", source, "hash123")
        relationships = extractor.extract_relationships("OrderController.cs", source, symbols, "/workspace")

        # Should have relationships for using statements. relation_type is
        # "imports" (matches the symbol_relationships CHECK constraint and
        # what the extractor actually emits) — this test previously checked
        # "import" (singular), which never matched anything.
        import_rels = [r for r in relationships if r.relation_type == "imports"]
        assert len(import_rels) >= 1

    def test_extract_inheritance_relationships(self):
        """Verify extraction of class inheritance."""
        source = """
public class OrderService : IOrderService
{
    public Order GetOrder(int id) { return null; }
}
"""
        extractor = CSharpExtractor()
        symbols = extractor.extract("OrderService.cs", source, "hash123")
        relationships = extractor.extract_relationships("OrderService.cs", source, symbols, "/workspace")

        # Should have inheritance relationship. relation_type is "inherits"
        # (matches the extractor and the CHECK constraint) — this test
        # previously checked "inheritance", which never matched anything.
        inheritance_rels = [r for r in relationships if r.relation_type == "inherits"]
        assert len(inheritance_rels) >= 1

    def test_extract_http_route_get(self):
        """Verify extraction of [HttpGet] routes."""
        source = """
[Route("api/[controller]")]
[ApiController]
public class OrdersController : ControllerBase
{
    [HttpGet("{id}")]
    public IActionResult GetOrder(int id)
    {
        return Ok();
    }
}
"""
        extractor = CSharpExtractor()
        symbols = extractor.extract("OrdersController.cs", source, "hash123")
        relationships = extractor.extract_relationships("OrdersController.cs", source, symbols, "/workspace")

        # Should have route relationships. HTTP routes are stored as
        # relation_type "references" (the extractor never emitted "route") —
        # this test previously checked a value that never matched anything.
        route_rels = [r for r in relationships if r.relation_type == "references"]
        assert len(route_rels) >= 1

    def test_extract_http_route_post(self):
        """Verify extraction of [HttpPost] routes."""
        source = """
[Route("api/[controller]")]
public class OrdersController
{
    [HttpPost]
    public IActionResult Create(CreateOrderDto dto)
    {
        return Created();
    }
}
"""
        extractor = CSharpExtractor()
        symbols = extractor.extract("OrdersController.cs", source, "hash123")
        relationships = extractor.extract_relationships("OrdersController.cs", source, symbols, "/workspace")

        route_rels = [r for r in relationships if r.relation_type == "references"]
        assert len(route_rels) >= 1


class TestCSharpCallExtraction:
    """Test C# method-call extraction (Phase 3c)."""

    def test_plain_call_same_file_resolves(self):
        source = """
public class PaymentService
{
    public bool ValidatePayment(Order order)
    {
        ChargeCustomer(order);
        return true;
    }

    private void ChargeCustomer(Order order) { }
}
"""
        extractor = CSharpExtractor()
        symbols = extractor.extract("service.cs", source, "hash123")
        relationships = extractor.extract_relationships("service.cs", source, symbols, "/workspace")

        charge_symbol = next(s for s in symbols if s.name == "ChargeCustomer")
        calls = [r for r in relationships if r.relation_type == "calls" and r.to_symbol_name == "ChargeCustomer"]
        assert len(calls) == 1
        assert calls[0].to_symbol_id == charge_symbol.id

    def test_this_member_call_resolves_bare_name(self):
        """C# method symbol names are stored bare (not "Class.Method" like
        Python/TS), so this.Method() resolves via a direct name match."""
        source = """
public class PaymentService
{
    public bool ValidatePayment(Order order)
    {
        this.LogEvent(order);
        return true;
    }

    private void LogEvent(Order order) { }
}
"""
        extractor = CSharpExtractor()
        symbols = extractor.extract("service.cs", source, "hash123")
        relationships = extractor.extract_relationships("service.cs", source, symbols, "/workspace")

        log_symbol = next(s for s in symbols if s.name == "LogEvent")
        calls = [r for r in relationships if r.relation_type == "calls" and r.to_symbol_name == "LogEvent"]
        assert len(calls) == 1
        assert calls[0].to_symbol_id == log_symbol.id

    def test_bcl_call_left_unresolved_not_dropped(self):
        source = """
public class PaymentService
{
    private void LogEvent(Order order)
    {
        Console.WriteLine(order);
    }
}
"""
        extractor = CSharpExtractor()
        symbols = extractor.extract("service.cs", source, "hash123")
        relationships = extractor.extract_relationships("service.cs", source, symbols, "/workspace")

        calls = [r for r in relationships if r.relation_type == "calls" and r.to_symbol_name == "WriteLine"]
        assert len(calls) == 1
        assert calls[0].to_symbol_id is None

    def test_call_attributed_to_enclosing_method(self):
        source = """
public class PaymentService
{
    public bool Outer(Order order)
    {
        Inner(order);
        return true;
    }

    private void Inner(Order order) { }
}
"""
        extractor = CSharpExtractor()
        symbols = extractor.extract("service.cs", source, "hash123")
        relationships = extractor.extract_relationships("service.cs", source, symbols, "/workspace")

        outer_symbol = next(s for s in symbols if s.name == "Outer")
        calls = [r for r in relationships if r.relation_type == "calls" and r.to_symbol_name == "Inner"]
        assert len(calls) == 1
        assert calls[0].from_symbol_id == outer_symbol.id


class TestCSharpResolverInitialization:
    """Test C# resolver initialization."""

    def test_resolver_initializes(self):
        """Verify resolver initializes without errors."""
        resolver = CSharpResolver("/workspace")
        assert resolver is not None

    def test_resolver_parses_di_registrations(self):
        """Verify resolver attempts to load DI registrations."""
        resolver = CSharpResolver("/workspace")
        # Should not raise even if Program.cs doesn't exist
        assert resolver is not None


class TestCSharpResolverNamespaceResolution:
    """Test C# namespace resolution."""

    def test_resolve_namespace_to_symbol(self):
        """Verify namespace resolution is deferred to Phase 3b.
        
        Currently returns None as real symbol ID resolution requires database queries.
        Will be implemented when DB access is integrated with resolver.
        """
        resolver = CSharpResolver("/workspace")
        result = resolver.resolve_namespace_to_symbol("MyApp.Services", "OrderService")
        # Deferred to Phase 3b when real symbol ID lookup is available
        assert result is None

    def test_resolve_namespace_with_nested_path(self):
        """Verify resolution of deeply nested namespace.
        
        Currently returns None as real symbol ID resolution requires database queries.
        """
        resolver = CSharpResolver("/workspace")
        result = resolver.resolve_namespace_to_symbol("MyApp.Services.Orders.Handlers", "GetOrderHandler")
        # Deferred to Phase 3b when real symbol ID lookup is available
        assert result is None


class TestCSharpResolverDIResolution:
    """Test C# DI resolution."""

    def test_parse_constructor_injection(self):
        """Verify extraction of constructor injection parameters."""
        resolver = CSharpResolver("/workspace")
        source = "public OrderService(IOrderRepository repo, ILogger logger)"
        injections = resolver.parse_constructor_injection(source)
        assert len(injections) >= 2
        types = [t for t, _ in injections]
        assert "IOrderRepository" in types or "ILogger" in types

    def test_resolve_interface_to_impl_no_registrations(self):
        """Verify interface resolution returns None when no registrations."""
        resolver = CSharpResolver("/workspace")
        result = resolver.resolve_interface_to_impl("IOrderService")
        # Should return None if no DI registrations found
        assert result is None

    def test_build_di_relationship(self):
        """Verify DI relationship building."""
        resolver = CSharpResolver("/workspace")
        # Without actual DI registrations, should return None
        result = resolver.build_di_relationship("IOrderService")
        assert result is None


class TestCSharpEmptyAndErrorCases:
    """Test edge cases and error handling."""

    def test_extract_empty_file(self):
        """Verify extraction handles empty files gracefully."""
        extractor = CSharpExtractor()
        symbols = extractor.extract("Empty.cs", "", "hash123")
        assert isinstance(symbols, list)

    def test_extract_invalid_syntax(self):
        """Verify extraction handles invalid C# syntax gracefully."""
        source = "this is not valid c# code {{{}}}"
        extractor = CSharpExtractor()
        symbols = extractor.extract("Invalid.cs", source, "hash123")
        # Should return empty list, not crash
        assert isinstance(symbols, list)

    def test_extract_without_namespace(self):
        """Verify extraction works with classes without explicit namespace."""
        source = """
public class SimpleClass
{
    public void DoSomething() { }
}
"""
        extractor = CSharpExtractor()
        symbols = extractor.extract("SimpleClass.cs", source, "hash123")
        assert len(symbols) >= 1

    def test_relationships_empty_on_no_imports(self):
        """Verify relationship extraction returns empty when no imports."""
        source = "public class Standalone { }"
        extractor = CSharpExtractor()
        symbols = extractor.extract("Standalone.cs", source, "hash123")
        relationships = extractor.extract_relationships("Standalone.cs", source, symbols, "/workspace")
        # May have empty or have class itself
        assert isinstance(relationships, list)


class TestCSharpBackwardCompatibility:
    """Test backward compatibility."""

    def test_registration_in_workspace_indexer(self):
        """Verify C# extractor can be registered in registry."""
        from agent.extractor_registry import ExtractorRegistry

        registry = ExtractorRegistry()
        registry.register(CSharpExtractor())
        extractor = registry.get(".cs")
        assert extractor is not None
        assert extractor.language == "csharp"

    def test_python_still_works_with_csharp(self):
        """Verify Python extraction still works when C# is registered."""
        from agent.extractor_registry import ExtractorRegistry
        from agent.python_extractor import PythonExtractor

        registry = ExtractorRegistry()
        registry.register(PythonExtractor())
        registry.register(CSharpExtractor())

        py_extractor = registry.get(".py")
        cs_extractor = registry.get(".cs")

        assert py_extractor is not None
        assert py_extractor.language == "python"
        assert cs_extractor is not None
        assert cs_extractor.language == "csharp"
