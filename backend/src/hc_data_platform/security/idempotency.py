from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from threading import RLock
from typing import Any, Generic, Protocol, TypeVar

from hc_data_platform.core.errors import problem

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class IdempotencyResult(Generic[T]):
    value: T
    replayed: bool


@dataclass(frozen=True, slots=True)
class _Record(Generic[T]):
    fingerprint: str
    value: T


def request_fingerprint(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def idempotency_conflict() -> Exception:
    return problem(
        status=409,
        code="IDEMPOTENCY_KEY_REUSED",
        title="Idempotency key reused",
        detail="The same scope and key were already used with a different request body.",
    )


class IdempotencyStore(Protocol):
    def execute(
        self,
        *,
        scope: str,
        key: str,
        payload: Any,
        action: Callable[[], T],
    ) -> IdempotencyResult[T]: ...


class InMemoryIdempotencyStore:
    """Thread-safe fake with the same conflict and replay semantics as PostgreSQL."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], _Record[Any]] = {}
        self._lock = RLock()

    def execute(
        self,
        *,
        scope: str,
        key: str,
        payload: Any,
        action: Callable[[], T],
    ) -> IdempotencyResult[T]:
        if not scope or not key:
            raise ValueError("idempotency scope and key must not be empty")
        fingerprint = request_fingerprint(payload)
        record_key = (scope, key)
        with self._lock:
            existing = self._records.get(record_key)
            if existing is not None:
                if existing.fingerprint != fingerprint:
                    raise idempotency_conflict()
                return IdempotencyResult(value=deepcopy(existing.value), replayed=True)
            value = action()
            self._records[record_key] = _Record(
                fingerprint=fingerprint,
                value=deepcopy(value),
            )
            return IdempotencyResult(value=value, replayed=False)
