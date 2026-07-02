"""Tests for Phase 3b: C# DI resolution with database integration."""

import pytest
import aiosqlite
from pathlib import Path
from tempfile import TemporaryDirectory

from agent.csharp_resolver import CSharpResolver
from agent.symbol_extractor import SymbolRecord
from agent.graph_retriever import SymbolRelationshipRecord, make_relationship_id


class TestCSharpResolverDatabaseIntegration:
    """Test database-backed symbol resolution."""

    @pytest.fixture
    async def db_connection(self):
        """Create an in-memory SQLite database for testing."""
        async with aiosqlite.connect(":memory:") as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            
            # Create symbols table
            await conn.execute("""
                CREATE TABLE symbols (
                    id TEXT PRIMARY KEY,
                    file_path TEXT NOT NULL,
                    language TEXT NOT NULL,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER,
                    docstring TEXT,
                    tags TEXT,
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP
                )
            """)
            
            # Create symbol_relationships table
            await conn.execute("""
                CREATE TABLE symbol_relationships (
                    id TEXT PRIMARY KEY,
                    from_symbol_id TEXT NOT NULL,
                    to_symbol_id TEXT,
                    to_symbol_name TEXT,
                    to_file_path TEXT,
                    relation_type TEXT NOT NULL,
                    workspace_root TEXT NOT NULL,
                    FOREIGN KEY (from_symbol_id) REFERENCES symbols(id)
                )
            """)
            
            await conn.commit()
            yield conn

    async def test_resolve_namespace_to_symbol_with_database(self, db_connection: aiosqlite.Connection):
        """Test resolving a symbol by namespace and name from database."""
        # Insert test symbols
        await db_connection.execute(
            """
            INSERT INTO symbols (id, file_path, language, name, kind, start_line, end_line)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("abc123def456", "/workspace/Services/OrderService.cs", "csharp", "OrderService", "class", 10, 50),
        )
        await db_connection.commit()
        
        resolver = CSharpResolver("/workspace")
        
        # Resolve the symbol
        symbol_id = await resolver.resolve_namespace_to_symbol_async(
            "MyApp.Services",
            "OrderService",
            db_connection,
        )
        
        assert symbol_id == "abc123def456"
    
    async def test_resolve_namespace_not_found(self, db_connection: aiosqlite.Connection):
        """Test resolution when symbol doesn't exist."""
        resolver = CSharpResolver("/workspace")
        
        symbol_id = await resolver.resolve_namespace_to_symbol_async(
            "MyApp.Services",
            "NonExistentService",
            db_connection,
        )
        
        assert symbol_id is None
    
    async def test_resolve_interface_to_symbol_via_di(self, db_connection: aiosqlite.Connection):
        """Test resolving interface to implementation via DI map."""
        # Insert concrete implementation
        await db_connection.execute(
            """
            INSERT INTO symbols (id, file_path, language, name, kind, start_line, end_line)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("impl123", "/workspace/Services/OrderService.cs", "csharp", "OrderService", "class", 10, 50),
        )
        await db_connection.commit()
        
        # Create resolver with DI mapping
        with TemporaryDirectory() as tmpdir:
            program_cs = Path(tmpdir) / "Program.cs"
            program_cs.write_text("""
var services = new ServiceCollection();
services.AddScoped<IOrderService, OrderService>();
services.AddTransient<IOrderRepository, OrderRepository>();
""")
            
            resolver = CSharpResolver(tmpdir)
            
            # Resolve interface to implementation symbol
            symbol_id = await resolver.resolve_interface_to_symbol_async(
                "IOrderService",
                db_connection,
            )
            
            assert symbol_id == "impl123"
    
    async def test_di_map_parsing_multiple_registrations(self):
        """Test parsing multiple DI registrations."""
        with TemporaryDirectory() as tmpdir:
            program_cs = Path(tmpdir) / "Program.cs"
            program_cs.write_text("""
var services = new ServiceCollection();
services.AddScoped<IOrderService, OrderService>();
services.AddTransient<IOrderRepository, OrderRepository>();
services.AddSingleton<IConfigService, ConfigService>();
services.AddScoped<ILoggerService, LoggerService>();
""")
            
            resolver = CSharpResolver(tmpdir)
            
            assert resolver.resolve_interface_to_impl("IOrderService") == "OrderService"
            assert resolver.resolve_interface_to_impl("IOrderRepository") == "OrderRepository"
            assert resolver.resolve_interface_to_impl("IConfigService") == "ConfigService"
            assert resolver.resolve_interface_to_impl("ILoggerService") == "LoggerService"
    
    async def test_extract_namespace_from_source(self):
        """Test extracting namespace declaration from C# source."""
        resolver = CSharpResolver("/workspace")
        
        source = """
using System;
namespace MyApp.Services.Orders
{
    public class OrderService
    {
        public void GetOrder() { }
    }
}
"""
        namespace = resolver.extract_namespace_from_source(source)
        assert namespace == "MyApp.Services.Orders"
    
    async def test_extract_namespace_with_brace_syntax(self):
        """Test namespace extraction with file-scoped syntax (C# 10+)."""
        resolver = CSharpResolver("/workspace")
        
        source = """
using System;
namespace MyApp.Services;

public class OrderService
{
    public void GetOrder() { }
}
"""
        namespace = resolver.extract_namespace_from_source(source)
        assert namespace == "MyApp.Services"
    
    async def test_extract_namespace_not_found(self):
        """Test when no namespace is declared."""
        resolver = CSharpResolver("/workspace")
        
        source = """
using System;

public class GlobalService
{
    public void GetOrder() { }
}
"""
        namespace = resolver.extract_namespace_from_source(source)
        assert namespace is None
    
    async def test_backfill_import_relationships(self, db_connection: aiosqlite.Connection):
        """Test backfilling to_symbol_id for import relationships."""
        # Insert target symbol
        await db_connection.execute(
            """
            INSERT INTO symbols (id, file_path, language, name, kind, start_line, end_line)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("sys123", "/workspace/System.cs", "csharp", "System", "namespace", 1, 1),
        )
        await db_connection.commit()
        
        resolver = CSharpResolver("/workspace")
        
        # Create relationship with to_symbol_id=None. relation_type is
        # "imports" — matches what csharp_extractor.py actually emits and
        # the symbol_relationships CHECK constraint; backfill_relationship_symbols
        # previously checked "import" (singular), which never matched.
        rel = SymbolRelationshipRecord(
            id="rel1",
            from_symbol_id="source123",
            to_symbol_id=None,
            to_symbol_name="System",
            to_file_path=None,
            relation_type="imports",
            workspace_root="/workspace",
        )
        
        # Backfill
        updated = await resolver.backfill_relationship_symbols(
            [rel],
            db_connection,
            file_namespace="MyApp.Services",
        )
        
        assert len(updated) == 1
        assert updated[0].to_symbol_id == "sys123"
    
    async def test_backfill_inheritance_relationships(self, db_connection: aiosqlite.Connection):
        """Test backfilling inheritance relationships."""
        # Insert base class
        await db_connection.execute(
            """
            INSERT INTO symbols (id, file_path, language, name, kind, start_line, end_line)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("base123", "/workspace/BaseRepo.cs", "csharp", "BaseRepository", "class", 5, 30),
        )
        await db_connection.commit()
        
        resolver = CSharpResolver("/workspace")
        
        # Create inheritance relationship. relation_type is "inherits"
        # (matches the extractor and the CHECK constraint) — this test
        # previously used "inheritance", which never matched.
        rel = SymbolRelationshipRecord(
            id="rel2",
            from_symbol_id="derived123",
            to_symbol_id=None,
            to_symbol_name="BaseRepository",
            to_file_path=None,
            relation_type="inherits",
            workspace_root="/workspace",
        )
        
        # Backfill
        updated = await resolver.backfill_relationship_symbols(
            [rel],
            db_connection,
            file_namespace="MyApp.Repositories",
        )
        
        assert len(updated) == 1
        assert updated[0].to_symbol_id == "base123"
    
    async def test_backfill_di_injection_relationships(self, db_connection: aiosqlite.Connection):
        """Test backfilling DI injection relationships."""
        # Insert implementation class
        await db_connection.execute(
            """
            INSERT INTO symbols (id, file_path, language, name, kind, start_line, end_line)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("impl456", "/workspace/OrderService.cs", "csharp", "OrderService", "class", 8, 60),
        )
        await db_connection.commit()
        
        with TemporaryDirectory() as tmpdir:
            program_cs = Path(tmpdir) / "Program.cs"
            program_cs.write_text("""
services.AddScoped<IOrderService, OrderService>();
""")
            
            resolver = CSharpResolver(tmpdir)
            
            # Create DI injection relationship
            rel = SymbolRelationshipRecord(
                id="rel3",
                from_symbol_id="constructor123",
                to_symbol_id=None,
                to_symbol_name="IOrderService",
                to_file_path=None,
                relation_type="di_injection",
                workspace_root=tmpdir,
            )
            
            # Backfill
            updated = await resolver.backfill_relationship_symbols(
                [rel],
                db_connection,
                file_namespace="MyApp.Controllers",
            )
            
            assert len(updated) == 1
            assert updated[0].to_symbol_id == "impl456"
    
    async def test_resolver_caching(self, db_connection: aiosqlite.Connection):
        """Test that resolved symbols are cached."""
        await db_connection.execute(
            """
            INSERT INTO symbols (id, file_path, language, name, kind, start_line, end_line)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("cached123", "/workspace/Service.cs", "csharp", "MyService", "class", 10, 30),
        )
        await db_connection.commit()
        
        resolver = CSharpResolver("/workspace")
        
        # First lookup
        id1 = await resolver.resolve_namespace_to_symbol_async(
            "MyApp",
            "MyService",
            db_connection,
        )
        
        # Second lookup (should hit cache)
        id2 = await resolver.resolve_namespace_to_symbol_async(
            "MyApp",
            "MyService",
            db_connection,
        )
        
        assert id1 == id2 == "cached123"
        # Verify cache is populated
        assert resolver._namespace_cache.get("MyApp::MyService") == "cached123"


class TestCSharpResolverBackwardCompatibility:
    """Ensure Phase 3b doesn't break existing functionality."""
    
    def test_resolver_initializes_without_query_function(self):
        """Test resolver can be created without database query function."""
        with TemporaryDirectory() as tmpdir:
            resolver = CSharpResolver(tmpdir)
            assert resolver._query_symbol_by_name is None
    
    def test_sync_namespace_resolution_uses_cache(self):
        """Test synchronous resolution falls back to cache."""
        with TemporaryDirectory() as tmpdir:
            resolver = CSharpResolver(tmpdir)
            
            # Pre-populate cache
            resolver._namespace_cache["Test::Service"] = "test123"
            
            # Sync resolution should use cache
            result = resolver.resolve_namespace_to_symbol("Test", "Service")
            assert result == "test123"
    
    def test_di_map_still_populated_from_program_cs(self):
        """Test that DI map is loaded even without async resolution."""
        with TemporaryDirectory() as tmpdir:
            program_cs = Path(tmpdir) / "Program.cs"
            program_cs.write_text("""
services.AddScoped<IOrderService, OrderService>();
""")
            
            resolver = CSharpResolver(tmpdir)
            
            # Should still resolve interface to impl name
            impl = resolver.resolve_interface_to_impl("IOrderService")
            assert impl == "OrderService"
