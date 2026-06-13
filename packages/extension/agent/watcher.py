"""File watcher for MemoPilot workspace.

Uses watchdog library for cross-platform file system monitoring.
Watches workspace files and triggers re-indexing on changes.

Excluded paths: .memopilot/, .git/, node_modules/, __pycache__/
Debounce: 1500ms per file
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

logger = logging.getLogger(__name__)

EXCLUDED_DIRS = {
    ".memopilot",
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    ".ruff_cache",
    ".pytest_cache",
}
DEBOUNCE_MS = 1500
MAX_FILES_PER_SECOND = 20


class FileChangeEvent:
    """Represents a file system change."""

    def __init__(self, event_type: str, file_path: str):
        self.event_type = event_type  # 'created', 'modified', 'deleted', 'moved'
        self.file_path = file_path
        self.timestamp = time.time()


class FileWatcher:
    """Watches workspace for file changes and queues them for indexing."""

    def __init__(
        self,
        workspace_root: Path,
        on_change: Callable[[FileChangeEvent], Awaitable[None]] | None = None,
    ):
        self.workspace_root = workspace_root
        self.on_change = on_change
        self._observer = None
        self._debounce_timers: dict[str, float] = {}
        self._queue: asyncio.Queue[FileChangeEvent] = asyncio.Queue()
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None

    def _is_excluded(self, path: str) -> bool:
        """Check if path should be excluded from watching."""
        parts = Path(path).parts
        return any(part in EXCLUDED_DIRS for part in parts)

    def _enqueue_change(self, change: FileChangeEvent) -> None:
        try:
            self._queue.put_nowait(change)
        except asyncio.QueueFull:
            logger.warning("File change queue full; dropping event for %s", change.file_path)

    async def start(self) -> None:
        """Start watching the workspace."""
        try:
            from watchdog.events import FileSystemEvent, FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            logger.warning(
                "watchdog not installed. File watching disabled. Install with: pip install watchdog"
            )
            return

        self._loop = asyncio.get_running_loop()
        watcher = self

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event: FileSystemEvent):
                if event.is_directory:
                    return
                src_path = event.src_path
                if watcher._is_excluded(src_path):
                    return
                now = time.time()
                last = watcher._debounce_timers.get(src_path, 0.0)
                if (now - last) * 1000 < DEBOUNCE_MS:
                    return
                watcher._debounce_timers[src_path] = now

                event_type_map = {
                    "created": "created",
                    "modified": "modified",
                    "deleted": "deleted",
                    "moved": "moved",
                }
                evt_type = event_type_map.get(event.event_type, "modified")
                change = FileChangeEvent(evt_type, src_path)
                if watcher._loop is not None:
                    watcher._loop.call_soon_threadsafe(watcher._enqueue_change, change)

        self._observer = Observer()
        self._observer.schedule(Handler(), str(self.workspace_root), recursive=True)
        self._observer.start()
        self._running = True
        logger.info("File watcher started for: %s", self.workspace_root)

    async def stop(self) -> None:
        """Stop watching."""
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
        self._running = False
        self._loop = None
        logger.info("File watcher stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    async def process_queue(self) -> None:
        """Process queued file change events (rate-limited)."""
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                if self.on_change:
                    await self.on_change(event)
                await asyncio.sleep(1.0 / MAX_FILES_PER_SECOND)
            except TimeoutError:
                continue
            except Exception as exc:
                logger.error("Error processing file change: %s", exc)
