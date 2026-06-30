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
