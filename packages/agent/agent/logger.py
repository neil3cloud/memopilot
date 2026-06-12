"""Structured logging for MemoPilot agent.

Outputs JSON lines to:
  - <workspace>/.memopilot/logs/agent-runs.jsonl
  - stderr (for extension output channel capture)
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path


class JsonFormatter(logging.Formatter):
    """Formats log records as JSON lines."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)


def setup_logging(logs_dir: Path | None = None, level: str = "info") -> None:
    """Configure structured logging for the agent."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Clear existing handlers
    root_logger.handlers.clear()

    formatter = JsonFormatter()

    # stderr handler (captured by extension)
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    root_logger.addHandler(stderr_handler)

    # File handler (append-only JSONL)
    if logs_dir is not None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = logs_dir / "agent-runs.jsonl"
        file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
