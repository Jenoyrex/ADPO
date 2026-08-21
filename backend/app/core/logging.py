"""Structured logging setup. Every log line is a single JSON object so
GitHub API traffic, sync progress, and analysis runs can be filtered/queried
in any log aggregator without a custom parser."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


ADPO_LOGGER_NAME = "adpo"


def configure_logging(level: int = logging.INFO) -> None:
    """Attaches the JSON handler to our own `adpo` logger namespace only -
    deliberately NOT the root logger. Attaching to root would also capture
    every third-party library's own logging (httpx logs one INFO line per
    HTTP request, uvicorn, sqlalchemy, ...), which is noisy at best and, in
    at least one observed case, deadlocked under pytest's stdout capture
    (a library log call reentered logging while flushing to a
    capture-wrapped stdout pipe that nothing was draining). Scoping to our
    own namespace avoids both problems and keeps log output to exactly
    what ADPO itself intentionally logs."""
    adpo_logger = logging.getLogger(ADPO_LOGGER_NAME)
    if adpo_logger.handlers:
        return  # already configured (e.g. re-imported under pytest)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    adpo_logger.addHandler(handler)
    adpo_logger.setLevel(level)
    adpo_logger.propagate = False  # don't also hand records up to root


def get_logger(name: str) -> logging.Logger:
    """`name` should be `"adpo"` or a dotted child of it (e.g.
    `"adpo.github.client"`) so it inherits the handler configured above."""
    return logging.getLogger(name)


def log_extra(**fields: Any) -> dict[str, dict[str, Any]]:
    """Usage: logger.info("message", extra=log_extra(repo_id=1, run_id=2))"""
    return {"extra_fields": fields}
