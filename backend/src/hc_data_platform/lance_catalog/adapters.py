"""Real Lance/PostgreSQL adapters and deterministic in-memory port implementations."""

from __future__ import annotations

import gc
import hashlib
import importlib
import json
import math
import shutil
from base64 import b64encode
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from ctypes import CDLL
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from threading import Lock, RLock
from types import ModuleType
from typing import Any, Protocol, cast
from urllib.parse import quote, unquote, urlparse

from .models import (
    AlignedFragmentManifestV1,
    DatasetSchemaSnapshot,
    DatasetVersionRef,
    PendingReconciliation,
    RolloutLineage,
    StepRecord,
    StorageCommitReceipt,
)
from .schema import (
    JSON_MODALITIES_METADATA_KEY,
    LanceDependencyError,
    canonical_schema_spec,
    compile_arrow_schema,
)
from .service import (
    CatalogConflictError,
    DatasetReconciliationRequired,
    SchemaIncompatibleError,
    _dump_json,
    _manifest_semantic_hash,
    _same_idempotent_content,
    _storage_commit_id,
)

_RECEIPT_PROPERTY = "hc.catalog.receipt"
_STAGE_MANIFEST_PROPERTY = "hc.stage.manifest"
_STAGE_ARROW_HASH_PROPERTY = "hc.stage.arrow_content_hash"
_STAGE_SEMANTIC_HASH_PROPERTY = "hc.stage.semantic_hash"
_WRITE_MAX_ROWS = 4_096
_WRITE_MAX_ROWS_PER_GROUP = 512
_WRITE_MAX_BYTES = 32 * 1024 * 1024


def _release_native_memory(arrow: Any) -> None:
    gc.collect()
    arrow.default_memory_pool().release_unused()
    try:
        malloc_trim = CDLL(None).malloc_trim
    except (AttributeError, OSError):
        return
    malloc_trim(0)


def _module(name: str) -> ModuleType:
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        raise LanceDependencyError(
            f"{name} is required by LanceAdapter; install the backend 'data' extra"
        ) from exc


def _safe_component(value: str) -> str:
    return quote(value, safe="-._~")


def _frequency_component(value: float) -> str:
    return f"{value:.12g}hz"


def build_dataset_uri(root_uri: str | Path, snapshot: DatasetSchemaSnapshot) -> str:
    """Build the one shared dataset URI for a project/dataset/schema/frequency tuple."""

    root = str(root_uri).rstrip("/")
    components = (
        _safe_component(snapshot.project_id),
        _safe_component(snapshot.dataset_id),
        _safe_component(snapshot.schema_snapshot_id),
        _frequency_component(snapshot.frequency_hz),
        "aligned_steps.lance",
    )
    return "/".join((root, *components))


