"""
Logging setup for DevFactory.

Configures two output targets:

* **Console** — Rich handler with colours and tracebacks.
* **File** — one JSON-lines log file per pipeline run, written to ``logs/``.

Usage::

    from devfactory.logging_setup import setup_logging
    setup_logging(level="INFO", issue_number=42)
    # → logs/issue-42-20240622-143012.log
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

LOGS_DIR = Path("./logs")

_LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


class JSONLinesHandler(logging.Handler):
    """Writes structured log records as JSON-lines to a file."""

    def __init__(self, path: Path):
        super().__init__()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("a", encoding="utf-8")

    def emit(self, record: logging.LogRecord):
        try:
            entry: dict = {
                "ts": datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S"),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            }
            if record.exc_info:
                entry["exc"] = self.formatException(record.exc_info)
            self._file.write(json.dumps(entry) + "\n")
            self._file.flush()
        except Exception:
            self.handleError(record)

    def close(self):
        self._file.close()
        super().close()


def setup_logging(level: str = "INFO", issue_number: int | None = None) -> Path | None:
    """
    Configure the root logger for a DevFactory session.

    Args:
        level:        Log level string (DEBUG, INFO, WARNING, ...).
        issue_number: If provided, a per-run JSON-lines log file is created
                      under logs/issue-{number}-{timestamp}.log.

    Returns:
        Path to the log file, or None if no file handler was created.
    """
    numeric_level = _LOG_LEVELS.get(level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(numeric_level)
    root.handlers.clear()

    # Console (Rich)
    try:
        from rich.logging import RichHandler

        console_handler = RichHandler(
            level=numeric_level,
            show_time=True,
            show_path=False,
            rich_tracebacks=True,
            markup=True,
        )
        root.addHandler(console_handler)
    except ImportError:
        handler = logging.StreamHandler()
        handler.setLevel(numeric_level)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        root.addHandler(handler)

    # Per-run file
    log_path: Path | None = None
    if issue_number is not None:
        ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        log_path = LOGS_DIR / f"issue-{issue_number}-{ts}.log"
        file_handler = JSONLinesHandler(log_path)
        file_handler.setLevel(logging.DEBUG)
        root.addHandler(file_handler)
        logging.getLogger(__name__).info(f"Run log: {log_path}")

    # Silence noisy third-party loggers
    for noisy in ("urllib3", "httpx", "git", "github"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return log_path
