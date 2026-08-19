"""Small JSON logging setup based on the standard library."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, cast


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        fields = getattr(record, "event_fields", None)
        if isinstance(fields, dict):
            payload.update(cast(dict[str, object], fields))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def fields(**values: object) -> dict[str, dict[str, object]]:
    return {"event_fields": values}
