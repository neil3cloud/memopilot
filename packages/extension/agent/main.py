"""MemoPilot Agent Backend entrypoint.

Starts the FastAPI server bound to 127.0.0.1:0 (OS-assigned port).
Writes the assigned port and PID to <workspace>/.memopilot/agent.lock.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
from pathlib import Path

import uvicorn

from .api import app, configure
from .config import load_config
from .db import DatabaseManager
from .logger import setup_logging

logger = logging.getLogger(__name__)


def write_lockfile(lock_path: Path, port: int, pid: int) -> None:
    """Write port and PID to the agent lockfile."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_data = {"port": port, "pid": pid}
    lock_path.write_text(json.dumps(lock_data), encoding="utf-8")


def cleanup_lockfile(lock_path: Path) -> None:
    """Remove the lockfile on shutdown."""
    try:
        if lock_path.exists():
            lock_path.unlink()
    except OSError:
        pass


class LockfileServer(uvicorn.Server):
    """Custom uvicorn server that writes lockfile after binding."""

    def __init__(self, config: uvicorn.Config, lock_path: Path) -> None:
        super().__init__(config)
        self.lock_path = lock_path

    async def startup(self, sockets=None) -> None:
        await super().startup(sockets)
        # After startup, extract the bound port
        if self.servers:
            for server in self.servers:
                socks = server.sockets
                if socks:
                    addr = socks[0].getsockname()
                    port = addr[1]
                    write_lockfile(self.lock_path, port, os.getpid())
                    logger.info(f"Backend listening on 127.0.0.1:{port}")
                    # Also print to stdout for debugging
                    print(f"MemoPilot backend started on port {port}", flush=True)
                    break


def main() -> None:
    """Main entry point for the MemoPilot agent backend."""
    # Load configuration
    config = load_config()

    # Setup logging
    setup_logging(logs_dir=config.logs_dir, level=config.log_level)
    logger.info(f"Starting MemoPilot agent for workspace: {config.workspace_path}")

    # Initialize database manager
    db = DatabaseManager(config.db_path)

    # Configure the FastAPI app
    configure(config, db)

    # Lockfile path
    lock_path = config.memopilot_dir / "agent.lock"

    # Register cleanup
    def on_exit(signum, frame):
        cleanup_lockfile(lock_path)
        sys.exit(0)

    signal.signal(signal.SIGTERM, on_exit)
    signal.signal(signal.SIGINT, on_exit)

    # Start uvicorn with OS-assigned port
    uvicorn_config = uvicorn.Config(
        app=app,
        host="127.0.0.1",
        port=0,  # OS assigns a free port
        log_level=config.log_level,
        access_log=False,
    )

    server = LockfileServer(uvicorn_config, lock_path)

    try:
        import asyncio
        asyncio.run(server.serve())
    finally:
        cleanup_lockfile(lock_path)


if __name__ == "__main__":
    main()
