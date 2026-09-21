"""Measure the unique stored files referenced by an immutable Dataset version.

Raw upload bytes remain a separate fact. Dataset bytes include the Lance files
and camera artifacts actually referenced by the selected revisions, including
shared original videos only once. No episode-count or compression estimates.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from time import monotonic
from typing import Any, Protocol
from urllib.parse import unquote, urlparse

from hc_data_platform.aligned_media.models import AlignedMediaObjectV1
from hc_data_platform.storage.inventory import StorageInventoryProvider

from .models import DatasetPageVersionCapacityFacts

logger = logging.getLogger(__name__)


class DatasetCapacityReader(Protocol):
    def resolve(
        self, facts: DatasetPageVersionCapacityFacts
    ) -> DatasetPageVersionCapacityFacts: ...


@dataclass(frozen=True)
class CapacityReferences:
    lance_versions: tuple[tuple[str, int], ...]
    media_objects: tuple[AlignedMediaObjectV1, ...]


class CapacityReferenceReader(Protocol):
    def load(self, facts: DatasetPageVersionCapacityFacts) -> CapacityReferences: ...


class PostgresCapacityReferences:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def load(self, facts: DatasetPageVersionCapacityFacts) -> CapacityReferences:
        scope = facts.scope
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT DISTINCT stream->'data_binding'->>'lance_version',
                                stream->'aligned_media_binding'->>'artifact_id'
                  FROM dataset_registry.dataset_version_episode_revisions r
                  JOIN dataset_registry.dataset_version_episodes e
                    USING (organization_id, project_id, region_code, dataset_id,
                           version_id, episode_id)
                  CROSS JOIN LATERAL jsonb_array_elements(r.revision_document->'streams') stream
                 WHERE r.organization_id = %s AND r.project_id = %s AND r.region_code = %s
                   AND r.dataset_id = %s AND r.version_id = %s
                   AND e.episode_document->>'included' = 'true'
                   AND r.revision_id = e.episode_document->'selected_revision'->>'revision_id'
                """,
                (
                    scope.organization_id,
                    scope.project_id,
                    scope.region_code,
                    facts.dataset_id,
                    facts.version_id,
                ),
            )
            bindings = cursor.fetchall()
            versions = sorted({int(row[0]) for row in bindings if row[0] is not None})
            artifacts = sorted({str(row[1]) for row in bindings if row[1] is not None})
            if not versions:
                raise ValueError("Version has no immutable storage binding")
            cursor.execute(
                """
                SELECT dataset_uri, lance_version
                  FROM lance_dataset_versions
                 WHERE organization_id = %s AND project_id = %s AND dataset_id = %s
                   AND version = ANY(%s)
                """,
                (scope.organization_id, scope.project_id, facts.dataset_id, versions),
            )
            lance = tuple((str(row[0]), int(row[1])) for row in cursor.fetchall())
            if len(lance) != len(versions):
                raise ValueError("Version storage bindings are incomplete")
            cursor.execute(
                """
                SELECT object_manifest
                  FROM aligned_media.artifacts
                 WHERE organization_id = %s AND project_id = %s AND region_code = %s
                   AND dataset_id = %s AND artifact_id = ANY(%s)
                   AND status = 'READY' AND deleted_at IS NULL
                   AND dataset_committed_at IS NOT NULL
                """,
                (
                    scope.organization_id,
                    scope.project_id,
                    scope.region_code,
                    facts.dataset_id,
                    artifacts,
                ),
            )
            media = cursor.fetchall()
            if len(media) != len(artifacts):
                raise ValueError("Version media bindings are incomplete")
            receipts: list[AlignedMediaObjectV1] = []
            for (manifest,) in media:
                items = json.loads(manifest) if isinstance(manifest, str) else manifest
                receipts.extend(AlignedMediaObjectV1.model_validate(item) for item in items)
            return CapacityReferences(lance, tuple(receipts))
        finally:
            cursor.close()
            connection.close()


