from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

_SENSITIVE = ("authorization", "cookie", "token", "secret", "access_key", "signature")
_OBJECT_KEYS = {"object_key", "oss_key", "full_object_key", "raw_payload", "payload"}


def sanitize(value: Any, key: str | None = None) -> Any:
    lowered = (key or "").lower()
    if lowered in _OBJECT_KEYS or any(fragment in lowered for fragment in _SENSITIVE):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): sanitize(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        parsed = urlsplit(value)
        if parsed.query:
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        document: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        fields = getattr(record, "safe_fields", None)
        if isinstance(fields, dict):
            document.update(sanitize(fields))
        return json.dumps(document, separators=(",", ":"), default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())


def log_event(
    logger: logging.Logger,
    message: str,
    *,
    request_id: str,
    actor: str | None,
    operation: str,
    duration: float,
    outcome: str,
    **safe_fields: Any,
) -> None:
    logger.info(
        message,
        extra={
            "safe_fields": {
                "request_id": request_id,
                "actor": actor,
                "operation": operation,
                "duration": duration,
                "outcome": outcome,
                **safe_fields,
            }
        },
    )
