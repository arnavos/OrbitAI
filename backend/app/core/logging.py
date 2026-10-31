"""Centralized structured JSON logging and correlation ID propagation."""

import contextvars
import json
import logging
import time
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

# Context variable to hold the active request correlation ID across async tasks
correlation_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default="sys-init"
)


def get_correlation_id() -> str:
    """Retrieve the current active correlation ID."""
    return correlation_id_ctx.get()


def set_correlation_id(corr_id: str | None = None) -> str:
    """Set or generate a new correlation ID for the active execution context."""
    cid = corr_id or str(uuid.uuid4())
    correlation_id_ctx.set(cid)
    return cid


class JSONLogFormatter(logging.Formatter):
    """Custom logging formatter that produces machine-readable JSON logs."""

    def format(self, record: logging.LogRecord) -> str:
        log_payload: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": get_correlation_id(),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
        }

        # Include exception details if present
        if record.exc_info:
            log_payload["exception"] = self.formatException(record.exc_info)

        # Include custom extra metadata if supplied
        if hasattr(record, "extra_data") and isinstance(record.extra_data, dict):
            log_payload["data"] = record.extra_data

        return json.dumps(log_payload)


def setup_logging(log_dir: Path, log_level: str = "INFO") -> logging.Logger:
    """Initialize root application logger with console and rotating file handlers."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "orbit.log"

    root_logger = logging.getLogger("orbit")
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    root_logger.handlers.clear()

    # Formatter
    formatter = JSONLogFormatter()

    # 1. Console Output Handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 2. Rotating File Handler (Max 10 MB, keep 5 backups)
    file_handler = RotatingFileHandler(
        filename=str(log_file),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Prevent duplicate propagation to standard root
    root_logger.propagate = False

    return root_logger


logger = logging.getLogger("orbit")