class StoredDatasetCapacityReader:
    def __init__(
        self,
        references: CapacityReferenceReader,
        provider: StorageInventoryProvider,
        *,
        bucket: str,
        storage_options: dict[str, str],
        dataset_loader: Callable[..., Any] | None = None,
    ) -> None:
        self._references = references
        self._provider = provider
        self._bucket = bucket
        self._storage_options = storage_options
        self._dataset_loader = dataset_loader
        self._cache: OrderedDict[tuple[str, ...], tuple[float, DatasetPageVersionCapacityFacts]] = (
            OrderedDict()
        )
        self._guard = Lock()

    def _prefix(self, uri: str) -> str:
        parsed = urlparse(uri)
        if parsed.scheme not in {"s3", "oss"} or parsed.netloc != self._bucket:
            raise ValueError("Dataset storage is outside the configured object store")
        key = unquote(parsed.path).strip("/")
        if not key:
            raise ValueError("Dataset storage prefix is empty")
        return key + "/"

    def _measure(self, references: CapacityReferences) -> int:
        if self._dataset_loader is None:
            import lance

            loader = lance.dataset
        else:
            loader = self._dataset_loader
        by_uri: dict[str, set[int]] = defaultdict(set)
        for uri, version in references.lance_versions:
            by_uri[uri].add(version)
        sizes: dict[str, int] = {}
        for uri, versions in by_uri.items():
            prefix = self._prefix(uri)
            dataset = loader(uri, version=max(versions), storage_options=self._storage_options)
            # Listing provides exact object sizes without reading media or rows.
            inventory = {
                item.object_key: item.physical_bytes for item in self._provider.list_prefix(prefix)
            }
            referenced: set[str] = set()
            for batch in dataset.tracked_files(min_version=min(versions)):
                for item in batch.to_pylist():
                    if item["version"] in versions:
                        base = self._prefix(item["base_uri"])
                        referenced.add(base + item["path"])
            if not referenced:
                raise ValueError("Dataset manifest contains no storage files")
            for key in referenced:
                if key in inventory:
                    sizes[key] = inventory[key]
                else:
                    observed = self._provider.head(key)
                    if observed is None:
                        raise ValueError("A referenced Dataset object is missing")
                    sizes[key] = observed.physical_bytes
        expected: dict[str, tuple[int, str]] = {}
        for receipt in references.media_objects:
            identity = (receipt.size, receipt.sha256)
            if receipt.key in expected and expected[receipt.key] != identity:
                raise ValueError("Shared media receipts disagree")
            expected[receipt.key] = identity
        for key, (size, _) in expected.items():
            if key not in sizes:
                observed = self._provider.head(key)
                if observed is None:
                    raise ValueError("A referenced media object is missing")
                sizes[key] = observed.physical_bytes
            if sizes[key] != size:
                raise ValueError("Media object size differs from its receipt")
        return sum(sizes.values())

    def resolve(self, facts: DatasetPageVersionCapacityFacts) -> DatasetPageVersionCapacityFacts:
        if facts.state == "SETTLED" and facts.actual_oss_bytes is not None:
            return facts
        key = (
            facts.scope.organization_id,
            facts.scope.project_id,
            facts.scope.region_code,
            facts.dataset_id,
            facts.version_id,
            facts.basis_revision,
        )
        with self._guard:
            cached = self._cache.get(key)
            if cached is not None and monotonic() - cached[0] < 60:
                self._cache.move_to_end(key)
                return cached[1]
        try:
            size = str(self._measure(self._references.load(facts)))
        except Exception as exc:
            # A missing object or unavailable provider must never become a fake 0 B.
            logger.warning(
                "dataset_capacity_unavailable dataset_id=%s version_id=%s error_type=%s",
                facts.dataset_id,
                facts.version_id,
                type(exc).__name__,
            )
            return facts
        measured = facts.model_copy(
            update={
                "required_physical_bytes": size,
                "actual_oss_bytes": size,
                "state": "SETTLED" if facts.source_bytes is not None else "PARTIAL",
                "calculated_at": datetime.now(timezone.utc),
            }
        )
        with self._guard:
            self._cache[key] = (monotonic(), measured)
            self._cache.move_to_end(key)
            while len(self._cache) > 128:
                self._cache.popitem(last=False)
        return measured
