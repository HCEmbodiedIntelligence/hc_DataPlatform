"""Consumers read fixed snapshots and bounded batches, never physical locations."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Protocol

from .models import LogicalSampleBatch, LogicalSnapshotView, SnapshotSelection

if TYPE_CHECKING:
    from hc_data_platform.security import AuthContext


class SnapshotReader(Protocol):
    def snapshot(
        self, selection: SnapshotSelection, *, auth: AuthContext, snapshot_token: str
    ) -> LogicalSnapshotView: ...

    def read_samples(
        self,
        selection: SnapshotSelection,
        stream_id: str,
        start: int,
        end: int,
        *,
        auth: AuthContext,
        snapshot_token: str,
        snapshot_id: str,
    ) -> LogicalSampleBatch: ...

    def iter_samples(
        self,
        selection: SnapshotSelection,
        stream_id: str,
        *,
        auth: AuthContext,
        snapshot_token: str,
        snapshot_id: str,
        batch_size: int = 128,
    ) -> Iterator[LogicalSampleBatch]: ...
