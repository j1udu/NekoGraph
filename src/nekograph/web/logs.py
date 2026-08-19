"""Bounded structured log buffer for the local dashboard."""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import UTC, datetime
from typing import Any, cast


class DashboardLogHandler(logging.Handler):
    def __init__(self, capacity: int = 500) -> None:
        super().__init__()
        self._records: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        item: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        values = getattr(record, "event_fields", None)
        if isinstance(values, dict):
            item.update(cast(dict[str, object], values))
        if record.exc_info:
            item["exception_type"] = (
                record.exc_info[0].__name__ if record.exc_info[0] is not None else "Exception"
            )
        with self._lock:
            self._records.append(item)

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._records)[-limit:]
