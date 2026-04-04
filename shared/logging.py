from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

UTC = timezone.utc
from typing import Any

from shared.request_context import current_correlation_id, current_request_id, current_user_id


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        request_id = getattr(record, "request_id", None) or current_request_id()
        correlation_id = getattr(record, "correlation_id", None) or current_correlation_id()
        user_id = getattr(record, "user_id", None) or current_user_id()
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "service": getattr(record, "service", record.name),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        dynamic_fields = {
            "request_id": request_id,
            "correlation_id": correlation_id,
            "user_id": user_id,
            "event_type": getattr(record, "event_type", None),
            "path": getattr(record, "path", None),
            "status_code": getattr(record, "status_code", None),
            "duration_ms": getattr(record, "duration_ms", None),
        }
        for key, value in dynamic_fields.items():
            if value is not None:
                payload[key] = value
        return json.dumps(payload, default=str)


def get_logger(service: str) -> logging.Logger:
    logger = logging.getLogger(service)
    if logger.handlers:
        return logger
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger
