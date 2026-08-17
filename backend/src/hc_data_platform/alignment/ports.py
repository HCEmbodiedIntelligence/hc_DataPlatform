"""Staging-writer boundary keeps BE-07 independent from Lance commits."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from .models import AlignedFragmentManifestV1, AlignedRowV1, AlignmentInputV1, AlignmentProfileV1


@runtime_checkable
class FragmentWriterPort(Protocol):
    def begin(self, *, rollout_id: str, attempt_id: str) -> None: ...

    def write_row(self, row: AlignedRowV1) -> None: ...

    def commit(self, *, row_count: int, content_sha256: str, schema_sha256: str) -> str:
        """Atomically finish this attempt and return its isolated staging URI."""

    def abort(self) -> None: ...


@runtime_checkable
class AlignmentPort(Protocol):
    def align_to_writer(
        self,
        data: AlignmentInputV1,
        profile: AlignmentProfileV1,
        writer: FragmentWriterPort,
    ) -> AlignedFragmentManifestV1: ...


@dataclass
class _FakeAttempt:
    rollout_id: str
    rows: list[AlignedRowV1] = field(default_factory=list)
    status: str = "WRITING"
    staging_uri: str | None = None


class FakeFragmentWriter:
    """Attempt-aware fake that never removes another attempt during abort."""

    def __init__(self, *, fail_at_row: int | None = None) -> None:
        self.fail_at_row = fail_at_row
        self.begun: tuple[str, str] | None = None
        self._attempts: dict[str, _FakeAttempt] = {}
        self._current_attempt_id: str | None = None
        self._last_attempt_id: str | None = None

    def begin(self, *, rollout_id: str, attempt_id: str) -> None:
        if self._current_attempt_id is not None:
            raise RuntimeError("another writer attempt is active")
        if attempt_id in self._attempts:
            raise ValueError(f"attempt already exists: {attempt_id}")
        self._attempts[attempt_id] = _FakeAttempt(rollout_id=rollout_id)
        self._current_attempt_id = attempt_id
        self._last_attempt_id = attempt_id
        self.begun = (rollout_id, attempt_id)

    def write_row(self, row: AlignedRowV1) -> None:
        attempt = self._current_attempt()
        if self.fail_at_row is not None and len(attempt.rows) == self.fail_at_row:
            raise OSError("injected staging failure")
        attempt.rows.append(row)

    def commit(self, *, row_count: int, content_sha256: str, schema_sha256: str) -> str:
        self._validate_hash(content_sha256, "content_sha256")
        self._validate_hash(schema_sha256, "schema_sha256")
        attempt_id = self._require_current_attempt_id()
        attempt = self._attempts[attempt_id]
        if row_count != len(attempt.rows):
            raise ValueError("row count does not match staged rows")
        attempt.status = "READY"
        attempt.staging_uri = (
            f"fake://staging/{attempt.rollout_id}/{attempt_id}/{content_sha256}.arrow"
        )
        self._current_attempt_id = None
        return attempt.staging_uri

    def abort(self) -> None:
        if self._current_attempt_id is None:
            return
        attempt = self._attempts[self._current_attempt_id]
        attempt.status = "ABORTED"
        attempt.rows.clear()
        attempt.staging_uri = None
        self._current_attempt_id = None

    @property
    def rows(self) -> list[AlignedRowV1]:
        if self._last_attempt_id is None:
            return []
        return self._attempts[self._last_attempt_id].rows

    @property
    def aborted(self) -> bool:
        return self._latest_status() == "ABORTED"

    @property
    def committed(self) -> bool:
        return any(attempt.status == "READY" for attempt in self._attempts.values())

    @property
    def committed_attempt_ids(self) -> tuple[str, ...]:
        return tuple(
            attempt_id
            for attempt_id, attempt in self._attempts.items()
            if attempt.status == "READY"
        )

    def attempt_status(self, attempt_id: str) -> str:
        return self._attempts[attempt_id].status

    def rows_for_attempt(self, attempt_id: str) -> tuple[AlignedRowV1, ...]:
        return tuple(self._attempts[attempt_id].rows)

    def _current_attempt(self) -> _FakeAttempt:
        return self._attempts[self._require_current_attempt_id()]

    def _require_current_attempt_id(self) -> str:
        if self._current_attempt_id is None:
            raise RuntimeError("writer attempt is not active")
        return self._current_attempt_id

    def _latest_status(self) -> str | None:
        if self._last_attempt_id is None:
            return None
        return self._attempts[self._last_attempt_id].status

    @staticmethod
    def _validate_hash(value: str, name: str) -> None:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError(f"{name} must be a lowercase SHA-256 digest")
