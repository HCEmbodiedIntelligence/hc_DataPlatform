"""Project-scoped object-store inventory snapshot production.

The catalog supplies tenant ownership and business classification.  The object
store supplies the physical instances and byte sizes.  Keeping those concerns
separate prevents an unscoped bucket listing from assigning another project's
objects to the active project.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, cast
from urllib.parse import quote, unquote, urlparse

from hc_data_platform.aligned_media.models import AlignedMediaObjectV1
from hc_data_platform.security.audit import canonical_hash

from .models import (
    BusinessCapacityCategory,
    CapacityInventoryFact,
    CapacitySnapshot,
    InventoryDisposition,
    ObjectRole,
)
from .service import StorageGovernanceService

_UNKNOWN_PROVIDER_OBSERVED_AT = datetime(1970, 1, 1, tzinfo=timezone.utc)


class InventoryUnavailable(RuntimeError):
    """Raised when a complete, truthful inventory cannot be published."""


@dataclass(frozen=True, slots=True)
class InventoryCatalogEntry:
    identity: str
    object_key: str
    business_category: BusinessCapacityCategory | None
    object_role: ObjectRole
    observed_at: datetime
    expected_bytes: int | None = None
    prefix: bool = False
    priority: int = 0
    disposition: InventoryDisposition = InventoryDisposition.PRIMARY
    required: bool = True


@dataclass(frozen=True, slots=True)
class ProviderInventoryObject:
    object_key: str
    physical_bytes: int
    observed_at: datetime


class StorageInventoryCatalog(Protocol):
    def entries(self, *, project_id: str) -> tuple[InventoryCatalogEntry, ...]: ...


class StorageInventoryProvider(Protocol):
    def head(self, object_key: str) -> ProviderInventoryObject | None: ...

    def list_prefix(self, prefix: str) -> tuple[ProviderInventoryObject, ...]: ...


@dataclass(frozen=True, slots=True)
class _ResolvedObject:
    entry: InventoryCatalogEntry
    provider: ProviderInventoryObject


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _rows(cursor: Any, raw_rows: Sequence[object]) -> tuple[dict[str, object], ...]:
    description = cursor.description
    if description is None:
        raise RuntimeError("inventory catalog query did not describe its columns")
    names = tuple(
        str(column.name if hasattr(column, "name") else column[0]) for column in description
    )
    values: list[dict[str, object]] = []
    for raw in raw_rows:
        if isinstance(raw, Mapping):
            values.append({str(key): value for key, value in raw.items()})
        else:
            values.append(dict(zip(names, cast(Sequence[object], raw), strict=True)))
    return tuple(values)


class PostgresStorageInventoryCatalog:
    """Read only durable object registrations under the bound PostgreSQL scope."""

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        object_store_bucket: str,
        object_store_scheme: str = "s3",
        artifact_prefix: str,
    ) -> None:
        self._connection_factory = connection_factory
        self._bucket = object_store_bucket
        self._scheme = object_store_scheme
        if self._scheme not in {"s3", "oss"}:
            raise ValueError("object-store inventory scheme must be s3 or oss")
        self._artifact_prefix = artifact_prefix.strip("/")

    def entries(self, *, project_id: str) -> tuple[InventoryCatalogEntry, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            entries = [
                *self._managed_entries(cursor, project_id),
                *self._ingest_entries(cursor, project_id),
                *self._publication_entries(cursor, project_id),
                *self._published_export_entries(cursor, project_id),
                *self._aligned_media_entries(cursor, project_id),
                *self._lance_entries(cursor, project_id),
            ]
            return tuple(sorted(entries, key=lambda item: (item.object_key, -item.priority)))
        finally:
            cursor.close()
            connection.close()

    @staticmethod
    def _managed_entries(cursor: Any, project_id: str) -> tuple[InventoryCatalogEntry, ...]:
        cursor.execute(
            """
            SELECT object_id, object_key, physical_bytes, business_category,
                   object_role, updated_at
            FROM storage.managed_objects
            WHERE project_id = %s
            ORDER BY object_id
            """,
            (project_id,),
        )
        return tuple(
            InventoryCatalogEntry(
                identity=f"managed:{row['object_id']}",
                object_key=str(row["object_key"]),
                expected_bytes=int(cast(Any, row["physical_bytes"])),
                business_category=BusinessCapacityCategory(str(row["business_category"])),
                object_role=ObjectRole(str(row["object_role"])),
                observed_at=_aware(cast(datetime, row["updated_at"])),
                priority=100,
            )
            for row in _rows(cursor, cursor.fetchall())
        )

    @staticmethod
    def _ingest_entries(cursor: Any, project_id: str) -> tuple[InventoryCatalogEntry, ...]:
        cursor.execute(
            """
            SELECT rollout_id, object_key, manifest_key, file_size, committed_at
            FROM ingest.rollout_objects
            WHERE project_id = %s AND status = 'COMMITTED'
            ORDER BY rollout_id
            """,
            (project_id,),
        )
        entries: list[InventoryCatalogEntry] = []
        for row in _rows(cursor, cursor.fetchall()):
            observed_at = _aware(cast(datetime, row["committed_at"]))
            identity = f"ingest:{row['rollout_id']}"
            entries.extend(
                (
                    InventoryCatalogEntry(
                        identity=f"{identity}:raw",
                        object_key=str(row["object_key"]),
                        expected_bytes=int(cast(Any, row["file_size"])),
                        business_category=BusinessCapacityCategory.RAW,
                        object_role=ObjectRole.RAW,
                        observed_at=observed_at,
                        priority=60,
                    ),
                    InventoryCatalogEntry(
                        identity=f"{identity}:manifest",
                        object_key=str(row["manifest_key"]),
                        business_category=BusinessCapacityCategory.RAW,
                        object_role=ObjectRole.MANIFEST,
                        observed_at=observed_at,
                        priority=60,
                    ),
                )
            )
        return tuple(entries)

    def _publication_entries(
        self, cursor: Any, project_id: str
    ) -> tuple[InventoryCatalogEntry, ...]:
        cursor.execute(
            """
            SELECT asset.dataset_id, asset.dataset_version, asset.asset_kind,
                   asset.artifact_uri, asset.size_bytes, version.created_at
            FROM publishing.publication_assets AS asset
            JOIN publishing.dataset_versions AS version
              ON version.project_id = asset.project_id
             AND version.dataset_id = asset.dataset_id
             AND version.dataset_version = asset.dataset_version
            WHERE asset.project_id = %s
            ORDER BY asset.dataset_id, asset.dataset_version, asset.asset_kind
            """,
            (project_id,),
        )
        return tuple(
            InventoryCatalogEntry(
                identity=(
                    f"publication:{row['dataset_id']}:{row['dataset_version']}:{row['asset_kind']}"
                ),
                object_key=f"{self._artifact_prefix}/{str(row['artifact_uri']).lstrip('/')}",
                expected_bytes=int(cast(Any, row["size_bytes"])),
                business_category=BusinessCapacityCategory.ANNOTATION_COMPLETE,
                object_role=(
                    ObjectRole.REBUILDABLE_DERIVATIVE
                    if str(row["asset_kind"]) == "ANNOTATIONS_LANCE"
                    else ObjectRole.PUBLISHED_MANIFEST
                ),
                observed_at=_aware(cast(datetime, row["created_at"])),
                priority=80,
            )
            for row in _rows(cursor, cursor.fetchall())
        )

    def _published_export_entries(
        self, cursor: Any, project_id: str
    ) -> tuple[InventoryCatalogEntry, ...]:
        cursor.execute(
            """
            SELECT dataset_id, dataset_version, export_format,
                   artifact_uri, published_at
            FROM publishing.published_exports
            WHERE project_id = %s
            ORDER BY dataset_id, dataset_version, export_format
            """,
            (project_id,),
        )
        return tuple(
            InventoryCatalogEntry(
                identity=(
                    f"published-export:{row['dataset_id']}:{row['dataset_version']}:"
                    f"{row['export_format']}"
                ),
                object_key=f"{self._artifact_prefix}/{str(row['artifact_uri']).lstrip('/')}",
                business_category=BusinessCapacityCategory.ANNOTATION_COMPLETE,
                object_role=ObjectRole.REBUILDABLE_DERIVATIVE,
                observed_at=_aware(cast(datetime, row["published_at"])),
                priority=70,
            )
            for row in _rows(cursor, cursor.fetchall())
        )

    @staticmethod
    def _aligned_media_entries(
        cursor: Any, project_id: str
    ) -> tuple[InventoryCatalogEntry, ...]:
        cursor.execute(
            """
            SELECT artifact_id, dataset_id, dataset_version, object_manifest,
                   dataset_committed_at
              FROM aligned_media.artifacts
             WHERE project_id = %s AND status = 'READY'
               AND dataset_committed_at IS NOT NULL AND deleted_at IS NULL
             ORDER BY dataset_id, dataset_version, artifact_id
            """,
            (project_id,),
        )
        entries: list[InventoryCatalogEntry] = []
        for row in _rows(cursor, cursor.fetchall()):
            raw_manifest = row["object_manifest"]
            if isinstance(raw_manifest, str):
                raw_manifest = json.loads(raw_manifest)
            if not isinstance(raw_manifest, list):
                raise InventoryUnavailable("aligned-media object receipt is invalid")
            observed_at = _aware(cast(datetime, row["dataset_committed_at"]))
            for item in raw_manifest:
                receipt = AlignedMediaObjectV1.model_validate(item)
                entries.append(
                    InventoryCatalogEntry(
                        identity=f"aligned-media:{row['artifact_id']}:{receipt.sha256}",
                        object_key=receipt.key,
                        expected_bytes=receipt.size,
                        business_category=BusinessCapacityCategory.PENDING_ANNOTATION,
                        object_role=ObjectRole.REBUILDABLE_DERIVATIVE,
                        observed_at=observed_at,
                        priority=85,
                    )
                )
        return tuple(entries)

    def _lance_entries(self, cursor: Any, project_id: str) -> tuple[InventoryCatalogEntry, ...]:
        cursor.execute(
            """
            SELECT dataset_id, dataset_uri, updated_at
            FROM lance_datasets
            WHERE project_id = %s AND current_version > 0
            ORDER BY dataset_id
            """,
            (project_id,),
        )
        entries: list[InventoryCatalogEntry] = []
        for row in _rows(cursor, cursor.fetchall()):
            dataset_id = str(row["dataset_id"])
            dataset_uri = str(row["dataset_uri"])
            observed_at = _aware(cast(datetime, row["updated_at"]))
            entries.extend(
                (
                    InventoryCatalogEntry(
                        identity=f"lance:{dataset_id}",
                        object_key=self._object_key(dataset_uri),
                        business_category=BusinessCapacityCategory.PENDING_ANNOTATION,
                        object_role=ObjectRole.REBUILDABLE_DERIVATIVE,
                        observed_at=observed_at,
                        prefix=True,
                        priority=40,
                    ),
                    InventoryCatalogEntry(
                        identity=f"lance-attempt:{dataset_id}",
                        object_key=self._lance_attempt_key(
                            dataset_uri,
                            project_id=project_id,
                            dataset_id=dataset_id,
                        ),
                        business_category=None,
                        object_role=ObjectRole.REBUILDABLE_DERIVATIVE,
                        observed_at=observed_at,
                        prefix=True,
                        priority=90,
                        disposition=InventoryDisposition.TEMPORARY,
                        required=False,
                    ),
                )
            )
        return tuple(entries)

    def _lance_attempt_key(self, uri: str, *, project_id: str, dataset_id: str) -> str:
        parsed = urlparse(uri)
        if parsed.scheme != self._scheme or parsed.netloc != self._bucket:
            raise InventoryUnavailable(
                f"registered dataset URI is outside {self._scheme}://{self._bucket}: {uri}"
            )
        key = parsed.path.lstrip("/")
        marker = f"{quote(project_id, safe='')}/{quote(dataset_id, safe='')}/"
        position = key.find(marker)
        if position < 0:
            raise InventoryUnavailable(
                f"registered dataset URI has no project/dataset prefix: {uri}"
            )
        root = key[:position]
        return unquote(f"{root}_attempts/{marker}")

    def _object_key(self, uri: str) -> str:
        parsed = urlparse(uri)
        key = unquote(parsed.path.lstrip("/"))
        if parsed.scheme != self._scheme or parsed.netloc != self._bucket or not key:
            raise InventoryUnavailable(
                f"registered dataset URI is outside {self._scheme}://{self._bucket}: {uri}"
            )
        return key.rstrip("/") + "/"


class S3StorageInventoryProvider:
    """Provider-side byte evidence for MinIO and other S3-compatible stores."""

    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def head(self, object_key: str) -> ProviderInventoryObject | None:
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=object_key)
        except Exception as exc:
            response = getattr(exc, "response", {})
            status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            code = response.get("Error", {}).get("Code")
            if status == 404 or code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        last_modified = response.get("LastModified")
        return ProviderInventoryObject(
            object_key=object_key,
            physical_bytes=int(response["ContentLength"]),
            observed_at=(
                _aware(last_modified)
                if isinstance(last_modified, datetime)
                else _UNKNOWN_PROVIDER_OBSERVED_AT
            ),
        )

    def list_prefix(self, prefix: str) -> tuple[ProviderInventoryObject, ...]:
        continuation: str | None = None
        found: list[ProviderInventoryObject] = []
        while True:
            arguments: dict[str, object] = {"Bucket": self._bucket, "Prefix": prefix}
            if continuation is not None:
                arguments["ContinuationToken"] = continuation
            response = self._client.list_objects_v2(**arguments)
            for item in response.get("Contents", ()):
                key = str(item["Key"])
                if key.endswith("/"):
                    continue
                last_modified = item.get("LastModified")
                found.append(
                    ProviderInventoryObject(
                        object_key=key,
                        physical_bytes=int(item["Size"]),
                        observed_at=(
                            _aware(last_modified)
                            if isinstance(last_modified, datetime)
                            else _UNKNOWN_PROVIDER_OBSERVED_AT
                        ),
                    )
                )
            if not response.get("IsTruncated", False):
                return tuple(sorted(found, key=lambda item: item.object_key))
            continuation = str(response["NextContinuationToken"])


class StorageInventorySnapshotProducer:
    """Build and publish one deterministic immutable snapshot for a project."""

    def __init__(
        self,
        catalog: StorageInventoryCatalog,
        provider: StorageInventoryProvider,
        service: StorageGovernanceService,
        *,
        provider_name: str = "s3",
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        observation_interval_seconds: int = 3_600,
    ) -> None:
        if observation_interval_seconds < 1:
            raise ValueError("inventory observation interval must be positive")
        self._catalog = catalog
        self._provider = provider
        self._service = service
        self._provider_name = provider_name
        self._clock = clock
        self._observation_interval_seconds = observation_interval_seconds

    def run(self, *, project_id: str) -> CapacitySnapshot:
        entries = self._catalog.entries(project_id=project_id)
        if not entries:
            raise InventoryUnavailable(
                "no project-scoped object registrations exist; capacity remains unknown"
            )

        resolved_by_key: dict[str, _ResolvedObject] = {}
        for entry in entries:
            provider_objects = (
                self._provider.list_prefix(entry.object_key)
                if entry.prefix
                else tuple(filter(None, (self._provider.head(entry.object_key),)))
            )
            if not provider_objects:
                if not entry.required:
                    continue
                raise InventoryUnavailable(
                    f"registered storage object is missing from the provider: {entry.object_key}"
                )
            if (
                not entry.prefix
                and entry.expected_bytes is not None
                and provider_objects[0].physical_bytes != entry.expected_bytes
            ):
                raise InventoryUnavailable(
                    f"registered byte size differs from provider metadata: {entry.object_key}"
                )
            for provider_object in provider_objects:
                current = resolved_by_key.get(provider_object.object_key)
                candidate = _ResolvedObject(entry=entry, provider=provider_object)
                if current is None or entry.priority > current.entry.priority:
                    resolved_by_key[provider_object.object_key] = candidate

        if not resolved_by_key:
            raise InventoryUnavailable("the provider inventory contains no physical objects")

        now = _aware(self._clock())
        epoch_seconds = int(now.timestamp())
        observed_at = datetime.fromtimestamp(
            epoch_seconds - (epoch_seconds % self._observation_interval_seconds),
            tz=timezone.utc,
        )
        digest_input = [
            {
                "object_key": key,
                "physical_bytes": item.provider.physical_bytes,
                "identity": item.entry.identity,
                "business_category": (
                    None
                    if item.entry.business_category is None
                    else item.entry.business_category.value
                ),
                "object_role": item.entry.object_role.value,
                "disposition": item.entry.disposition.value,
                "catalog_observed_at": item.entry.observed_at.isoformat(),
                "provider_observed_at": item.provider.observed_at.isoformat(),
                "observed_at": observed_at.isoformat(),
            }
            for key, item in sorted(resolved_by_key.items())
        ]
        slot = observed_at.strftime("%Y%m%dT%H%M%SZ")
        snapshot_id = f"inventory-{slot}-{canonical_hash(digest_input)[:32]}"
        facts = tuple(
            CapacityInventoryFact(
                snapshot_id=snapshot_id,
                project_id=project_id,
                physical_instance_id=(
                    f"{self._provider_name}:{hashlib.sha256(key.encode()).hexdigest()}"
                ),
                logical_object_id=(
                    f"{item.entry.identity}:{hashlib.sha256(key.encode()).hexdigest()[:24]}"
                ),
                physical_bytes=str(item.provider.physical_bytes),
                disposition=item.entry.disposition,
                business_category=item.entry.business_category,
                object_role=item.entry.object_role,
                observed_at=observed_at,
            )
            for key, item in sorted(resolved_by_key.items())
        )
        return self._service.record_inventory_snapshot(
            project_id=project_id,
            snapshot_id=snapshot_id,
            facts=facts,
        )
