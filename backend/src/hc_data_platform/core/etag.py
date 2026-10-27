from __future__ import annotations

import hashlib
import json
import re
from collections.abc import MutableMapping
from dataclasses import dataclass
from typing import Any

from .errors import problem

_ENTITY_TAG_RE = re.compile(r'^(?P<weak>W/)?"(?P<value>[!#-~\x80-\xff]*)"$')
_ENTITY_TAG_VALUE_RE = re.compile(r"^[!#-~\x80-\xff]+$")


@dataclass(frozen=True, slots=True)
class ETag:
    """An RFC 9110 entity tag with an explicit strong/weak representation."""

    value: str
    weak: bool = False

    def __post_init__(self) -> None:
        if _ENTITY_TAG_VALUE_RE.fullmatch(self.value) is None:
            raise ValueError("ETag value contains invalid characters")

    def __str__(self) -> str:
        prefix = "W/" if self.weak else ""
        return f'{prefix}"{self.value}"'

    @classmethod
    def parse(cls, value: str) -> ETag:
        match = _ENTITY_TAG_RE.fullmatch(value.strip())
        if match is None:
            raise ValueError("invalid entity tag")
        return cls(value=match.group("value"), weak=bool(match.group("weak")))


def make_etag(value: bytes | str | dict[str, Any], *, weak: bool = False) -> ETag:
    if isinstance(value, dict):
        body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    elif isinstance(value, str):
        body = value.encode()
    else:
        body = value
    return ETag(hashlib.sha256(body).hexdigest(), weak=weak)


def preserve_strong_etag(headers: MutableMapping[str, str]) -> None:
    """Keep resource validators usable for If-Match through transforming proxies."""
    value = headers.get("ETag")
    if value is None:
        return
    try:
        etag = ETag.parse(value)
    except ValueError:
        return
    if etag.weak:
        return
    cache_control = headers.get("Cache-Control", "")
    if any(part.strip().lower() == "no-transform" for part in cache_control.split(",")):
        return
    # Edge compression may otherwise change "vN" to W/"vN" on reads, making
    # every subsequent mutation fail even though the resource did not change.
    # Preserve the endpoint's privacy and freshness directives as well as its tag.
    headers["Cache-Control"] = (
        f"{cache_control}, no-transform" if cache_control else "no-transform"
    )


def etag_matches(if_match: str, current: ETag | str) -> bool:
    current_tag = ETag.parse(current) if isinstance(current, str) else current
    candidates = [candidate.strip() for candidate in if_match.split(",")]
    if "*" in candidates:
        return True
    for candidate in candidates:
        try:
            supplied = ETag.parse(candidate)
        except ValueError:
            continue
        if not supplied.weak and not current_tag.weak and supplied.value == current_tag.value:
            return True
    return False


def require_if_match(
    if_match: str | None,
    current: ETag | str,
    *,
    request_id: str | None = None,
) -> None:
    if if_match is None:
        raise problem(
            status=428,
            code="IF_MATCH_REQUIRED",
            title="Precondition required",
            detail="The If-Match header is required for this mutation.",
            request_id=request_id,
        )
    if not etag_matches(if_match, current):
        raise problem(
            status=412,
            code="ETAG_MISMATCH",
            title="Resource version changed",
            detail="The supplied entity tag does not match the current resource version.",
            request_id=request_id,
        )