def _normalize_arrow_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return {"$float": "nan"}
        if math.isinf(value):
            return {"$float": "inf" if value > 0 else "-inf"}
        return {"$float": value.hex()}
    if isinstance(value, bytes):
        return {"$binary": b64encode(value).decode("ascii")}
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, (date, datetime, time)):
        return {"$temporal": value.isoformat()}
    if isinstance(value, dict):
        return {str(key): _normalize_arrow_value(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_normalize_arrow_value(item) for item in value]
    raise TypeError(f"unsupported Arrow scalar value {type(value).__name__}")


def _arrow_rows(table: Any, *, batch_rows: int = 4_096) -> Iterator[dict[str, object]]:
    """Materialize at most one bounded RecordBatch as Python objects."""

    for batch in table.to_batches(max_chunksize=batch_rows):
        yield from cast(list[dict[str, object]], batch.to_pylist())


def _arrow_table_hash(table: Any) -> str:
    """Hash logical rows incrementally, independent of Arrow chunk boundaries."""

    digest = hashlib.sha256()
    digest.update(b"[")
    first = True
    for row in _arrow_rows(table):
        if not first:
            digest.update(b",")
        digest.update(_dump_json(_normalize_arrow_value(row)).encode())
        first = False
    digest.update(b"]")
    return digest.hexdigest()


def _table_from_steps(snapshot: DatasetSchemaSnapshot, steps: Sequence[StepRecord]) -> Any:
    arrow = _module("pyarrow")
    schema = compile_arrow_schema(snapshot)
    json_modalities = {
        name for name, type_name in canonical_schema_spec(snapshot.fields) if type_name == "json"
    }
    rows = []
    for step in steps:
        row = step.model_dump(mode="python", exclude={"schema_version"})
        modalities = dict(row["modalities"])
        for name in json_modalities:
            modalities[name] = json.dumps(
                modalities[name],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        row["modalities"] = modalities
        rows.append(row)
    try:
        return arrow.Table.from_pylist(rows, schema=schema)
    except (TypeError, ValueError) as exc:
        raise SchemaIncompatibleError(
            f"fragment values do not conform to the compiled Arrow schema: {exc}"
        ) from exc


def _step_size(step: StepRecord) -> int:
    size = 256
    for value in step.modalities.values():
        if isinstance(value, (bytes, bytearray, memoryview)):
            size += len(value)
        elif isinstance(value, str):
            size += len(value.encode())
        else:
            size += len(json.dumps(value, default=str).encode())
    return size


def _record_batches_from_steps(
    snapshot: DatasetSchemaSnapshot,
    steps: Sequence[StepRecord],
    *,
    max_rows: int = 4_096,
    max_bytes: int = _WRITE_MAX_BYTES,
) -> Iterator[Any]:
    buffered: list[StepRecord] = []
    buffered_bytes = 0
    for step in steps:
        size = _step_size(step)
        if buffered and (len(buffered) >= max_rows or buffered_bytes + size > max_bytes):
            yield from _table_from_steps(snapshot, buffered).to_batches()
            buffered = []
            buffered_bytes = 0
        buffered.append(step)
        buffered_bytes += size
    if buffered:
        yield from _table_from_steps(snapshot, buffered).to_batches()


def _write_bounded_fragments(
    lance: Any,
    arrow: Any,
    batches: Iterator[Any],
    destination: Any,
    *,
    schema: Any,
    mode: str,
    storage_options: dict[str, str],
) -> list[Any]:
    """Write one bounded Arrow batch at a time without publishing a manifest."""

    fragments: list[Any] = []
    for batch in batches:
        written = lance.fragment.write_fragments(
            batch,
            destination,
            schema=schema,
            mode=mode,
            max_rows_per_file=_WRITE_MAX_ROWS,
            max_rows_per_group=_WRITE_MAX_ROWS_PER_GROUP,
            max_bytes_per_file=_WRITE_MAX_BYTES,
            storage_options=storage_options,
        )
        fragments.extend(written)
        del batch, written
        _release_native_memory(arrow)
    if not fragments:
        raise CatalogConflictError("cannot commit an empty Lance fragment set")
    _release_native_memory(arrow)
    return fragments


def _steps_from_table(table: Any) -> tuple[StepRecord, ...]:
    """Decode logical JSON modalities without exposing their physical encoding."""

    metadata = table.schema.metadata or {}
    try:
        raw_names = json.loads(metadata.get(JSON_MODALITIES_METADATA_KEY, b"[]"))
        if not isinstance(raw_names, list) or not all(
            isinstance(name, str) and name for name in raw_names
        ):
            raise ValueError("JSON modality metadata must be a string list")
        rows: list[StepRecord] = []
        for row in _arrow_rows(table):
            modalities = row["modalities"]
            if not isinstance(modalities, dict):
                raise ValueError("modalities must be an Arrow struct")
            for name in raw_names:
                encoded = modalities.get(name)
                if not isinstance(encoded, str):
                    raise ValueError(f"JSON modality {name!r} is not encoded text")
                modalities[name] = json.loads(encoded)
            rows.append(StepRecord.model_validate(row))
        return tuple(rows)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SchemaIncompatibleError(
            f"stored Lance JSON modalities do not match schema metadata: {exc}"
        ) from exc


def _steps_from_projected_table(
    table: Any,
    modality_names: Sequence[str],
    *,
    json_modalities: frozenset[str],
) -> tuple[StepRecord, ...]:
    """Decode camera-projected batches scalar-by-scalar, never batch.to_pylist()."""

    rows: list[StepRecord] = []
    for batch in table.to_batches(max_chunksize=4_096):
        arrays = {name: batch.column(name) for name in batch.schema.names}
        for index in range(batch.num_rows):
            modalities: dict[str, object] = {}
            source_timestamps: dict[str, tuple[int, ...]] = {}
            errors: dict[str, int | None] = {}
            valid: dict[str, bool] = {}
            repeated: dict[str, bool] = {}
            for position, name in enumerate(modality_names):
                value = arrays[f"__hc_modality_{position}"][index].as_py()
                if name in json_modalities and value is not None:
                    if not isinstance(value, str):
                        raise SchemaIncompatibleError(f"JSON modality {name!r} is not encoded text")
                    value = json.loads(value)
                modalities[name] = value
                source_timestamps[name] = tuple(
                    arrays[f"__hc_source_{position}"][index].as_py() or ()
                )
                errors[name] = arrays[f"__hc_error_{position}"][index].as_py()
                valid[name] = bool(arrays[f"__hc_valid_{position}"][index].as_py())
                repeated[name] = bool(arrays[f"__hc_repeated_{position}"][index].as_py())
            rows.append(
                StepRecord(
                    rollout_id=str(arrays["rollout_id"][index].as_py()),
                    step_index=int(arrays["step_index"][index].as_py()),
                    timestamp_ns=int(arrays["timestamp_ns"][index].as_py()),
                    modalities=modalities,
                    source_timestamps_ns=source_timestamps,
                    time_error_ns=errors,
                    valid=valid,
                    repeated=repeated,
                    sample_valid=bool(arrays["sample_valid"][index].as_py()),
                )
            )
    return tuple(rows)


def _nested_column(parent: str, name: str) -> str:
    if "`" in name:
        raise ValueError("Lance projected modality names must not contain backticks")
    return f"{parent}.`{name}`"


class LanceAdapter:
    """Physical adapter using immutable Lance transactions and logical step keys."""

    def __init__(
        self,
        root_uri: str | Path,
        *,
        storage_options: dict[str, str] | None = None,
        object_store_client: Any | None = None,
    ) -> None:
        self._root_uri = str(root_uri)
        self._storage_options = dict(storage_options or {})
        self._object_store_client = object_store_client
        # This protects direct single-process use. Production also supplies the
        # PostgreSQL advisory writer lock to LanceCatalogService.
        self._guard = Lock()
        self._locks: dict[tuple[str, str], RLock] = {}

    def _lock_for(self, snapshot: DatasetSchemaSnapshot) -> RLock:
        key = snapshot.project_id, snapshot.dataset_id
        with self._guard:
            return self._locks.setdefault(key, RLock())

    def dataset_uri(self, snapshot: DatasetSchemaSnapshot) -> str:
        return build_dataset_uri(self._root_uri, snapshot)

    def _attempt_uri(
        self, snapshot: DatasetSchemaSnapshot, manifest: AlignedFragmentManifestV1
    ) -> str:
        root = self._root_uri.rstrip("/")
        return "/".join(
            (
                root,
                "_attempts",
                _safe_component(snapshot.project_id),
                _safe_component(snapshot.dataset_id),
                _storage_commit_id(manifest),
                f"{_safe_component(manifest.attempt_id)}.lance",
            )
        )

    def _open_optional(self, uri: str, *, version: int | None = None) -> Any | None:
        lance = _module("lance")
        try:
            return lance.dataset(
                uri,
                version=version,
                storage_options=self._storage_options,
            )
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            message = str(exc).lower()
            if any(marker in message for marker in ("was not found", "not found:", "no such file")):
                return None
            raise

    def stage_attempt(
        self,
        snapshot: DatasetSchemaSnapshot,
        manifest: AlignedFragmentManifestV1,
        steps: Sequence[StepRecord],
    ) -> str:
        lance = _module("lance")
        stage_uri = self._attempt_uri(snapshot, manifest)
        with self._lock_for(snapshot):
            if self._open_optional(stage_uri) is not None:
                self.validate_staged(snapshot, manifest, stage_uri)
                return stage_uri
            arrow = _module("pyarrow")
            schema = compile_arrow_schema(snapshot)
            batches = _record_batches_from_steps(snapshot, steps)
            properties = {
                _STAGE_MANIFEST_PROPERTY: manifest.model_dump_json(),
                _STAGE_ARROW_HASH_PROPERTY: manifest.content_hash,
                _STAGE_SEMANTIC_HASH_PROPERTY: _manifest_semantic_hash(manifest),
            }
            try:
                fragments = _write_bounded_fragments(
                    lance,
                    arrow,
                    batches,
                    stage_uri,
                    schema=schema,
                    mode="create",
                    storage_options=self._storage_options,
                )
                transaction = lance.Transaction(
                    read_version=0,
                    operation=lance.LanceOperation.Overwrite(schema, fragments),
                    transaction_properties=properties,
                )
                lance.LanceDataset.commit(
                    stage_uri,
                    transaction,
                    storage_options=self._storage_options,
                )
            except (OSError, TypeError, ValueError) as exc:
                raise CatalogConflictError(f"failed to stage Lance attempt: {exc}") from exc
            finally:
                _release_native_memory(arrow)
            return stage_uri

    @staticmethod
    def _transaction_properties(dataset: Any, version: int) -> dict[str, str]:
        transaction = dataset.read_transaction(version)
        if transaction is None:
            return {}
        return dict(transaction.transaction_properties or {})

    def validate_staged(
        self,
        snapshot: DatasetSchemaSnapshot,
        manifest: AlignedFragmentManifestV1,
        stage_uri: str,
    ) -> None:
        dataset = self._open_optional(stage_uri)
        if dataset is None:
            raise CatalogConflictError(f"staged attempt is missing: {stage_uri}")
        expected_schema = compile_arrow_schema(snapshot)
        if not dataset.schema.equals(expected_schema, check_metadata=True):
            raise SchemaIncompatibleError(
                "staged attempt Arrow schema differs from the Dataset Schema Snapshot"
            )
        if dataset.count_rows() != manifest.step_count:
            raise CatalogConflictError("staged attempt row count differs from its manifest")
        properties = self._transaction_properties(dataset, dataset.version)
        try:
            staged_manifest = AlignedFragmentManifestV1.model_validate_json(
                properties[_STAGE_MANIFEST_PROPERTY]
            )
            staged_arrow_hash = properties[_STAGE_ARROW_HASH_PROPERTY]
            staged_semantic_hash = properties[_STAGE_SEMANTIC_HASH_PROPERTY]
        except (KeyError, ValueError) as exc:
            raise CatalogConflictError("staged attempt metadata is incomplete") from exc
        if not _same_idempotent_content(staged_manifest, manifest):
            raise CatalogConflictError(
                "staged attempt manifest differs from the supplied logical manifest"
            )
        if staged_semantic_hash != _manifest_semantic_hash(manifest):
            raise CatalogConflictError("staged attempt manifest hash is invalid")
        if staged_arrow_hash != manifest.content_hash:
            raise CatalogConflictError("staged attempt content receipt is invalid")
        expected_index = 0
        scanner = dataset.scanner(
            columns=["rollout_id", "step_index"],
            batch_size=4_096,
            batch_size_bytes=4 * 1024 * 1024,
        )
        for batch in scanner.to_batches():
            rollout = batch.column("rollout_id")
            indexes = batch.column("step_index")
            for row_index in range(batch.num_rows):
                if (
                    rollout[row_index].as_py() != manifest.rollout_id
                    or indexes[row_index].as_py() != expected_index
                ):
                    raise CatalogConflictError(
                        "staged attempt step_index values are not contiguous logical identities"
                    )
                expected_index += 1

    def list_commits(self, snapshot: DatasetSchemaSnapshot) -> tuple[StorageCommitReceipt, ...]:
        dataset = self._open_optional(self.dataset_uri(snapshot))
        if dataset is None:
            return ()
        receipts: list[StorageCommitReceipt] = []
        for version_info in dataset.versions():
            version = int(version_info["version"])
            properties = self._transaction_properties(dataset, version)
            encoded = properties.get(_RECEIPT_PROPERTY)
            if encoded is not None:
                receipts.append(StorageCommitReceipt.model_validate_json(encoded))
        receipts.sort(key=lambda item: item.version_ref.version)
        expected_versions = list(range(1, len(receipts) + 1))
        if [item.version_ref.version for item in receipts] != expected_versions:
            raise DatasetReconciliationRequired(
                "Lance catalog receipts do not form a contiguous logical version history"
            )
        return tuple(receipts)

    def find_commit(
        self,
        snapshot: DatasetSchemaSnapshot,
        idempotency_key: tuple[str, str, str],
    ) -> StorageCommitReceipt | None:
        for receipt in self.list_commits(snapshot):
            if receipt.manifest.idempotency_key == idempotency_key:
                return receipt
        return None

    def commit_staged(
        self,
        snapshot: DatasetSchemaSnapshot,
        manifest: AlignedFragmentManifestV1,
        stage_uri: str,
    ) -> StorageCommitReceipt:
        lance = _module("lance")
        with self._lock_for(snapshot):
            existing = self.find_commit(snapshot, manifest.idempotency_key)
            if existing is not None:
                if not _same_idempotent_content(existing.manifest, manifest):
                    raise CatalogConflictError(
                        "an idempotency key was reused with different logical content"
                    )
                return existing
            self.validate_staged(snapshot, manifest, stage_uri)
            prior = self.list_commits(snapshot)
            if any(item.manifest.rollout_id == manifest.rollout_id for item in prior):
                raise CatalogConflictError(
                    f"rollout {manifest.rollout_id!r} is already present in the dataset"
                )

            shared_uri = self.dataset_uri(snapshot)
            current = self._open_optional(shared_uri)
            expected_schema = compile_arrow_schema(snapshot)
            if current is not None and not current.schema.equals(
                expected_schema, check_metadata=True
            ):
                raise SchemaIncompatibleError(
                    "shared Lance dataset schema differs from the registered snapshot"
                )
            lance_version = 1 if current is None else int(current.version) + 1
            logical_version = len(prior) + 1
            prior_hash = prior[-1].version_ref.content_hash if prior else ""
            logical_hash = hashlib.sha256(
                (f"{prior_hash}:{_manifest_semantic_hash(manifest)}:{logical_version}").encode()
            ).hexdigest()
            receipt = StorageCommitReceipt(
                storage_commit_id=_storage_commit_id(manifest),
                manifest=manifest,
                version_ref=DatasetVersionRef(
                    project_id=manifest.project_id,
                    dataset_id=manifest.dataset_id,
                    version=logical_version,
                    schema_snapshot_id=manifest.schema_snapshot_id,
                    schema_fingerprint=manifest.schema_fingerprint,
                    frequency_hz=manifest.frequency_hz,
                    content_hash=logical_hash,
                    dataset_uri=shared_uri,
                    lance_version=lance_version,
                    storage_commit_id=_storage_commit_id(manifest),
                    committed_rollouts=tuple(
                        sorted(
                            {
                                *(item.manifest.rollout_id for item in prior),
                                manifest.rollout_id,
                            }
                        )
                    ),
                ),
            )
            staged = self._open_optional(stage_uri)
            if staged is None:
                raise CatalogConflictError(f"staged attempt is missing: {stage_uri}")
            arrow = _module("pyarrow")
            batches = staged.scanner(
                batch_size=4_096,
                batch_size_bytes=_WRITE_MAX_BYTES,
            ).to_batches()
            mode = "create" if current is None else "append"
            try:
                destination = shared_uri if current is None else current
                fragments = _write_bounded_fragments(
                    lance,
                    arrow,
                    batches,
                    destination,
                    schema=expected_schema,
                    mode=mode,
                    storage_options=self._storage_options,
                )
                operation = (
                    lance.LanceOperation.Overwrite(expected_schema, fragments)
                    if current is None
                    else lance.LanceOperation.Append(fragments)
                )
                transaction = lance.Transaction(
                    read_version=0 if current is None else int(current.version),
                    operation=operation,
                    transaction_properties={_RECEIPT_PROPERTY: receipt.model_dump_json()},
                )
                committed = lance.LanceDataset.commit(
                    destination,
                    transaction,
                    storage_options=self._storage_options,
                )
            except (OSError, TypeError, ValueError) as exc:
                raise CatalogConflictError(f"failed to commit Lance attempt: {exc}") from exc
            finally:
                _release_native_memory(arrow)
            if int(committed.version) != lance_version:
                raise DatasetReconciliationRequired(
                    "Lance physical version advanced outside the dataset writer lock"
                )
            return receipt

    def cleanup_attempt(
        self,
        snapshot: DatasetSchemaSnapshot,
        manifest: AlignedFragmentManifestV1,
    ) -> None:
        """Remove a committed staging dataset without touching shared Lance data."""

        stage_uri = self._attempt_uri(snapshot, manifest)
        parsed = urlparse(stage_uri)
        if parsed.scheme in {"s3", "oss"}:
            if self._object_store_client is None:
                raise CatalogConflictError(
                    "cannot clean a remote Lance attempt without an object-store client"
                )
            prefix = unquote(parsed.path.lstrip("/")).rstrip("/") + "/"
            continuation: str | None = None
            while True:
                arguments: dict[str, object] = {
                    "Bucket": parsed.netloc,
                    "Prefix": prefix,
                }
                if continuation is not None:
                    arguments["ContinuationToken"] = continuation
                response = self._object_store_client.list_objects_v2(**arguments)
                # Some S3-compatible providers require Content-MD5 for the
                # multi-delete API. Individual deletes are universally
                # supported and attempt datasets contain only a few objects.
                for item in response.get("Contents", ()):
                    self._object_store_client.delete_object(
                        Bucket=parsed.netloc,
                        Key=str(item["Key"]),
                    )
                if not response.get("IsTruncated", False):
                    return
                continuation = str(response["NextContinuationToken"])

        if parsed.scheme not in {"", "file"}:
            raise CatalogConflictError(
                f"attempt cleanup is unsupported for URI scheme {parsed.scheme!r}"
            )
        target = Path(unquote(parsed.path) if parsed.scheme == "file" else stage_uri).resolve()
        root_parsed = urlparse(self._root_uri)
        root = Path(
            unquote(root_parsed.path) if root_parsed.scheme == "file" else self._root_uri
        ).resolve()
        attempts_root = (root / "_attempts").resolve()
        try:
            target.relative_to(attempts_root)
        except ValueError as exc:
            raise CatalogConflictError("refusing to clean a path outside _attempts") from exc
        if target.exists():
            shutil.rmtree(target)

    def read_steps(
        self,
        version: DatasetVersionRef,
        rollout_id: str,
        start_step: int,
        end_step: int,
        *,
        columns: Sequence[str] | None = None,
    ) -> tuple[StepRecord, ...]:
        arrow_compute = _module("pyarrow.compute")
        dataset = self._open_optional(version.dataset_uri, version=version.lance_version)
        if dataset is None:
            raise KeyError((version.dataset_uri, version.lance_version))
        predicate = (
            (arrow_compute.field("rollout_id") == rollout_id)
            & (arrow_compute.field("step_index") >= start_step)
            & (arrow_compute.field("step_index") < end_step)
        )
        if columns is None:
            table = dataset.to_table(filter=predicate)
            steps = list(_steps_from_table(table))
        else:
            modality_type = dataset.schema.field("modalities").type
            available = frozenset(field.name for field in modality_type)
            selected = tuple(dict.fromkeys(name for name in columns if name in available))
            projection: dict[str, str] = {
                "rollout_id": "rollout_id",
                "step_index": "step_index",
                "timestamp_ns": "timestamp_ns",
                "sample_valid": "sample_valid",
            }
            for index, name in enumerate(selected):
                projection[f"__hc_modality_{index}"] = _nested_column("modalities", name)
                projection[f"__hc_source_{index}"] = _nested_column("source_timestamps_ns", name)
                projection[f"__hc_error_{index}"] = _nested_column("time_error_ns", name)
                projection[f"__hc_valid_{index}"] = _nested_column("valid", name)
                projection[f"__hc_repeated_{index}"] = _nested_column("repeated", name)
            table = dataset.to_table(columns=projection, filter=predicate)
            raw_json = json.loads(
                (dataset.schema.metadata or {}).get(JSON_MODALITIES_METADATA_KEY, b"[]")
            )
            json_names = (
                frozenset(name for name in raw_json if isinstance(name, str))
                if isinstance(raw_json, list)
                else frozenset()
            )
            steps = list(
                _steps_from_projected_table(
                    table,
                    selected,
                    json_modalities=json_names,
                )
            )
        steps.sort(key=lambda item: item.step_index)
        return tuple(steps)

    def compact(self, snapshot: DatasetSchemaSnapshot, **options: object) -> int:
        """Compact physical files; logical readers remain pinned by catalog version."""

        dataset = self._open_optional(self.dataset_uri(snapshot))
        if dataset is None:
            raise KeyError(self.dataset_uri(snapshot))
        dataset.optimize.compact_files(**options)
        reopened = self._open_optional(self.dataset_uri(snapshot))
        if reopened is None:
            raise KeyError(self.dataset_uri(snapshot))
        return int(reopened.version)


class InMemoryCatalogRepository:
    """Transactional catalog fake used with the real Lance adapter in tests."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._schemas: dict[tuple[str, str], DatasetSchemaSnapshot] = {}
        self._dataset_uris: dict[tuple[str, str], str] = {}
        self._receipts: dict[str, StorageCommitReceipt] = {}
        self._versions: dict[tuple[str, str], dict[int, StorageCommitReceipt]] = {}
        self._idempotency: dict[tuple[str, str, str, str, str], StorageCommitReceipt] = {}
        self._pending: dict[str, PendingReconciliation] = {}

    def register_schema(self, snapshot: DatasetSchemaSnapshot, dataset_uri: str) -> None:
        key = snapshot.project_id, snapshot.dataset_id
        with self._lock:
            existing = self._schemas.get(key)
            if existing is not None and (
                existing != snapshot or self._dataset_uris[key] != dataset_uri
            ):
                raise SchemaIncompatibleError(
                    f"dataset {key!r} already has a different schema or URI"
                )
            self._schemas[key] = snapshot
            self._dataset_uris[key] = dataset_uri

    def schema_for(self, project_id: str, dataset_id: str) -> DatasetSchemaSnapshot | None:
        with self._lock:
            return self._schemas.get((project_id, dataset_id))

    def find_idempotent(
        self,
        project_id: str,
        dataset_id: str,
        idempotency_key: tuple[str, str, str],
    ) -> StorageCommitReceipt | None:
        with self._lock:
            return self._idempotency.get((project_id, dataset_id, *idempotency_key))

    def has_storage_commit(self, storage_commit_id: str) -> bool:
        with self._lock:
            return storage_commit_id in self._receipts

    def record_commit(self, receipt: StorageCommitReceipt) -> None:
        manifest = receipt.manifest
        key = manifest.project_id, manifest.dataset_id
        idempotency = *key, *manifest.idempotency_key
        with self._lock:
            existing_receipt = self._receipts.get(receipt.storage_commit_id)
            if existing_receipt is not None:
                if existing_receipt != receipt:
                    raise CatalogConflictError("storage commit receipt changed")
                return
            existing_idempotency = self._idempotency.get(idempotency)
            if existing_idempotency is not None:
                if existing_idempotency.storage_commit_id != receipt.storage_commit_id:
                    raise CatalogConflictError("idempotency key already has another commit")
                return
            versions = self._versions.setdefault(key, {})
            expected = max(versions, default=0) + 1
            if receipt.version_ref.version != expected:
                raise DatasetReconciliationRequired(
                    f"cannot index version {receipt.version_ref.version}; expected {expected}"
                )
            versions[receipt.version_ref.version] = receipt
            self._receipts[receipt.storage_commit_id] = receipt
            self._idempotency[idempotency] = receipt
            self._pending.pop(receipt.storage_commit_id, None)

    def record_pending(self, pending: PendingReconciliation) -> None:
        with self._lock:
            self._pending[pending.storage_commit_id] = pending

    def pending_reconciliations(self) -> tuple[PendingReconciliation, ...]:
        with self._lock:
            return tuple(self._pending.values())

    def list_versions(self, project_id: str, dataset_id: str) -> tuple[DatasetVersionRef, ...]:
        with self._lock:
            versions = self._versions.get((project_id, dataset_id), {})
            return tuple(versions[number].version_ref for number in sorted(versions))

    def lineage(
        self, project_id: str, dataset_id: str, rollout_id: str, version: int
    ) -> RolloutLineage:
        with self._lock:
            versions = self._versions.get((project_id, dataset_id), {})
            candidates = [
                receipt
                for number, receipt in versions.items()
                if number <= version and receipt.manifest.rollout_id == rollout_id
            ]
            if not candidates:
                raise KeyError((project_id, dataset_id, version, rollout_id))
            receipt = max(candidates, key=lambda item: item.version_ref.version)
            manifest = receipt.manifest
            selected_version = versions[version].version_ref
            return RolloutLineage(
                project_id=project_id,
                dataset_id=dataset_id,
                dataset_version=version,
                rollout_id=rollout_id,
                source_sha256=manifest.source_sha256,
                converter_version=manifest.converter_version,
                schema_snapshot_id=manifest.schema_snapshot_id,
                schema_fingerprint=manifest.schema_fingerprint,
                fragment_uri=manifest.fragment_uri,
                fragment_content_hash=manifest.content_hash,
                dataset_uri=selected_version.dataset_uri,
                lance_version=selected_version.lance_version,
            )


class InMemoryDatasetWriterLock:
    """Per-project/dataset lock fake with the same lifetime as the advisory lock."""

    def __init__(self) -> None:
        self._guard = Lock()
        self._locks: dict[tuple[str, str], RLock] = {}

    @contextmanager
    def acquire(self, project_id: str, dataset_id: str) -> Iterator[None]:
        key = project_id, dataset_id
        with self._guard:
            lock = self._locks.setdefault(key, RLock())
        with lock:
            yield


class DbApiCursor(Protocol):
    def execute(self, operation: str, parameters: Sequence[object] = ()) -> object: ...

    def fetchone(self) -> Sequence[Any] | None: ...

    def fetchall(self) -> Sequence[Sequence[Any]]: ...

    def close(self) -> None: ...


class DbApiConnection(Protocol):
    def cursor(self) -> DbApiCursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


DbApiConnectionFactory = Callable[[], DbApiConnection]


def _snapshot_from_db(value: object) -> DatasetSchemaSnapshot:
    if isinstance(value, (str, bytes, bytearray)):
        return DatasetSchemaSnapshot.model_validate_json(value)
    return DatasetSchemaSnapshot.model_validate(value)


def _receipt_from_db(value: object) -> StorageCommitReceipt:
    if isinstance(value, (str, bytes, bytearray)):
        return StorageCommitReceipt.model_validate_json(value)
    return StorageCommitReceipt.model_validate(value)


class PostgresAdvisoryDatasetLock:
    """Session-level PostgreSQL advisory lock spanning Lance and DB commits."""

    def __init__(self, connection_factory: DbApiConnectionFactory) -> None:
        self._connection_factory = connection_factory

    @contextmanager
    def acquire(self, project_id: str, dataset_id: str) -> Iterator[None]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        # PostgreSQL text parameters reject NUL separators. Canonical JSON keeps
        # the three lock components unambiguous and stable for hashtextextended().
        lock_name = _dump_json(("hc-lance-catalog", project_id, dataset_id))
        acquired = False
        try:
            cursor.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (lock_name,))
            acquired = True
            yield
        finally:
            try:
                if acquired:
                    cursor.execute(
                        "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
                        (lock_name,),
                    )
            finally:
                cursor.close()
                connection.close()


class PostgresCatalogAdapter:
    """Synchronous DB-API adapter for version, lineage, and pending records."""

    def __init__(self, connection_factory: DbApiConnectionFactory) -> None:
        self._connection_factory = connection_factory

    @contextmanager
    def _transaction(self) -> Iterator[DbApiCursor]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            yield cursor
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def register_schema(self, snapshot: DatasetSchemaSnapshot, dataset_uri: str) -> None:
        snapshot_json = snapshot.model_dump_json()
        with self._transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO lance_schema_snapshots (
                    project_id, dataset_id, schema_snapshot_id, frequency_hz,
                    fields_json, fingerprint, snapshot_json
                ) VALUES (%s, %s, %s, %s, CAST(%s AS jsonb), %s, CAST(%s AS jsonb))
                ON CONFLICT (project_id, dataset_id, schema_snapshot_id) DO NOTHING
                """,
                (
                    snapshot.project_id,
                    snapshot.dataset_id,
                    snapshot.schema_snapshot_id,
                    snapshot.frequency_hz,
                    _dump_json(snapshot.fields),
                    snapshot.fingerprint,
                    snapshot_json,
                ),
            )
            cursor.execute(
                """
                INSERT INTO lance_datasets (
                    project_id, dataset_id, schema_snapshot_id, frequency_hz,
                    fingerprint, dataset_uri
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (project_id, dataset_id) DO NOTHING
                """,
                (
                    snapshot.project_id,
                    snapshot.dataset_id,
                    snapshot.schema_snapshot_id,
                    snapshot.frequency_hz,
                    snapshot.fingerprint,
                    dataset_uri,
                ),
            )
            cursor.execute(
                """
                SELECT schema_snapshot_id, frequency_hz, fingerprint, dataset_uri
                FROM lance_datasets WHERE project_id = %s AND dataset_id = %s
                """,
                (snapshot.project_id, snapshot.dataset_id),
            )
            row = cursor.fetchone()
            expected = (
                snapshot.schema_snapshot_id,
                snapshot.frequency_hz,
                snapshot.fingerprint,
                dataset_uri,
            )
            if row is None or tuple(row) != expected:
                raise SchemaIncompatibleError(
                    "dataset is already registered with a different schema or URI"
                )

    def schema_for(self, project_id: str, dataset_id: str) -> DatasetSchemaSnapshot | None:
        with self._transaction() as cursor:
            cursor.execute(
                """
                SELECT s.snapshot_json
                FROM lance_datasets d
                JOIN lance_schema_snapshots s
                  ON s.project_id = d.project_id
                 AND s.dataset_id = d.dataset_id
                 AND s.schema_snapshot_id = d.schema_snapshot_id
                WHERE d.project_id = %s AND d.dataset_id = %s
                """,
                (project_id, dataset_id),
            )
            row = cursor.fetchone()
        return None if row is None else _snapshot_from_db(row[0])

    def find_idempotent(
        self,
        project_id: str,
        dataset_id: str,
        idempotency_key: tuple[str, str, str],
    ) -> StorageCommitReceipt | None:
        with self._transaction() as cursor:
            cursor.execute(
                """
                SELECT receipt_json FROM lance_dataset_versions
                WHERE project_id = %s AND dataset_id = %s
                  AND rollout_id = %s AND source_sha256 = %s
                  AND converter_version = %s
                """,
                (project_id, dataset_id, *idempotency_key),
            )
            row = cursor.fetchone()
        return None if row is None else _receipt_from_db(row[0])

    def has_storage_commit(self, storage_commit_id: str) -> bool:
        with self._transaction() as cursor:
            cursor.execute(
                "SELECT 1 FROM lance_dataset_versions WHERE storage_commit_id = %s",
                (storage_commit_id,),
            )
            return cursor.fetchone() is not None

    def record_commit(self, receipt: StorageCommitReceipt) -> None:
        manifest = receipt.manifest
        version = receipt.version_ref
        with self._transaction() as cursor:
            cursor.execute(
                """
                SELECT current_version FROM lance_datasets
                WHERE project_id = %s AND dataset_id = %s
                FOR UPDATE
                """,
                (manifest.project_id, manifest.dataset_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise KeyError((manifest.project_id, manifest.dataset_id))
            current = int(row[0])
            cursor.execute(
                "SELECT receipt_json FROM lance_dataset_versions WHERE storage_commit_id = %s",
                (receipt.storage_commit_id,),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if _receipt_from_db(existing[0]) != receipt:
                    raise CatalogConflictError("storage commit receipt changed")
                return
            if version.version != current + 1:
                raise DatasetReconciliationRequired(
                    f"cannot index version {version.version}; expected {current + 1}"
                )
            cursor.execute(
                """
                INSERT INTO lance_dataset_versions (
                    project_id, dataset_id, version, schema_snapshot_id,
                    frequency_hz, fingerprint, content_hash, dataset_uri,
                    lance_version, storage_commit_id, rollout_id, source_sha256,
                    converter_version, committed_rollouts, receipt_json, created_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    CAST(%s AS jsonb), CAST(%s AS jsonb), %s
                )
                """,
                (
                    version.project_id,
                    version.dataset_id,
                    version.version,
                    version.schema_snapshot_id,
                    version.frequency_hz,
                    version.schema_fingerprint,
                    version.content_hash,
                    version.dataset_uri,
                    version.lance_version,
                    version.storage_commit_id,
                    manifest.rollout_id,
                    manifest.source_sha256,
                    manifest.converter_version,
                    _dump_json(version.committed_rollouts),
                    receipt.model_dump_json(),
                    version.created_at,
                ),
            )
            cursor.execute(
                """
                INSERT INTO lance_rollout_lineage (
                    project_id, dataset_id, rollout_id, version_added,
                    source_sha256, converter_version, schema_snapshot_id,
                    fingerprint, fragment_uri, fragment_content_hash,
                    step_count, storage_commit_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    manifest.project_id,
                    manifest.dataset_id,
                    manifest.rollout_id,
                    version.version,
                    manifest.source_sha256,
                    manifest.converter_version,
                    manifest.schema_snapshot_id,
                    manifest.schema_fingerprint,
                    manifest.fragment_uri,
                    manifest.content_hash,
                    manifest.step_count,
                    receipt.storage_commit_id,
                ),
            )
            cursor.execute(
                """
                UPDATE lance_datasets SET current_version = %s, updated_at = now()
                WHERE project_id = %s AND dataset_id = %s
                """,
                (version.version, manifest.project_id, manifest.dataset_id),
            )
            cursor.execute(
                "DELETE FROM lance_pending_reconciliation WHERE storage_commit_id = %s",
                (receipt.storage_commit_id,),
            )

    def record_pending(self, pending: PendingReconciliation) -> None:
        with self._transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO lance_pending_reconciliation (
                    storage_commit_id, project_id, dataset_id, dataset_uri,
                    lance_version, last_error, first_seen_at, last_attempt_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (storage_commit_id) DO UPDATE SET
                    last_error = EXCLUDED.last_error,
                    attempt_count = lance_pending_reconciliation.attempt_count + 1,
                    last_attempt_at = now()
                """,
                (
                    pending.storage_commit_id,
                    pending.project_id,
                    pending.dataset_id,
                    pending.dataset_uri,
                    pending.lance_version,
                    pending.error,
                    pending.created_at,
                ),
            )

    def list_versions(self, project_id: str, dataset_id: str) -> tuple[DatasetVersionRef, ...]:
        with self._transaction() as cursor:
            cursor.execute(
                """
                SELECT receipt_json FROM lance_dataset_versions
                WHERE project_id = %s AND dataset_id = %s ORDER BY version
                """,
                (project_id, dataset_id),
            )
            rows = cursor.fetchall()
        return tuple(_receipt_from_db(row[0]).version_ref for row in rows)

    def lineage(
        self, project_id: str, dataset_id: str, rollout_id: str, version: int
    ) -> RolloutLineage:
        with self._transaction() as cursor:
            cursor.execute(
                """
                SELECT receipt_json FROM lance_dataset_versions
                WHERE project_id = %s AND dataset_id = %s AND version = %s
                """,
                (project_id, dataset_id, version),
            )
            version_row = cursor.fetchone()
            cursor.execute(
                """
                SELECT v.receipt_json
                FROM lance_rollout_lineage l
                JOIN lance_dataset_versions v
                  ON v.storage_commit_id = l.storage_commit_id
                WHERE l.project_id = %s AND l.dataset_id = %s
                  AND l.rollout_id = %s AND l.version_added <= %s
                ORDER BY l.version_added DESC LIMIT 1
                """,
                (project_id, dataset_id, rollout_id, version),
            )
            lineage_row = cursor.fetchone()
        if version_row is None or lineage_row is None:
            raise KeyError((project_id, dataset_id, version, rollout_id))
        selected = _receipt_from_db(version_row[0]).version_ref
        source = _receipt_from_db(lineage_row[0]).manifest
        return RolloutLineage(
            project_id=project_id,
            dataset_id=dataset_id,
            dataset_version=version,
            rollout_id=rollout_id,
            source_sha256=source.source_sha256,
            converter_version=source.converter_version,
            schema_snapshot_id=source.schema_snapshot_id,
            schema_fingerprint=source.schema_fingerprint,
            fragment_uri=source.fragment_uri,
            fragment_content_hash=source.content_hash,
            dataset_uri=selected.dataset_uri,
            lance_version=selected.lance_version,
        )
