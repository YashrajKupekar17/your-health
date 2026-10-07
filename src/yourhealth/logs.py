"""Structured JSON logs: one line per turn and per tool call.

PHI policy: log ids, codes, counts and timings only. Never patient text, names, dates of birth,
tool arguments or model-written strings (a model's handoff reason can quote the patient).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

LOGGER_NAME = "yourhealth"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "event": record.getMessage(),
            **getattr(record, "fields", {}),
        }
        return json.dumps(entry, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Idempotent: route the app's loggers to stderr as JSON lines."""
    root = logging.getLogger(LOGGER_NAME)
    root.setLevel(level.upper())
    if not any(getattr(h, "_yourhealth", False) for h in root.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JsonFormatter())
        handler._yourhealth = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def log_event(logger: logging.Logger, event: str, **fields) -> None:
    logger.info(event, extra={"fields": fields})
