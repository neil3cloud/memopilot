"""MemoPilot Agent Backend entrypoint.

Starts the FastAPI server bound to 127.0.0.1:0 (OS-assigned port).
Writes the assigned port and PID to <workspace>/.memopilot/agent.lock.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_RUNTIME_DEPENDENCIES: tuple[tuple[str, str], ...] = (
    ("uvicorn", "uvicorn[standard]>=0.27.0"),
    ("fastapi", "fastapi>=0.109.0"),
    ("pydantic", "pydantic>=2.5.0"),
    ("aiosqlite", "aiosqlite>=0.19.0"),
    ("detect_secrets", "detect-secrets>=1.5.0"),
    ("openpyxl", "openpyxl>=3.1.5"),
    ("pdfplumber", "pdfplumber>=0.11.4"),
    ("yaml", "pyyaml>=6.0"),
    ("PIL", "pillow>=11.0.0"),
    ("docx", "python-docx>=1.1.2"),
    ("pptx", "python-pptx>=1.0.2"),
)


def ensure_runtime_dependencies() -> None:
    """Install missing runtime dependencies into the selected interpreter."""
    missing = [
        module_name
        for module_name, _package_name in _RUNTIME_DEPENDENCIES
        if importlib.util.find_spec(module_name) is None
    ]
    if not missing:
        return

    packages = [
        package_name
        for module_name, package_name in _RUNTIME_DEPENDENCIES
        if module_name in missing
    ]
    _ensure_pip_available()
    install = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *packages],
        capture_output=True,
        text=True,
        check=False,
    )
    if install.returncode != 0:
        raise RuntimeError(
            "Failed to install backend dependencies:\n"
            f"{(install.stderr or install.stdout).strip()}"
        )


def _ensure_pip_available() -> None:
    check = subprocess.run(
        [sys.executable, "-m", "pip", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    if check.returncode == 0:
        return
    output = (check.stderr or check.stdout).strip()
    if "No module named pip" not in output:
        raise RuntimeError(f"Failed to verify pip availability:\n{output}")

    ensure = subprocess.run(
        [sys.executable, "-m", "ensurepip", "--upgrade"],
        capture_output=True,
        text=True,
        check=False,
    )
    if ensure.returncode != 0:
        raise RuntimeError(
            "Failed to bootstrap pip with ensurepip:\n"
            f"{(ensure.stderr or ensure.stdout).strip()}"
        )


def write_lockfile(lock_path: Path, port: int, pid: int) -> None:
    """Write port and PID to the agent lockfile atomically."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_data = {"port": port, "pid": pid}
    # Atomic write: write to temp file then rename to avoid race conditions
    tmp_path = lock_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(lock_data), encoding="utf-8")
    tmp_path.replace(lock_path)


def cleanup_lockfile(lock_path: Path) -> None:
    """Remove the lockfile on shutdown."""
    try:
        if lock_path.exists():
            lock_path.unlink()
    except OSError:
        pass


def main() -> None:
    """Main entry point for the MemoPilot agent backend."""
    ensure_runtime_dependencies()

    import uvicorn

    from .api import app, configure
    from .config import load_config
    from .db import DatabaseManager
    from .logger import setup_logging

    class LockfileServer(uvicorn.Server):
        """Custom uvicorn server that writes lockfile after binding."""

        def __init__(self, config: uvicorn.Config, lock_path: Path) -> None:
            super().__init__(config)
            self.lock_path = lock_path

        async def startup(self, sockets=None) -> None:
            await super().startup(sockets)
            if self.servers:
                for server in self.servers:
                    socks = server.sockets
                    if socks:
                        addr = socks[0].getsockname()
                        port = addr[1]
                        write_lockfile(self.lock_path, port, os.getpid())
                        logger.info(f"Backend listening on 127.0.0.1:{port}")
                        print(f"MemoPilot backend started on port {port}", flush=True)
                        break

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
