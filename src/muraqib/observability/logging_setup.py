"""Structured JSON logging with secret scrubbing."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone

_SECRET = re.compile(
    r"(?i)\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|Bearer\s+[A-Za-z0-9._-]{20,})"
)

_STD = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": _SECRET.sub("[REDACTED]", record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key not in _STD and not key.startswith("_"):
                try:
                    json.dumps(value)
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = str(value)
        if record.exc_info:
            payload["exception"] = _SECRET.sub("[REDACTED]", self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        JsonFormatter() if json_output else logging.Formatter("%(levelname)s %(name)s %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    logging.getLogger("httpx").setLevel(logging.WARNING)
