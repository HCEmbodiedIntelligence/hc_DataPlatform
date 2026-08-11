from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Query

from app.core.errors import ValidationError


@dataclass(frozen=True, slots=True)
class CursorParams:
    limit: int = 50
    after: str | None = None
    before: str | None = None

    def __post_init__(self) -> None:
        if self.after and self.before:
            raise ValidationError(
                code="CURSOR_DIRECTION_CONFLICT",
                field_errors=[
                    {
                        "path": "/after",
                        "code": "MUTUALLY_EXCLUSIVE",
                        "message": "after and before cannot be supplied together.",
                    },
                    {
                        "path": "/before",
                        "code": "MUTUALLY_EXCLUSIVE",
                        "message": "after and before cannot be supplied together.",
                    },
                ],
            )
        if not 1 <= self.limit <= 100:
            raise ValidationError(
                code="INVALID_PAGE_LIMIT",
                field_errors=[
                    {"path": "/limit", "code": "OUT_OF_RANGE", "message": "limit must be 1..100."}
                ],
            )


async def cursor_params(
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    after: Annotated[str | None, Query(min_length=1, max_length=2048)] = None,
    before: Annotated[str | None, Query(min_length=1, max_length=2048)] = None,
) -> CursorParams:
    return CursorParams(limit=limit, after=after, before=before)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    raise TypeError(f"Cursor value {type(value).__name__} is not serializable")


def encode_cursor(**parts: Any) -> str:
    raw = json.dumps(
        {"v": 1, "parts": parts},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_json_default,
    ).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        padding = "=" * (-len(cursor) % 4)
        document = json.loads(base64.urlsafe_b64decode(cursor + padding))
        if document.get("v") != 1 or not isinstance(document.get("parts"), dict):
            raise ValueError("unsupported cursor")
        return document["parts"]
    except (
        ValueError,
        TypeError,
        KeyError,
        json.JSONDecodeError,
        binascii.Error,
        UnicodeDecodeError,
    ) as exc:
        raise ValidationError(code="INVALID_CURSOR", message="The page cursor is invalid.") from exc


def _read(item: Any, field: str) -> Any:
    if isinstance(item, dict):
        return item[field]
    return getattr(item, field)


def _cursor_for(item: Any, cursor_fields: tuple[str, ...] | list[str]) -> str:
    return encode_cursor(**{field: _read(item, field) for field in cursor_fields})


def build_page(
    items: list[Any],
    params: CursorParams,
    cursor_fields: tuple[str, ...] | list[str],
) -> dict[str, Any]:
    """Build a bidirectional page from an already stably ordered limit+1 query."""

    has_extra = len(items) > params.limit
    page_items = items[: params.limit]
    has_previous = bool(params.after) or (has_extra if params.before else False)
    has_next = bool(params.before) or (has_extra if not params.before else False)
    return {
        "items": page_items,
        "page_info": {
            "has_next_page": has_next,
            "has_previous_page": has_previous,
            "start_cursor": _cursor_for(page_items[0], cursor_fields) if page_items else None,
            "end_cursor": _cursor_for(page_items[-1], cursor_fields) if page_items else None,
        },
        "snapshot_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
