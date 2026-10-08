"""Ports and lightweight fakes for catalog, storage, locking, and logical reads."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Protocol, runtime_checkable

from .models import (
    AlignedFragmentManifestV1,
    DatasetSchemaSnapshot,
    DatasetVersionRef,
    DerivedReadyV1,
    PendingReconciliation,
    RolloutLineage,
    StepRecord,
    StepWindow,
    StorageCommitReceipt,
)


@runtime_checkable
class StepReaderPort(Protocol):
    def read_steps(
        self,
        dataset_id: str,
        rollout_id: str,
        start_step: int,
        end_step: int,
        *,
        version: int | None = None,
        project_id: str | None = None,
        columns: Sequence[str] | None = None,
    ) -> StepWindow: ...

    def lineage(
        self,
        dataset_id: str,
        rollout_id: str,
        *,
        version: int | None = None,
        project_id: str | None = None,
    ) -> RolloutLineage: ...

    def version_snapshot(
        self,
        dataset_id: str,
        *,
        version: int | None = None,
        project_id: str | None = None,
    ) -> DatasetVersionRef: ...


@runtime_checkable
class LanceCatalogPort(StepReaderPort, Protocol):
    def register_schema(self, snapshot: DatasetSchemaSnapshot) -> None: ...

    def schema_for(self, project_id: str, dataset_id: str) -> DatasetSchemaSnapshot | None: ...

    def commit_fragment(
        self,
        manifest: AlignedFragmentManifestV1,
        steps: Sequence[StepRecord],
        *,
        expected_version: int | None = None,
    ) -> tuple[DatasetVersionRef, DerivedReadyV1]: ...

    def reconcile(
        self, dataset_id: str, *, project_id: str | None = None
    ) -> tuple[DatasetVersionRef, ...]: ...

    def current_version(
        self, dataset_id: str, *, project_id: str | None = None
    ) -> DatasetVersionRef | None: ...

    def list_versions(
        self, dataset_id: str, *, project_id: str | None = None
    ) -> tuple[DatasetVersionRef, ...]: ...


class LanceStoragePort(Protocol):
    """Physical Lance operations; all identities are logical, never row addresses."""

    def dataset_uri(self, snapshot: DatasetSchemaSnapshot) -> str: ...

    def stage_attempt(
        self,
        snapshot: DatasetSchemaSnapshot,
        manifest: AlignedFragmentManifestV1,
        steps: Sequence[StepRecord],
    ) -> str: ...

    def validate_staged(
        self,
        snapshot: DatasetSchemaSnapshot,
        manifest: AlignedFragmentManifestV1,
        stage_uri: str,
    ) -> None: ...

    def find_commit(
        self,
        snapshot: DatasetSchemaSnapshot,
        idempotency_key: tuple[str, str, str],
    ) -> StorageCommitReceipt | None: ...

    def list_commits(self, snapshot: DatasetSchemaSnapshot) -> tuple[StorageCommitReceipt, ...]: ...

    def commit_staged(
        self,
        snapshot: DatasetSchemaSnapshot,
        manifest: AlignedFragmentManifestV1,
        stage_uri: str,
    ) -> StorageCommitReceipt: ...

    def cleanup_attempt(
        self,
        snapshot: DatasetSchemaSnapshot,
        manifest: AlignedFragmentManifestV1,
    ) -> None:
        """Delete an attempt dataset only after its shared commit is indexed."""

    def read_steps(
        self,
        version: DatasetVersionRef,
        rollout_id: str,
        start_step: int,
        end_step: int,
        *,
        columns: Sequence[str] | None = None,
    ) -> tuple[StepRecord, ...]: ...


class CatalogRepositoryPort(Protocol):
    """Transactional catalog index, implemented by PostgreSQL in production."""

    def register_schema(self, snapshot: DatasetSchemaSnapshot, dataset_uri: str) -> None: ...

    def schema_for(self, project_id: str, dataset_id: str) -> DatasetSchemaSnapshot | None: ...

    def find_idempotent(
        self,
        project_id: str,
        dataset_id: str,
        idempotency_key: tuple[str, str, str],
    ) -> StorageCommitReceipt | None: ...

    def has_storage_commit(self, storage_commit_id: str) -> bool: ...

    def record_commit(self, receipt: StorageCommitReceipt) -> None: ...

    def record_pending(self, pending: PendingReconciliation) -> None: ...

    def list_versions(self, project_id: str, dataset_id: str) -> tuple[DatasetVersionRef, ...]: ...

    def lineage(
        self, project_id: str, dataset_id: str, rollout_id: str, version: int
    ) -> RolloutLineage: ...


class DatasetWriterLockPort(Protocol):
    """Cross-process lock held across storage append and catalog registration."""

    def acquire(self, project_id: str, dataset_id: str) -> AbstractContextManager[None]: ...


def _project_step(step: StepRecord, columns: Sequence[str] | None) -> StepRecord:
    if columns is None:
        return step
    selected = frozenset(columns)
    return step.model_copy(
        update={
            "modalities": {
                name: value for name, value in step.modalities.items() if name in selected
            },
            "source_timestamps_ns": {
                name: value for name, value in step.source_timestamps_ns.items() if name in selected
            },
            "time_error_ns": {
                name: value for name, value in step.time_error_ns.items() if name in selected
            },
            "valid": {name: value for name, value in step.valid.items() if name in selected},
            "repeated": {name: value for name, value in step.repeated.items() if name in selected},
        }
    )


class FakeStepReader:
    """Minimal deterministic fake for annotation, preview, and publishing tests."""

    _ZERO_HASH = "0" * 64

    def __init__(
        self,
        dataset_id: str,
        version: int,
        steps: Sequence[StepRecord],
        *,
        project_id: str = "fake-project",
    ) -> None:
        self._project_id = project_id
        self._dataset_id = dataset_id
        self._version = version
        self._steps = tuple(steps)

    def _check(self, dataset_id: str, version: int | None, project_id: str | None) -> int:
        if dataset_id != self._dataset_id:
            raise KeyError(dataset_id)
        if project_id is not None and project_id != self._project_id:
            raise KeyError((project_id, dataset_id))
        selected_version = self._version if version is None else version
        if selected_version != self._version:
            raise KeyError(selected_version)
        return selected_version

    def read_steps(
        self,
        dataset_id: str,
        rollout_id: str,
        start_step: int,
        end_step: int,
        *,
        version: int | None = None,
        project_id: str | None = None,
        columns: Sequence[str] | None = None,
    ) -> StepWindow:
        selected_version = self._check(dataset_id, version, project_id)
        selected = tuple(
            _project_step(step, columns)
            for step in self._steps
            if step.rollout_id == rollout_id and start_step <= step.step_index < end_step
        )
        return StepWindow(
            project_id=self._project_id,
            dataset_id=dataset_id,
            dataset_version=selected_version,
            rollout_id=rollout_id,
            start_step=start_step,
            end_step=end_step,
            steps=selected,
        )

    def lineage(
        self,
        dataset_id: str,
        rollout_id: str,
        *,
        version: int | None = None,
        project_id: str | None = None,
    ) -> RolloutLineage:
        selected_version = self._check(dataset_id, version, project_id)
        if not any(step.rollout_id == rollout_id for step in self._steps):
            raise KeyError(rollout_id)
        return RolloutLineage(
            project_id=self._project_id,
            dataset_id=dataset_id,
            dataset_version=selected_version,
            rollout_id=rollout_id,
            source_sha256=self._ZERO_HASH,
            converter_version="fake",
            schema_snapshot_id="fake",
            schema_fingerprint=self._ZERO_HASH,
            fragment_uri="memory://fake-fragment",
            fragment_content_hash=self._ZERO_HASH,
            dataset_uri="memory://fake-dataset/aligned_steps.lance",
            lance_version=selected_version,
        )

    def version_snapshot(
        self,
        dataset_id: str,
        *,
        version: int | None = None,
        project_id: str | None = None,
    ) -> DatasetVersionRef:
        selected_version = self._check(dataset_id, version, project_id)
        return DatasetVersionRef(
            project_id=self._project_id,
            dataset_id=dataset_id,
            version=selected_version,
            schema_snapshot_id="fake",
            schema_fingerprint=self._ZERO_HASH,
            frequency_hz=1,
            content_hash=self._ZERO_HASH,
            dataset_uri="memory://fake-dataset/aligned_steps.lance",
            lance_version=selected_version,
            storage_commit_id=self._ZERO_HASH,
            committed_rollouts=tuple(sorted({step.rollout_id for step in self._steps})),
        )
