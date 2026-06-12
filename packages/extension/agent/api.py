"""FastAPI application for MemoPilot agent backend.

Routes:
  GET  /v1/health         — Health check with version info
  POST /v1/workspace/init — Initialize .memopilot/ workspace structure

Security:
  All routes require X-Agent-Token header matching MEMOPILOT_TOKEN env var.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .config import Config
from .db import DatabaseManager
from .migration_runner import run_migrations
from .workspace_init import ensure_global_config

logger = logging.getLogger(__name__)

app = FastAPI(title="MemoPilot Agent", version="0.1.0")

# Module-level state (set during startup)
_config: Config | None = None
_db: DatabaseManager | None = None
_expected_token: str | None = None


class HealthResponse(BaseModel):
    schema_version: int
    api_version: int
    status: str


class InitWorkspaceResponse(BaseModel):
    initialized: bool
    memopilot_dir: str


def configure(config: Config, db: DatabaseManager) -> None:
    """Configure the app with resolved config and database manager."""
    global _config, _db, _expected_token
    _config = config
    _db = db
    _expected_token = os.environ.get("MEMOPILOT_TOKEN")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Validate X-Agent-Token on every request."""
    if _expected_token is None:
        return JSONResponse(status_code=500, content={"detail": "MEMOPILOT_TOKEN not configured"})

    token = request.headers.get("X-Agent-Token")
    if not token or token != _expected_token:
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    response = await call_next(request)
    return response


@app.get("/v1/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Return backend health status and version info."""
    config = _get_config()
    return HealthResponse(
        schema_version=config.schema_version,
        api_version=config.api_version,
        status="ok",
    )


@app.post("/v1/workspace/init", response_model=InitWorkspaceResponse)
async def init_workspace() -> InitWorkspaceResponse:
    """Initialize the .memopilot/ workspace folder structure and run migrations."""
    config = _get_config()
    db = _get_db()

    # Create directory structure
    dirs_to_create = [
        config.memopilot_dir / "rules",
        config.memopilot_dir / "memory",
        config.memopilot_dir / "logs",
        config.memopilot_dir / "context-packs",
        config.memopilot_dir / "context-templates",
        config.memopilot_dir / "snapshots",
    ]
    for dir_path in dirs_to_create:
        dir_path.mkdir(parents=True, exist_ok=True)

    # Ensure global config exists
    ensure_global_config(config.global_dir)

    # Run database migrations
    conn = await db.connect()
    schema_version = await run_migrations(conn)
    config.schema_version = schema_version

    logger.info(
        f"Workspace initialized: {config.memopilot_dir} (schema v{schema_version})"
    )

    return InitWorkspaceResponse(
        initialized=True,
        memopilot_dir=str(config.memopilot_dir),
    )


def _get_config() -> Config:
    if _config is None:
        raise HTTPException(status_code=500, detail="Backend not configured")
    return _config


def _get_db() -> DatabaseManager:
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    return _db
