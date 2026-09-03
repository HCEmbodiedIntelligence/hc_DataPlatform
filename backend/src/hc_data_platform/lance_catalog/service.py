"""Catalog coordination plus the thread-safe in-memory reference implementation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from threading import Lock, RLock

from hc_data_platform.alignment.canonical import normalize_for_json

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
from .ports import CatalogRepositoryPort, DatasetWriterLockPort, LanceStoragePort, _project_step
from .schema import (
    LanceDependencyError,
    compile_arrow_schema,
    compute_schema_fingerprint,
    validate_schema_fields,
)

DatasetKey = tuple[str, str]
ScopedIdempotencyKey = tuple[str, str, str, str, str]


class CatalogError(RuntimeError):
    code = "LANCE_CATALOG_ERROR"


class CatalogConflictError(CatalogError):
    code = "LANCE_CATALOG_CONFLICT"


class SchemaIncompatibleError(CatalogError):
    code = "LANCE_SCHEMA_INCOMPATIBLE"


class CatalogIndexPendingError(CatalogError):
    code = "LANCE_CATALOG_INDEX_PENDING"


class DatasetReconciliationRequired(CatalogError):
    code = "LANCE_RECONCILIATION_REQUIRED"


def _dump_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compute_fragment_hash(steps: Sequence[StepRecord]) -> str:
    """Return a deterministic logical hash independent of Python object identity."""

    digest = hashlib.sha256()
    digest.update(b"[")
    for index, step in enumerate(steps):
        if index:
            digest.update(b",")
        digest.update(_dump_json(normalize_for_json(step.model_dump(mode="python"))).encode())
    digest.update(b"]")
    return digest.hexdigest()


def _manifest_semantic_hash(manifest: AlignedFragmentManifestV1) -> str:
    """Hash retry-stable fields; attempt IDs and staging URIs may legitimately change."""

    payload = {
        "project_id": manifest.project_id,
        "dataset_id": manifest.dataset_id,
        "schema_snapshot_id": manifest.schema_snapshot_id,
        "schema_fingerprint": manifest.schema_fingerprint,
        "frequency_hz": manifest.frequency_hz,
        "rollout_id": manifest.rollout_id,
        "source_sha256": manifest.source_sha256,
        "converter_version": manifest.converter_version,
        "step_count": manifest.step_count,
        "content_hash": manifest.content_hash,
    }
    return hashlib.sha256(_dump_json(payload).encode()).hexdigest()


def _storage_commit_id(manifest: AlignedFragmentManifestV1) -> str:
    payload = (
        manifest.project_id,
        manifest.dataset_id,
        *manifest.idempotency_key,
    )
    return hashlib.sha256(_dump_json(payload).encode()).hexdigest()


def _validate_snapshot(snapshot: DatasetSchemaSnapshot) -> None:
    try:
        validate_schema_fields(snapshot.fields)
    except ValueError as exc:
        raise SchemaIncompatibleError(str(exc)) from exc
    expected = compute_schema_fingerprint(snapshot.fields)
    if snapshot.fingerprint != expected:
        raise SchemaIncompatibleError(
            "schema snapshot fingerprint does not match its canonical fields"
        )
    try:
        compile_arrow_schema(snapshot)
    except LanceDependencyError:
        # The in-memory fake remains usable without the optional data dependencies.
        pass
    except ValueError as exc:
        raise SchemaIncompatibleError(str(exc)) from exc


def _validate_manifest(schema: DatasetSchemaSnapshot, manifest: AlignedFragmentManifestV1) -> None:
    if (
        manifest.project_id != schema.project_id
        or manifest.dataset_id != schema.dataset_id
        or manifest.schema_snapshot_id != schema.schema_snapshot_id
        or manifest.schema_fingerprint != schema.fingerprint
        or manifest.frequency_hz != schema.frequency_hz
    ):
        raise SchemaIncompatibleError(
            "fragment does not match the dataset schema snapshot and frequency"
        )


def _validate_steps(
    schema: DatasetSchemaSnapshot,
    manifest: AlignedFragmentManifestV1,
    steps: Sequence[StepRecord],
) -> None:
    if len(steps) != manifest.step_count:
        raise CatalogConflictError("fragment step_count does not match supplied steps")
    expected_fields = set(schema.fields)
    for expected_index, step in enumerate(steps):
        if step.rollout_id != manifest.rollout_id:
            raise CatalogConflictError("fragment contains a step from another rollout")
        if step.step_index != expected_index:
            raise CatalogConflictError(
                "fragment step indexes must be ordered and contiguous from zero"
            )
        mappings = {
            "modalities": set(step.modalities),
            "source_timestamps_ns": set(step.source_timestamps_ns),
            "time_error_ns": set(step.time_error_ns),
            "valid": set(step.valid),
            "repeated": set(step.repeated),
        }
        for mapping_name, actual_fields in mappings.items():
            if actual_fields != expected_fields:
                raise SchemaIncompatibleError(
                    f"step {step.step_index} {mapping_name} fields do not match the snapshot"
                )
    if compute_fragment_hash(steps) != manifest.content_hash:
        raise CatalogConflictError("fragment content hash does not match supplied steps")


def _same_idempotent_content(
    previous: AlignedFragmentManifestV1, candidate: AlignedFragmentManifestV1
) -> bool:
    return _manifest_semantic_hash(previous) == _manifest_semantic_hash(candidate)


def _ready_event(receipt: StorageCommitReceipt) -> DerivedReadyV1:
    manifest = receipt.manifest
    version = receipt.version_ref
    return DerivedReadyV1(
        project_id=manifest.project_id,
        dataset_id=manifest.dataset_id,
        rollout_id=manifest.rollout_id,
        source_sha256=manifest.source_sha256,
        converter_version=manifest.converter_version,
        dataset_version=version.version,
        lance_version=version.lance_version,
        step_count=manifest.step_count,
        content_hash=version.content_hash,
    )


@dataclass(frozen=True)
class _MemoryStorageCommit:
    receipt: StorageCommitReceipt
    steps_by_identity: dict[tuple[str, int], StepRecord]
    manifests_by_rollout: dict[str, AlignedFragmentManifestV1]


class InMemoryLanceCatalog:
    """Executable fake preserving production two-phase and single-writer semantics."""

    def __init__(self) -> None:
        self._guard = Lock()
        self._dataset_locks: dict[DatasetKey, RLock] = {}
        self._schemas: dict[DatasetKey, DatasetSchemaSnapshot] = {}
        self._storage_commits: dict[DatasetKey, list[_MemoryStorageCommit]] = {}
        self._versions: dict[DatasetKey, dict[int, DatasetVersionRef]] = {}
        self._indexed_steps: dict[tuple[str, str, int], dict[tuple[str, int], StepRecord]] = {}
        self._indexed_manifests: dict[
            tuple[str, str, int], dict[str, AlignedFragmentManifestV1]
        ] = {}
        self._idempotency: dict[ScopedIdempotencyKey, _MemoryStorageCommit] = {}
        self._pending: dict[str, PendingReconciliation] = {}

    def _lock_for(self, key: DatasetKey) -> RLock:
        with self._guard:
            return self._dataset_locks.setdefault(key, RLock())

    def _resolve_key(self, dataset_id: str, project_id: str | None) -> DatasetKey:
        if project_id is not None:
            return project_id, dataset_id
        matches = [key for key in self._schemas if key[1] == dataset_id]
        if len(matches) != 1:
            raise KeyError((project_id, dataset_id))
        return matches[0]

    @staticmethod
    def _scoped_idempotency(manifest: AlignedFragmentManifestV1) -> ScopedIdempotencyKey:
        return manifest.project_id, manifest.dataset_id, *manifest.idempotency_key

    def register_schema(self, snapshot: DatasetSchemaSnapshot) -> None:
        _validate_snapshot(snapshot)
        key = snapshot.project_id, snapshot.dataset_id
        with self._lock_for(key):
            existing = self._schemas.get(key)
            if existing is not None and existing != snapshot:
                raise SchemaIncompatibleError(
                    f"dataset {snapshot.project_id!r}/{snapshot.dataset_id!r} "
                    "already has a different schema snapshot"
                )
            self._schemas[key] = snapshot

    def schema_for(self, project_id: str, dataset_id: str) -> DatasetSchemaSnapshot | None:
        return self._schemas.get((project_id, dataset_id))

    def commit_fragment(
        self,
        manifest: AlignedFragmentManifestV1,
        steps: Sequence[StepRecord],
        *,
        simulate_catalog_failure: bool = False,
    ) -> tuple[DatasetVersionRef, DerivedReadyV1]:
        key = manifest.project_id, manifest.dataset_id
        with self._lock_for(key):
            existing = self._idempotency.get(self._scoped_idempotency(manifest))
            if existing is not None:
                if not _same_idempotent_content(existing.receipt.manifest, manifest):
                    raise CatalogConflictError(
                        "an idempotency key was reused with different logical content"
                    )
                self._index_commit(existing)
                self._pending.pop(existing.receipt.storage_commit_id, None)
                return existing.receipt.version_ref, _ready_event(existing.receipt)

            pending = self._pending_commits(key)
            if pending:
                raise DatasetReconciliationRequired(
                    f"dataset {manifest.project_id!r}/{manifest.dataset_id!r} "
                    "has an unindexed Lance commit"
                )

            schema = self._schemas.get(key)
            if schema is None:
                raise KeyError(f"unknown dataset {manifest.project_id!r}/{manifest.dataset_id!r}")
            _validate_manifest(schema, manifest)
            _validate_steps(schema, manifest, steps)

            storage_history = self._storage_commits.setdefault(key, [])
            prior_steps = dict(storage_history[-1].steps_by_identity) if storage_history else {}
            prior_manifests = (
                dict(storage_history[-1].manifests_by_rollout) if storage_history else {}
            )
            if manifest.rollout_id in prior_manifests:
                raise CatalogConflictError(f"rollout {manifest.rollout_id!r} is already committed")
            for step in steps:
                identity = step.rollout_id, step.step_index
                if identity in prior_steps:
                    raise CatalogConflictError(f"duplicate logical step {identity!r}")
                prior_steps[identity] = step
            prior_manifests[manifest.rollout_id] = manifest

            version = len(storage_history) + 1
            prior_hash = (
                storage_history[-1].receipt.version_ref.content_hash if storage_history else ""
            )
            version_hash = hashlib.sha256(
                f"{prior_hash}:{_manifest_semantic_hash(manifest)}:{version}".encode()
            ).hexdigest()
            storage_commit_id = _storage_commit_id(manifest)
            version_ref = DatasetVersionRef(
                project_id=manifest.project_id,
                dataset_id=manifest.dataset_id,
                version=version,
                schema_snapshot_id=manifest.schema_snapshot_id,
                schema_fingerprint=manifest.schema_fingerprint,
                frequency_hz=manifest.frequency_hz,
                content_hash=version_hash,
                dataset_uri=(
                    f"memory://{manifest.project_id}/{manifest.dataset_id}/"
                    f"{manifest.schema_snapshot_id}/{manifest.frequency_hz:g}/"
                    "aligned_steps.lance"
                ),
                lance_version=version,
                storage_commit_id=storage_commit_id,
                committed_rollouts=tuple(sorted(prior_manifests)),
            )
            receipt = StorageCommitReceipt(
                storage_commit_id=storage_commit_id,
                manifest=manifest,
                version_ref=version_ref,
            )
            commit = _MemoryStorageCommit(receipt, prior_steps, prior_manifests)
            storage_history.append(commit)
            self._idempotency[self._scoped_idempotency(manifest)] = commit
            if simulate_catalog_failure:
                pending_record = PendingReconciliation(
                    storage_commit_id=storage_commit_id,
                    project_id=manifest.project_id,
                    dataset_id=manifest.dataset_id,
                    dataset_uri=version_ref.dataset_uri,
                    lance_version=version_ref.lance_version,
                    error="simulated catalog transaction failure",
                )
                self._pending[storage_commit_id] = pending_record
                raise CatalogIndexPendingError(
                    "Lance commit succeeded but catalog indexing was interrupted"
                )
            self._index_commit(commit)
            return version_ref, _ready_event(receipt)

    def _index_commit(self, commit: _MemoryStorageCommit) -> None:
        version_ref = commit.receipt.version_ref
        key = version_ref.project_id, version_ref.dataset_id
        versions = self._versions.setdefault(key, {})
        if version_ref.version in versions:
            return
        expected = max(versions, default=0) + 1
        if version_ref.version != expected:
            raise DatasetReconciliationRequired(
                f"cannot index version {version_ref.version}; expected version {expected}"
            )
        index_key = *key, version_ref.version
        self._indexed_steps[index_key] = dict(commit.steps_by_identity)
        self._indexed_manifests[index_key] = dict(commit.manifests_by_rollout)
        versions[version_ref.version] = version_ref
        self._pending.pop(commit.receipt.storage_commit_id, None)

    def _pending_commits(self, key: DatasetKey) -> tuple[_MemoryStorageCommit, ...]:
        indexed = self._versions.get(key, {})
        return tuple(
            commit
            for commit in self._storage_commits.get(key, [])
            if commit.receipt.version_ref.version not in indexed
        )

    def reconcile(
        self, dataset_id: str, *, project_id: str | None = None
    ) -> tuple[DatasetVersionRef, ...]:
        key = self._resolve_key(dataset_id, project_id)
        with self._lock_for(key):
            repaired: list[DatasetVersionRef] = []
            for commit in self._pending_commits(key):
                self._index_commit(commit)
                repaired.append(commit.receipt.version_ref)
            return tuple(repaired)

    def pending_reconciliations(
        self, dataset_id: str, *, project_id: str | None = None
    ) -> tuple[PendingReconciliation, ...]:
        key = self._resolve_key(dataset_id, project_id)
        return tuple(
            item for item in self._pending.values() if (item.project_id, item.dataset_id) == key
        )

    def current_version(
        self, dataset_id: str, *, project_id: str | None = None
    ) -> DatasetVersionRef | None:
        key = self._resolve_key(dataset_id, project_id)
        with self._lock_for(key):
            versions = self._versions.get(key, {})
            return versions[max(versions)] if versions else None

    def list_versions(
        self, dataset_id: str, *, project_id: str | None = None
    ) -> tuple[DatasetVersionRef, ...]:
        key = self._resolve_key(dataset_id, project_id)
        with self._lock_for(key):
            versions = self._versions.get(key, {})
            return tuple(versions[number] for number in sorted(versions))

    def version_snapshot(
        self,
        dataset_id: str,
        *,
        version: int | None = None,
        project_id: str | None = None,
    ) -> DatasetVersionRef:
        key = self._resolve_key(dataset_id, project_id)
        with self._lock_for(key):
            return self._resolve_version(key, version)

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
        if start_step < 0 or end_step < start_step:
            raise ValueError("step window must be a valid half-open interval")
        key = self._resolve_key(dataset_id, project_id)
        with self._lock_for(key):
            selected = self._resolve_version(key, version)
            snapshot = self._indexed_steps[(*key, selected.version)]
            steps = tuple(
                _project_step(snapshot[(rollout_id, index)], columns)
                for index in range(start_step, end_step)
                if (rollout_id, index) in snapshot
            )
            return StepWindow(
                project_id=key[0],
                dataset_id=dataset_id,
                dataset_version=selected.version,
                rollout_id=rollout_id,
                start_step=start_step,
                end_step=end_step,
                steps=steps,
            )

    def lineage(
        self,
        dataset_id: str,
        rollout_id: str,
        *,
        version: int | None = None,
        project_id: str | None = None,
    ) -> RolloutLineage:
        key = self._resolve_key(dataset_id, project_id)
        with self._lock_for(key):
            selected = self._resolve_version(key, version)
            try:
                manifest = self._indexed_manifests[(*key, selected.version)][rollout_id]
            except KeyError as exc:
                raise KeyError((*key, selected.version, rollout_id)) from exc
            return RolloutLineage(
                project_id=key[0],
                dataset_id=dataset_id,
                dataset_version=selected.version,
                rollout_id=rollout_id,
                source_sha256=manifest.source_sha256,
                converter_version=manifest.converter_version,
                schema_snapshot_id=manifest.schema_snapshot_id,
                schema_fingerprint=manifest.schema_fingerprint,
                fragment_uri=manifest.fragment_uri,
                fragment_content_hash=manifest.content_hash,
                dataset_uri=selected.dataset_uri,
                lance_version=selected.lance_version,
            )

    def rewrite_storage_layout(self, dataset_id: str, *, project_id: str | None = None) -> None:
        """Simulate compaction by changing physical iteration order only."""

        key = self._resolve_key(dataset_id, project_id)
        with self._lock_for(key):
            for index_key, snapshot in tuple(self._indexed_steps.items()):
                if index_key[:2] == key:
                    self._indexed_steps[index_key] = dict(reversed(tuple(snapshot.items())))

    def _resolve_version(self, key: DatasetKey, version: int | None) -> DatasetVersionRef:
        versions = self._versions.get(key, {})
        selected_version = max(versions) if version is None and versions else version
        if selected_version is None or selected_version not in versions:
            raise KeyError((*key, selected_version))
        return versions[selected_version]


class LanceCatalogService:
    """Production coordinator with Lance storage, catalog, and writer-lock ports."""

    def __init__(
        self,
        storage: LanceStoragePort,
        repository: CatalogRepositoryPort,
        writer_lock: DatasetWriterLockPort,
    ) -> None:
        self._storage = storage
        self._repository = repository
        self._writer_lock = writer_lock
        self._known_projects: dict[str, set[str]] = {}

    def register_schema(self, snapshot: DatasetSchemaSnapshot) -> None:
        _validate_snapshot(snapshot)
        self._repository.register_schema(snapshot, self._storage.dataset_uri(snapshot))
        self._known_projects.setdefault(snapshot.dataset_id, set()).add(snapshot.project_id)

    def schema_for(self, project_id: str, dataset_id: str) -> DatasetSchemaSnapshot | None:
        return self._repository.schema_for(project_id, dataset_id)

    def _project(self, dataset_id: str, project_id: str | None) -> str:
        if project_id is not None:
            return project_id
        projects = self._known_projects.get(dataset_id, set())
        if len(projects) != 1:
            raise KeyError((project_id, dataset_id))
        return next(iter(projects))

    def _schema(self, project_id: str, dataset_id: str) -> DatasetSchemaSnapshot:
        schema = self._repository.schema_for(project_id, dataset_id)
        if schema is None:
            raise KeyError((project_id, dataset_id))
        return schema

    def commit_fragment(
        self,
        manifest: AlignedFragmentManifestV1,
        steps: Sequence[StepRecord],
    ) -> tuple[DatasetVersionRef, DerivedReadyV1]:
        schema = self._schema(manifest.project_id, manifest.dataset_id)
        _validate_manifest(schema, manifest)
        _validate_steps(schema, manifest, steps)
        with self._writer_lock.acquire(manifest.project_id, manifest.dataset_id):
            existing = self._repository.find_idempotent(
                manifest.project_id, manifest.dataset_id, manifest.idempotency_key
            )
            if existing is not None:
                result = self._return_existing(existing, manifest)
                self._storage.cleanup_attempt(schema, manifest)
                return result

            stored = self._storage.find_commit(schema, manifest.idempotency_key)
            if stored is not None:
                self._assert_idempotent_content(stored, manifest)
                # Rebuild in logical order so a retry can also recover an empty
                # or partially restored PostgreSQL catalog.
                self._reconcile_locked(schema)
                self._storage.cleanup_attempt(schema, manifest)
                return stored.version_ref, _ready_event(stored)

            self._reconcile_locked(schema)
            stage_uri = self._storage.stage_attempt(schema, manifest, steps)
            self._storage.validate_staged(schema, manifest, stage_uri)
            receipt = self._storage.commit_staged(schema, manifest, stage_uri)
            self._record_or_pending(receipt)
            self._storage.cleanup_attempt(schema, manifest)
            return receipt.version_ref, _ready_event(receipt)

    def _return_existing(
        self, receipt: StorageCommitReceipt, manifest: AlignedFragmentManifestV1
    ) -> tuple[DatasetVersionRef, DerivedReadyV1]:
        self._assert_idempotent_content(receipt, manifest)
        return receipt.version_ref, _ready_event(receipt)

    @staticmethod
    def _assert_idempotent_content(
        receipt: StorageCommitReceipt, manifest: AlignedFragmentManifestV1
    ) -> None:
        if not _same_idempotent_content(receipt.manifest, manifest):
            raise CatalogConflictError(
                "an idempotency key was reused with different logical content"
            )

    def _record_or_pending(self, receipt: StorageCommitReceipt) -> None:
        try:
            self._repository.record_commit(receipt)
        except Exception as exc:
            pending = PendingReconciliation(
                storage_commit_id=receipt.storage_commit_id,
                project_id=receipt.manifest.project_id,
                dataset_id=receipt.manifest.dataset_id,
                dataset_uri=receipt.version_ref.dataset_uri,
                lance_version=receipt.version_ref.lance_version,
                error=f"{type(exc).__name__}: {exc}",
            )
            with suppress(Exception):
                self._repository.record_pending(pending)
                # The Lance transaction receipt remains the durable source of truth.
            raise CatalogIndexPendingError(
                "Lance commit succeeded but PostgreSQL catalog indexing is pending"
            ) from exc

    def _reconcile_locked(self, schema: DatasetSchemaSnapshot) -> tuple[DatasetVersionRef, ...]:
        repaired: list[DatasetVersionRef] = []
        for receipt in self._storage.list_commits(schema):
            if self._repository.has_storage_commit(receipt.storage_commit_id):
                continue
            self._record_or_pending(receipt)
            repaired.append(receipt.version_ref)
        return tuple(repaired)

    def reconcile(
        self, dataset_id: str, *, project_id: str | None = None
    ) -> tuple[DatasetVersionRef, ...]:
        selected_project = self._project(dataset_id, project_id)
        schema = self._schema(selected_project, dataset_id)
        with self._writer_lock.acquire(selected_project, dataset_id):
            return self._reconcile_locked(schema)

    def list_versions(
        self, dataset_id: str, *, project_id: str | None = None
    ) -> tuple[DatasetVersionRef, ...]:
        selected_project = self._project(dataset_id, project_id)
        return self._repository.list_versions(selected_project, dataset_id)

    def current_version(
        self, dataset_id: str, *, project_id: str | None = None
    ) -> DatasetVersionRef | None:
        versions = self.list_versions(dataset_id, project_id=project_id)
        return versions[-1] if versions else None

    def version_snapshot(
        self,
        dataset_id: str,
        *,
        version: int | None = None,
        project_id: str | None = None,
    ) -> DatasetVersionRef:
        versions = self.list_versions(dataset_id, project_id=project_id)
        selected = versions[-1].version if version is None and versions else version
        for item in versions:
            if item.version == selected:
                return item
        raise KeyError((project_id, dataset_id, selected))

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
        if start_step < 0 or end_step < start_step:
            raise ValueError("step window must be a valid half-open interval")
        selected_project = self._project(dataset_id, project_id)
        selected = self.version_snapshot(dataset_id, version=version, project_id=selected_project)
        steps = (
            self._storage.read_steps(selected, rollout_id, start_step, end_step)
            if columns is None
            else self._storage.read_steps(
                selected,
                rollout_id,
                start_step,
                end_step,
                columns=columns,
            )
        )
        return StepWindow(
            project_id=selected_project,
            dataset_id=dataset_id,
            dataset_version=selected.version,
            rollout_id=rollout_id,
            start_step=start_step,
            end_step=end_step,
            steps=steps,
        )

    def lineage(
        self,
        dataset_id: str,
        rollout_id: str,
        *,
        version: int | None = None,
        project_id: str | None = None,
    ) -> RolloutLineage:
        selected_project = self._project(dataset_id, project_id)
        selected = self.version_snapshot(dataset_id, version=version, project_id=selected_project)
        return self._repository.lineage(selected_project, dataset_id, rollout_id, selected.version)
