"""Shared test fixtures for MemoPilot agent tests."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from agent.api import app, configure
from agent.config import Config
from agent.db import DatabaseManager


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory."""
    workspace = tmp_path / "test-workspace"
    workspace.mkdir()
    return workspace


@pytest.fixture
def test_config(tmp_workspace: Path) -> Config:
    """Create a test configuration with temporary paths."""
    return Config(
        workspace_path=tmp_workspace,
        memopilot_dir=tmp_workspace / ".memopilot",
        global_dir=tmp_workspace / ".memopilot-global",
    )


@pytest_asyncio.fixture
async def test_db(test_config: Config) -> DatabaseManager:
    """Create an in-memory database manager for tests."""
    # Use in-memory SQLite for speed
    db = DatabaseManager(Path(":memory:"))
    await db.connect()
    yield db
    await db.close()


@pytest.fixture
def test_token() -> str:
    """Fixed test token for authentication."""
    return "test-token-" + "a" * 48


@pytest_asyncio.fixture
async def client(test_config: Config, test_db: DatabaseManager, test_token: str) -> AsyncClient:
    """Create an authenticated test HTTP client."""
    with patch.dict(os.environ, {"MEMOPILOT_TOKEN": test_token}):
        configure(test_config, test_db)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
