"""Test Phase 2a: TypeScript symbol extraction."""

import pytest
from agent.typescript_extractor import TypeScriptExtractor
from agent.symbol_extractor import SymbolRecord


@pytest.fixture
def extractor():
    """Create a TypeScript extractor."""
    return TypeScriptExtractor()


def test_extract_function_declaration(extractor):
    """Extract top-level function."""
    source = """function greet(name: string): string {
    return `Hello, ${name}!`;
}"""
    symbols = extractor.extract("test.ts", source, "hash123")
    
    assert len(symbols) > 0
    func_syms = [s for s in symbols if s.name == "greet"]
    assert len(func_syms) == 1
    assert func_syms[0].kind == "function"
    assert func_syms[0].file_path == "test.ts"


def test_extract_arrow_function(extractor):
    """Extract arrow function assigned to const."""
    source = """const handleClick = (e: React.MouseEvent) => {
    console.log(e);
};"""
    symbols = extractor.extract("test.ts", source, "hash123")
    
    func_syms = [s for s in symbols if s.name == "handleClick"]
    assert len(func_syms) >= 1
    assert func_syms[0].kind == "function"


def test_extract_class_and_methods(extractor):
    """Extract class and its methods."""
    source = """class OrderService {
    getOrder(id: number) {
        return { id, status: 'pending' };
    }
    
    async createOrder(order: any) {
        return order;
    }
}"""
    symbols = extractor.extract("test.ts", source, "hash123")
    
    # Should have class and methods
    class_syms = [s for s in symbols if s.name == "OrderService" and s.kind == "class"]
    assert len(class_syms) >= 1
    
    method_syms = [s for s in symbols if "OrderService" in s.name and s.kind == "method"]
    assert len(method_syms) >= 1


def test_extract_javascript_class_name(extractor):
    """Extract class symbols from JavaScript parser output using identifier nodes."""
    source = """class CheckoutService {
    submitOrder() {
        return true;
    }
}"""
    symbols = extractor.extract("checkout.js", source, "hash123")

    class_syms = [s for s in symbols if s.name == "CheckoutService" and s.kind == "class"]
    assert len(class_syms) == 1


def test_extractor_has_protocol_attributes(extractor):
    """TypeScript extractor implements LanguageExtractor protocol."""
    assert extractor.extensions == (".ts", ".tsx", ".js", ".jsx")
    assert extractor.language == "typescript"
    assert hasattr(extractor, "extract")
    assert hasattr(extractor, "extract_relationships")


def test_extract_empty_file(extractor):
    """Empty file returns empty symbol list."""
    symbols = extractor.extract("test.ts", "", "hash123")
    assert symbols == []


def test_extract_invalid_syntax(extractor):
    """Invalid syntax returns empty list (no exception)."""
    source = "function { invalid syntax here"
    symbols = extractor.extract("test.ts", source, "hash123")
    # Should not raise; may return empty or partial symbols
    assert isinstance(symbols, list)


def test_extract_relationships_empty(extractor):
    """extract_relationships returns empty list for file without imports."""
    source = """function greet(name: string) {
    return `Hello, ${name}!`;
}"""
    symbols = extractor.extract("test.ts", source, "hash123")
    rels = extractor.extract_relationships("test.ts", source, symbols, "/workspace")

    # Phase 2a returns empty; Phase 2b will populate
    assert isinstance(rels, list)


def test_extract_calls_plain_function_call_same_file(extractor):
    """A plain identifier call to a same-file function resolves immediately."""
    source = """function chargeCustomer(order) {
    return true;
}

function validatePayment(order) {
    chargeCustomer(order);
    return true;
}"""
    symbols = extractor.extract("orders.ts", source, "hash123")
    rels = extractor.extract_relationships("orders.ts", source, symbols, "/workspace")

    calls = [r for r in rels if r.relation_type == "calls"]
    assert any(
        r.to_symbol_name == "chargeCustomer" and r.to_file_path == "orders.ts" and r.to_symbol_id
        for r in calls
    )


def test_extract_calls_this_member_call_resolves_to_qualified_method(extractor):
    """this.method() resolves against the qualified "ClassName.method" symbol."""
    source = """class OrderService {
    process(order) {
        this.logEvent(order);
    }

    logEvent(order) {
        console.log(order);
    }
}"""
    symbols = extractor.extract("service.ts", source, "hash123")
    rels = extractor.extract_relationships("service.ts", source, symbols, "/workspace")

    log_event_symbol = next(s for s in symbols if s.name == "OrderService.logEvent")
    calls = [r for r in rels if r.relation_type == "calls" and r.to_symbol_name == "logEvent"]
    assert len(calls) == 1
    assert calls[0].to_symbol_id == log_event_symbol.id
    assert calls[0].to_file_path == "service.ts"


def test_extract_calls_unresolvable_call_left_unresolved(extractor):
    """A call with no matching symbol or import (e.g. a builtin) stays unresolved, not dropped."""
    source = """function logDebug(message) {
    console.log(message);
}"""
    symbols = extractor.extract("utils.ts", source, "hash123")
    rels = extractor.extract_relationships("utils.ts", source, symbols, "/workspace")

    calls = [r for r in rels if r.relation_type == "calls" and r.to_symbol_name == "log"]
    assert len(calls) == 1
    assert calls[0].to_symbol_id is None
    assert calls[0].to_file_path is None


def test_extract_calls_attributes_call_to_enclosing_function(extractor):
    """A call inside a top-level function is attributed to that function, not module scope."""
    source = """function outer(order) {
    inner(order);
}

function inner(order) {
    return order;
}"""
    symbols = extractor.extract("a.ts", source, "hash123")
    rels = extractor.extract_relationships("a.ts", source, symbols, "/workspace")

    outer_symbol = next(s for s in symbols if s.name == "outer")
    calls = [r for r in rels if r.relation_type == "calls" and r.to_symbol_name == "inner"]
    assert len(calls) == 1
    assert calls[0].from_symbol_id == outer_symbol.id
