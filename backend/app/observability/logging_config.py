"""
Structured (JSON) logging so latency/tokens/tool-usage fields are easy to
grep or ship to a log aggregator, instead of unstructured print statements.
"""

import json
import logging
import sys

from app.config import get_settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
        }
        # `extra={...}` passed to logger calls ends up as attributes on the
        # record -- surface anything under record.extra_fields automatically.
        extra_fields = getattr(record, "extra_fields", None)
        if extra_fields:
            payload.update(extra_fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging() -> None:
    settings = get_settings()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(logger: logging.Logger, message: str, **fields) -> None:
    """Convenience wrapper: log_event(logger, "chat_turn", latency_ms=120, tokens=42)."""
    logger.info(message, extra={"extra_fields": fields})
