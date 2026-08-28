"""Scoped persistence for P05 dataset-page projections."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from threading import RLock
from typing import Any, Protocol, cast
from uuid import uuid4

from pydantic import TypeAdapter

from hc_data_platform.security.versioning import ResourceVersion

from .models import (
    DatasetPageAsyncJob,
    DatasetPageDetailFacts,
    DatasetPageEpisodeRecord,
    DatasetPageEpisodeRevision,
    DatasetPageEpisodeRevisionHistoryItem,
    DatasetPageManifestEntry,
    DatasetPageOperationalInventoryItem,
    DatasetPageRecord,
    DatasetPageRequiredStorageItem,
    DatasetPageReturnedVersion,
    DatasetPageReviewDecision,
    DatasetPageReviewFinding,
    DatasetPageReviewingVersion,
    DatasetPageRevisionSnapshotReference,
    DatasetPageScope,
    DatasetPageSourceProvenance,
    DatasetPageVersion,
    DatasetPageVersionCapacityFacts,
    DatasetPageVersionContentProjection,
    DatasetPageVersionManifestEntryRecord,
    DatasetPageVersionSchemaDetail,
    DatasetPageVersionSchemaSummary,
    DatasetWorkflowState,
)


@dataclass(frozen=True, slots=True)
class DatasetPageFilters:
    query: str | None = None
    robot_model_id: str | None = None
    robot_id: str | None = None
    collection_task_id: str | None = None
    task: str | None = None
    tag: str | None = None
    scene: str | None = None
    asset_state: str | None = None
    workflow_state: DatasetWorkflowState | None = None
    storage_class: str | None = None
    channels: tuple[str, ...] = ()
    channel_match: str = "all"
    created_from: date | None = None
    created_to: date | None = None


@dataclass(frozen=True, slots=True)
class DatasetPageVersionFilters:
    query: str | None = None
    kind: str | None = None
    status: str | None = None
    include_internal: bool = False


@dataclass(frozen=True, slots=True)
class DatasetPageSourceProvenanceFilters:
    query: str | None = None
    source_id: str | None = None


@dataclass(frozen=True, slots=True)
class DatasetPageEpisodeFilters:
    query: str | None = None
    task: str | None = None
    robot_id: str | None = None
    success_state: str | None = None
    started_from: datetime | None = None
    started_to: datetime | None = None
    included: bool | None = None
    review_statuses: tuple[str, ...] = ()
    has_finding: bool | None = None
    change_types: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DatasetPageAuditEvent:
    project_id: str
    region_code: str
    actor_id: str
    action: str
    resource_id: str
    request_id: str
    outcome: str
    occurred_at: datetime
    before_hash: str | None = None
    after_hash: str | None = None
    details: Mapping[str, object] | None = None


class DatasetPageDuplicateError(RuntimeError):
    pass


class DatasetPagePreconditionError(RuntimeError):
    pass


class DatasetPageRepository(Protocol):
    def has_organization_project(self, *, organization_id: str, project_id: str) -> bool: ...

    def list_records(
        self, *, scope: DatasetPageScope, filters: DatasetPageFilters
    ) -> tuple[DatasetPageRecord, ...]: ...

    def get_record(
        self, *, scope: DatasetPageScope, dataset_id: str
    ) -> DatasetPageRecord | None: ...

    def list_versions(
        self,
        *,
        scope: DatasetPageScope,
        dataset_id: str,
        filters: DatasetPageVersionFilters,
    ) -> tuple[DatasetPageVersion, ...]: ...

    def detail_facts(
        self, *, scope: DatasetPageScope, dataset_id: str
    ) -> DatasetPageDetailFacts | None: ...

    def schema_summary(
        self, *, scope: DatasetPageScope, dataset_id: str, version_id: str
    ) -> DatasetPageVersionSchemaSummary | None: ...

    def list_source_provenance(
        self,
        *,
        scope: DatasetPageScope,
        dataset_id: str,
        version_id: str,
        filters: DatasetPageSourceProvenanceFilters,
    ) -> tuple[DatasetPageSourceProvenance, ...]: ...

    def capacity_facts(
        self, *, scope: DatasetPageScope, dataset_id: str, version_id: str
    ) -> DatasetPageVersionCapacityFacts | None: ...

    def list_episodes(
        self,
        *,
        scope: DatasetPageScope,
        dataset_id: str,
        version_id: str,
        filters: DatasetPageEpisodeFilters,
    ) -> tuple[DatasetPageEpisodeRecord, ...]: ...

    def content_projection(
        self, *, scope: DatasetPageScope, dataset_id: str, version_id: str
    ) -> DatasetPageVersionContentProjection | None: ...

    def episode_revision(
        self, *, scope: DatasetPageScope, dataset_id: str, version_id: str, revision_id: str
    ) -> DatasetPageEpisodeRevision | None: ...

    def list_episode_revision_history(
        self, *, scope: DatasetPageScope, dataset_id: str, episode_id: str
    ) -> tuple[DatasetPageEpisodeRevisionHistoryItem, ...]: ...

    def schema_detail(
        self, *, scope: DatasetPageScope, dataset_id: str, version_id: str
    ) -> DatasetPageVersionSchemaDetail | None: ...

    def list_manifest_entries(
        self, *, scope: DatasetPageScope, dataset_id: str, version_id: str
    ) -> tuple[DatasetPageManifestEntry, ...]: ...

    def list_required_storage(
        self, *, scope: DatasetPageScope, dataset_id: str, version_id: str
    ) -> tuple[DatasetPageRequiredStorageItem, ...]: ...

    def list_operational_inventory(
        self,
        *,
        scope: DatasetPageScope,
        dataset_id: str,
        version_id: str,
        operational_revision: str,
    ) -> tuple[DatasetPageOperationalInventoryItem, ...]: ...

    def get_async_job(
        self, *, scope: DatasetPageScope, job_id: str
    ) -> DatasetPageAsyncJob | None: ...

    def apply_review_approval(
        self,
        *,
        expected_version_etag: str,
        expected_dataset_etag: str,
        previous_version: DatasetPageReviewingVersion,
        next_version: DatasetPageReviewingVersion,
        next_record: DatasetPageRecord,
        decision: DatasetPageReviewDecision,
        job: DatasetPageAsyncJob,
        audit_event: DatasetPageAuditEvent,
    ) -> None: ...

    def apply_review_return(
        self,
        *,
        expected_version_etag: str,
        expected_dataset_etag: str,
        previous_version: DatasetPageReviewingVersion,
        next_version: DatasetPageReturnedVersion,
        next_record: DatasetPageRecord,
        decision: DatasetPageReviewDecision,
        findings: tuple[DatasetPageReviewFinding, ...],
        successor_draft_id: str,
        supersedes_draft_id: str,
        episode_updates: tuple[DatasetPageEpisodeRecord, ...],
        audit_event: DatasetPageAuditEvent,
    ) -> None: ...

    def create_async_job(
        self,
        *,
        scope: DatasetPageScope,
        dataset_id: str,
        version_id: str,
        job: DatasetPageAsyncJob,
        audit_event: DatasetPageAuditEvent,
    ) -> None: ...

    def create_record(
        self, *, record: DatasetPageRecord, audit_event: DatasetPageAuditEvent
    ) -> DatasetPageRecord: ...

    def append_audit(self, event: DatasetPageAuditEvent) -> None: ...


class InMemoryDatasetPageRepository:
    """Thread-safe reference repository with the same scope and uniqueness rules."""

    def __init__(
        self,
        *,
        organization_projects: tuple[tuple[str, str], ...] = (),
        records: tuple[DatasetPageRecord, ...] = (),
        versions: tuple[DatasetPageVersion, ...] = (),
        detail_facts: tuple[DatasetPageDetailFacts, ...] = (),
        schema_summaries: tuple[DatasetPageVersionSchemaSummary, ...] = (),
        source_provenance: tuple[DatasetPageSourceProvenance, ...] = (),
        capacity_facts: tuple[DatasetPageVersionCapacityFacts, ...] = (),
        episodes: tuple[DatasetPageEpisodeRecord, ...] = (),
        content_projections: tuple[DatasetPageVersionContentProjection, ...] = (),
        episode_revisions: tuple[DatasetPageEpisodeRevision, ...] = (),
        schema_details: tuple[DatasetPageVersionSchemaDetail, ...] = (),
        manifest_entries: tuple[DatasetPageVersionManifestEntryRecord, ...] = (),
        required_storage: tuple[DatasetPageRequiredStorageItem, ...] = (),
        operational_inventory: tuple[DatasetPageOperationalInventoryItem, ...] = (),
    ) -> None:
        self._organization_projects = set(organization_projects)
        self._organization_projects.update(
            (record.scope.organization_id, record.scope.project_id) for record in records
        )
        self._records = {_key(record.scope, record.dataset_id): record for record in records}
        self._versions = {
            _version_key(version.scope, version.dataset_id, version.version_id): version
            for version in versions
        }
        self._detail_facts = {_key(fact.scope, fact.dataset_id): fact for fact in detail_facts}
        self._schema_summaries = {
            _version_key(summary.scope, summary.dataset_id, summary.version_id): summary
            for summary in schema_summaries
        }
        self._source_provenance = {
            _provenance_key(item.scope, item.dataset_id, item.version_id, item.provenance_id): item
            for item in source_provenance
        }
        self._capacity_facts = {
            _version_key(fact.scope, fact.dataset_id, fact.version_id): fact
            for fact in capacity_facts
        }
        self._episodes = {
            _episode_key(item.scope, item.dataset_id, item.version_id, item.episode_id): item
            for item in episodes
        }
        self._content_projections = {
            _version_key(item.scope, item.dataset_id, item.version_id): item
            for item in content_projections
        }
        self._episode_revisions = {
            _revision_key(item.scope, item.dataset_id, item.version_id, item.revision_id): item
            for item in episode_revisions
        }
        self._schema_details = {
            _version_key(item.scope, item.dataset_id, item.version_id): item
            for item in schema_details
        }
        self._manifest_entries = {
            _manifest_entry_key(
                item.scope, item.dataset_id, item.version_id, item.entry.entry_id
            ): item.entry
            for item in manifest_entries
        }
        self._required_storage = {
            _storage_key(item.scope, item.dataset_id, item.version_id, item.object_id): item
            for item in required_storage
        }
        self._operational_inventory = {
            _inventory_key(item.scope, item.dataset_id, item.version_id, item.inventory_id): item
            for item in operational_inventory
        }
        self._review_decisions: dict[
            tuple[str, str, str, str, str, str], DatasetPageReviewDecision
        ] = {}
        self._review_findings: dict[
            tuple[str, str, str, str, str, str], DatasetPageReviewFinding
        ] = {}
        self._successor_drafts: dict[tuple[str, str, str, str, str, str], tuple[str, str]] = {}
        self._jobs: dict[str, tuple[DatasetPageScope, DatasetPageAsyncJob]] = {}
        self.audit_events: list[DatasetPageAuditEvent] = []
        self._lock = RLock()

    def has_organization_project(self, *, organization_id: str, project_id: str) -> bool:
        with self._lock:
            return (organization_id, project_id) in self._organization_projects

    def list_records(
        self, *, scope: DatasetPageScope, filters: DatasetPageFilters
    ) -> tuple[DatasetPageRecord, ...]:
        with self._lock:
            return tuple(
                record
                for record in self._records.values()
                if record.scope == scope
                and _matches(
                    record,
                    filters,
                    task_matches=(
                        filters.task is None
                        or (
                            record.metadata.task is not None
                            and filters.task.casefold() in record.metadata.task.casefold()
                        )
                        or any(
                            episode.scope == scope
                            and episode.dataset_id == record.dataset_id
                            and episode.task is not None
                            and filters.task.casefold() in episode.task.casefold()
                            for episode in self._episodes.values()
                        )
                    ),
                )
            )

    def get_record(self, *, scope: DatasetPageScope, dataset_id: str) -> DatasetPageRecord | None:
        with self._lock:
            return self._records.get(_key(scope, dataset_id))

    def list_versions(
        self,
        *,
        scope: DatasetPageScope,
        dataset_id: str,
        filters: DatasetPageVersionFilters,
    ) -> tuple[DatasetPageVersion, ...]:
        with self._lock:
            return tuple(
                version
                for version in self._versions.values()
                if version.scope == scope
                and version.dataset_id == dataset_id
                and (
                    filters.include_internal
                    or not version.version_id.startswith("version_lance_")
                )
                and _matches_version(version, filters)
            )

    def detail_facts(
        self, *, scope: DatasetPageScope, dataset_id: str
    ) -> DatasetPageDetailFacts | None:
        with self._lock:
            return self._detail_facts.get(_key(scope, dataset_id))

    def schema_summary(
        self, *, scope: DatasetPageScope, dataset_id: str, version_id: str
    ) -> DatasetPageVersionSchemaSummary | None:
        with self._lock:
            return self._schema_summaries.get(_version_key(scope, dataset_id, version_id))

    def list_source_provenance(
        self,
        *,
        scope: DatasetPageScope,
        dataset_id: str,
        version_id: str,
        filters: DatasetPageSourceProvenanceFilters,
    ) -> tuple[DatasetPageSourceProvenance, ...]:
        with self._lock:
            return tuple(
                item
                for item in self._source_provenance.values()
                if item.scope == scope
                and item.dataset_id == dataset_id
                and item.version_id == version_id
                and _matches_source_provenance(item, filters)
            )

    def capacity_facts(
        self, *, scope: DatasetPageScope, dataset_id: str, version_id: str
    ) -> DatasetPageVersionCapacityFacts | None:
        with self._lock:
            return self._capacity_facts.get(_version_key(scope, dataset_id, version_id))

    def list_episodes(
        self,
        *,
        scope: DatasetPageScope,
        dataset_id: str,
        version_id: str,
        filters: DatasetPageEpisodeFilters,
    ) -> tuple[DatasetPageEpisodeRecord, ...]:
        with self._lock:
            return tuple(
                item
                for item in self._episodes.values()
                if item.scope == scope
                and item.dataset_id == dataset_id
                and item.version_id == version_id
                and _matches_episode(item, filters)
            )

    def content_projection(
        self, *, scope: DatasetPageScope, dataset_id: str, version_id: str
    ) -> DatasetPageVersionContentProjection | None:
        with self._lock:
            return self._content_projections.get(_version_key(scope, dataset_id, version_id))

    def episode_revision(
        self, *, scope: DatasetPageScope, dataset_id: str, version_id: str, revision_id: str
    ) -> DatasetPageEpisodeRevision | None:
        with self._lock:
            return self._episode_revisions.get(
                _revision_key(scope, dataset_id, version_id, revision_id)
            )

    def list_episode_revision_history(
        self, *, scope: DatasetPageScope, dataset_id: str, episode_id: str
    ) -> tuple[DatasetPageEpisodeRevisionHistoryItem, ...]:
        with self._lock:
            history: list[DatasetPageEpisodeRevisionHistoryItem] = []
            for episode in self._episodes.values():
                if (
                    episode.scope != scope
                    or episode.dataset_id != dataset_id
                    or episode.episode_id != episode_id
                ):
                    continue
                revision = self._episode_revisions.get(
                    _revision_key(
                        scope,
                        dataset_id,
                        episode.version_id,
                        episode.selected_revision.revision_id,
                    )
                )
                version = self._versions.get(_version_key(scope, dataset_id, episode.version_id))
                if revision is None or version is None:
                    raise RuntimeError("episode revision history projection is inconsistent")
                history.append(_episode_revision_history_item(version, revision))
            return tuple(history)

    def schema_detail(
        self, *, scope: DatasetPageScope, dataset_id: str, version_id: str
    ) -> DatasetPageVersionSchemaDetail | None:
        with self._lock:
            return self._schema_details.get(_version_key(scope, dataset_id, version_id))

    def list_manifest_entries(
        self, *, scope: DatasetPageScope, dataset_id: str, version_id: str
    ) -> tuple[DatasetPageManifestEntry, ...]:
        with self._lock:
            prefix = _version_key(scope, dataset_id, version_id)
            return tuple(item for key, item in self._manifest_entries.items() if key[:5] == prefix)

    def list_required_storage(
        self, *, scope: DatasetPageScope, dataset_id: str, version_id: str
    ) -> tuple[DatasetPageRequiredStorageItem, ...]:
        with self._lock:
            return tuple(
                item
                for item in self._required_storage.values()
                if item.scope == scope
                and item.dataset_id == dataset_id
                and item.version_id == version_id
            )

    def list_operational_inventory(
        self,
        *,
        scope: DatasetPageScope,
        dataset_id: str,
        version_id: str,
        operational_revision: str,
    ) -> tuple[DatasetPageOperationalInventoryItem, ...]:
        with self._lock:
            return tuple(
                item
                for item in self._operational_inventory.values()
                if item.scope == scope
                and item.dataset_id == dataset_id
                and item.version_id == version_id
                and item.operational_revision == operational_revision
            )

    def get_async_job(self, *, scope: DatasetPageScope, job_id: str) -> DatasetPageAsyncJob | None:
        with self._lock:
            stored = self._jobs.get(job_id)
            return stored[1] if stored is not None and stored[0] == scope else None

    def apply_review_approval(
        self,
        *,
        expected_version_etag: str,
        expected_dataset_etag: str,
        previous_version: DatasetPageReviewingVersion,
        next_version: DatasetPageReviewingVersion,
        next_record: DatasetPageRecord,
        decision: DatasetPageReviewDecision,
        job: DatasetPageAsyncJob,
        audit_event: DatasetPageAuditEvent,
    ) -> None:
        version_key = _version_key(
            previous_version.scope, previous_version.dataset_id, previous_version.version_id
        )
        record_key = _key(previous_version.scope, previous_version.dataset_id)
        with self._lock:
            current = self._versions.get(version_key)
            record = self._records.get(record_key)
            if (
                current is None
                or current.etag != expected_version_etag
                or record is None
                or record.etag != expected_dataset_etag
            ):
                raise DatasetPagePreconditionError
            self._versions[version_key] = next_version
            self._records[record_key] = next_record
            self._review_decisions[(*version_key, decision.id)] = decision
            self._jobs[job.job_id] = (previous_version.scope, job)
            self.audit_events.append(audit_event)

    def apply_review_return(
        self,
        *,
        expected_version_etag: str,
        expected_dataset_etag: str,
        previous_version: DatasetPageReviewingVersion,
        next_version: DatasetPageReturnedVersion,
        next_record: DatasetPageRecord,
        decision: DatasetPageReviewDecision,
        findings: tuple[DatasetPageReviewFinding, ...],
        successor_draft_id: str,
        supersedes_draft_id: str,
        episode_updates: tuple[DatasetPageEpisodeRecord, ...],
        audit_event: DatasetPageAuditEvent,
    ) -> None:
        version_key = _version_key(
            previous_version.scope, previous_version.dataset_id, previous_version.version_id
        )
        record_key = _key(previous_version.scope, previous_version.dataset_id)
        with self._lock:
            current = self._versions.get(version_key)
            record = self._records.get(record_key)
            if (
                current is None
                or current.etag != expected_version_etag
                or record is None
                or record.etag != expected_dataset_etag
            ):
                raise DatasetPagePreconditionError
            self._versions[version_key] = next_version
            self._records[record_key] = next_record
            self._review_decisions[(*version_key, decision.id)] = decision
            self._successor_drafts[(*version_key, successor_draft_id)] = (
                supersedes_draft_id,
                decision.id,
            )
            for finding in findings:
                self._review_findings[(*version_key, finding.id)] = finding
            for episode in episode_updates:
                self._episodes[
                    _episode_key(
                        episode.scope, episode.dataset_id, episode.version_id, episode.episode_id
                    )
                ] = episode
            self.audit_events.append(audit_event)

    def create_async_job(
        self,
        *,
        scope: DatasetPageScope,
        dataset_id: str,
        version_id: str,
        job: DatasetPageAsyncJob,
        audit_event: DatasetPageAuditEvent,
    ) -> None:
        with self._lock:
            if _version_key(scope, dataset_id, version_id) not in self._versions:
                raise DatasetPagePreconditionError
            if job.job_id in self._jobs:
                raise DatasetPageDuplicateError
            self._jobs[job.job_id] = (scope, job)
            self.audit_events.append(audit_event)

    def create_record(
        self, *, record: DatasetPageRecord, audit_event: DatasetPageAuditEvent
    ) -> DatasetPageRecord:
        key = _key(record.scope, record.dataset_id)
        with self._lock:
            if key in self._records or any(
                candidate.scope == record.scope
                and candidate.name.casefold() == record.name.casefold()
                for candidate in self._records.values()
            ):
                raise DatasetPageDuplicateError
            self._records[key] = record
            self.audit_events.append(audit_event)
        return record

    def append_audit(self, event: DatasetPageAuditEvent) -> None:
        with self._lock:
            self.audit_events.append(event)


class DbApiCursor(Protocol):
    description: Sequence[Sequence[Any]] | None

    def execute(self, query: str, params: Sequence[object] = ()) -> object: ...

    def fetchone(self) -> object | None: ...

    def fetchall(self) -> Sequence[object]: ...

    def close(self) -> None: ...


class DbApiConnection(Protocol):
    def cursor(self) -> DbApiCursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


ConnectionFactory = Callable[[], DbApiConnection]


def _key(scope: DatasetPageScope, dataset_id: str) -> tuple[str, str, str, str]:
    return (scope.organization_id, scope.project_id, scope.region_code, dataset_id)


def _version_key(
    scope: DatasetPageScope, dataset_id: str, version_id: str
) -> tuple[str, str, str, str, str]:
    return (*_key(scope, dataset_id), version_id)


def _provenance_key(
    scope: DatasetPageScope, dataset_id: str, version_id: str, provenance_id: str
) -> tuple[str, str, str, str, str, str]:
    return (*_version_key(scope, dataset_id, version_id), provenance_id)


def _episode_key(
    scope: DatasetPageScope, dataset_id: str, version_id: str, episode_id: str
) -> tuple[str, str, str, str, str, str]:
    return (*_version_key(scope, dataset_id, version_id), episode_id)


def _revision_key(
    scope: DatasetPageScope, dataset_id: str, version_id: str, revision_id: str
) -> tuple[str, str, str, str, str, str]:
    return (*_version_key(scope, dataset_id, version_id), revision_id)


def _manifest_entry_key(
    scope: DatasetPageScope, dataset_id: str, version_id: str, entry_id: str
) -> tuple[str, str, str, str, str, str]:
    return (*_version_key(scope, dataset_id, version_id), entry_id)


def _storage_key(
    scope: DatasetPageScope, dataset_id: str, version_id: str, object_id: str
) -> tuple[str, str, str, str, str, str]:
    return (*_version_key(scope, dataset_id, version_id), object_id)


def _inventory_key(
    scope: DatasetPageScope, dataset_id: str, version_id: str, inventory_id: str
) -> tuple[str, str, str, str, str, str]:
    return (*_version_key(scope, dataset_id, version_id), inventory_id)


def _row(cursor: DbApiCursor, raw: object) -> dict[str, object]:
    if isinstance(raw, Mapping):
        return {str(key): value for key, value in raw.items()}
    if cursor.description is None:
        raise RuntimeError("database cursor did not describe result columns")
    return dict(
        zip(
            (str(column[0]) for column in cursor.description),
            cast(Sequence[object], raw),
            strict=True,
        )
    )


def _decode_json(value: object) -> object:
    return json.loads(value) if isinstance(value, str) else value


def _record(value: object) -> DatasetPageRecord:
    return DatasetPageRecord.model_validate(_decode_json(value))


_VERSION_ADAPTER: TypeAdapter[DatasetPageVersion] = TypeAdapter(DatasetPageVersion)


def _version(value: object) -> DatasetPageVersion:
    return _VERSION_ADAPTER.validate_python(_decode_json(value))


def _detail_facts(value: object) -> DatasetPageDetailFacts:
    return DatasetPageDetailFacts.model_validate(_decode_json(value))


def _schema_summary(value: object) -> DatasetPageVersionSchemaSummary:
    return DatasetPageVersionSchemaSummary.model_validate(_decode_json(value))


def _source_provenance(value: object) -> DatasetPageSourceProvenance:
    return DatasetPageSourceProvenance.model_validate(_decode_json(value))


def _capacity_facts(value: object) -> DatasetPageVersionCapacityFacts:
    return DatasetPageVersionCapacityFacts.model_validate(_decode_json(value))


def _episode(value: object) -> DatasetPageEpisodeRecord:
    return DatasetPageEpisodeRecord.model_validate(_decode_json(value))


def _content_projection(value: object) -> DatasetPageVersionContentProjection:
    return DatasetPageVersionContentProjection.model_validate(_decode_json(value))


def _episode_revision(value: object) -> DatasetPageEpisodeRevision:
    return DatasetPageEpisodeRevision.model_validate(_decode_json(value))


def _episode_revision_history_item(
    version: DatasetPageVersion,
    revision: DatasetPageEpisodeRevision,
) -> DatasetPageEpisodeRevisionHistoryItem:
    if (
        version.scope != revision.scope
        or version.dataset_id != revision.dataset_id
        or version.version_id != revision.version_id
    ):
        raise RuntimeError("episode revision history has mismatched version identity")
    return DatasetPageEpisodeRevisionHistoryItem(
        scope=revision.scope,
        dataset_id=revision.dataset_id,
        episode_id=revision.episode_id,
        version_id=version.version_id,
        display_version=version.display_version,
        version_kind=version.kind,
        version_status=version.status,
        version_created_at=version.created_at,
        version_published_at=(version.published_at if version.status == "READY" else None),
        selected_revision=DatasetPageRevisionSnapshotReference(
            episode_id=revision.episode_id,
            revision_id=revision.revision_id,
            ordinal=revision.ordinal,
            content_sha256=revision.content_sha256,
        ),
    )


def _schema_detail(value: object) -> DatasetPageVersionSchemaDetail:
    return DatasetPageVersionSchemaDetail.model_validate(_decode_json(value))


def _manifest_entry(value: object) -> DatasetPageManifestEntry:
    return DatasetPageManifestEntry.model_validate(_decode_json(value))


def _required_storage(value: object) -> DatasetPageRequiredStorageItem:
    return DatasetPageRequiredStorageItem.model_validate(_decode_json(value))


def _operational_inventory(value: object) -> DatasetPageOperationalInventoryItem:
    return DatasetPageOperationalInventoryItem.model_validate(_decode_json(value))


def _async_job(value: object) -> DatasetPageAsyncJob:
    return DatasetPageAsyncJob.model_validate(_decode_json(value))


def _document(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = cast(Any, value).model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _matches(
    record: DatasetPageRecord,
    filters: DatasetPageFilters,
    *,
    task_matches: bool,
) -> bool:
    metadata = record.metadata
    if filters.query:
        needle = filters.query.casefold()
        if needle not in record.name.casefold() and needle not in record.dataset_id.casefold():
            return False
    if filters.tag:
        needle = filters.tag.casefold()
        if not any(needle in label.casefold() for label in record.labels):
            return False
    values = (
        (filters.robot_model_id, metadata.robot_model_id),
        (filters.robot_id, metadata.robot_id),
        (filters.collection_task_id, metadata.collection_task_id),
        (filters.scene, metadata.scene),
        (filters.asset_state, metadata.asset_state),
        (filters.storage_class, metadata.storage_class),
    )
    if not task_matches or any(
        expected is not None and expected != actual for expected, actual in values
    ):
        return False
    workflow_counts = {
        "pendingReview": record.pending_review_version_count,
        "returned": record.returned_version_count,
        "actionableDraft": record.actionable_draft_count,
    }
    if filters.workflow_state is not None and int(workflow_counts[filters.workflow_state]) <= 0:
        return False
    selected = set(filters.channels)
    channels = set(metadata.channels)
    if selected and (
        (filters.channel_match == "all" and not selected.issubset(channels))
        or (filters.channel_match == "any" and not selected.intersection(channels))
    ):
        return False
    created = record.created_at.date()
    return not (
        (filters.created_from is not None and created < filters.created_from)
        or (filters.created_to is not None and created > filters.created_to)
    )


def _matches_version(version: DatasetPageVersion, filters: DatasetPageVersionFilters) -> bool:
    if filters.query:
        needle = filters.query.casefold()
        if (
            needle not in version.version_id.casefold()
            and needle not in version.display_version.casefold()
        ):
            return False
    return (filters.kind is None or version.kind == filters.kind) and (
        filters.status is None or version.status == filters.status
    )


def _matches_source_provenance(
    item: DatasetPageSourceProvenance, filters: DatasetPageSourceProvenanceFilters
) -> bool:
    if filters.source_id is not None and item.source_id != filters.source_id:
        return False
    if not filters.query:
        return True
    needle = filters.query.casefold()
    return any(
        needle in value.casefold()
        for value in (
            item.provenance_id,
            item.upload_id,
            item.source_id or "",
            item.source_display_name or "",
            item.source_manifest_id,
        )
    )


def _matches_episode(item: DatasetPageEpisodeRecord, filters: DatasetPageEpisodeFilters) -> bool:
    if filters.query:
        needle = filters.query.casefold()
        if not any(
            needle in value.casefold()
            for value in (item.episode_id, item.task or "", item.robot_id or "")
        ):
            return False
    if filters.task is not None and item.task != filters.task:
        return False
    if filters.robot_id is not None and item.robot_id != filters.robot_id:
        return False
    if filters.success_state is not None and item.success_state != filters.success_state:
        return False
    if filters.included is not None and item.included != filters.included:
        return False
    if filters.review_statuses and item.review_status not in filters.review_statuses:
        return False
    if filters.has_finding is not None and item.has_finding != filters.has_finding:
        return False
    if filters.change_types and item.change_type not in filters.change_types:
        return False
    return not (
        (
            filters.started_from is not None
            and (item.started_at is None or item.started_at < filters.started_from)
        )
        or (
            filters.started_to is not None
            and (item.started_at is None or item.started_at > filters.started_to)
        )
    )


class PostgresDatasetPageRepository:
    """RLS-scoped PostgreSQL storage for user-visible dataset facts."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def has_organization_project(self, *, organization_id: str, project_id: str) -> bool:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT 1
                  FROM registry.organization_projects
                 WHERE organization_id = %s AND project_id = %s
                """,
                (organization_id, project_id),
            )
            return cursor.fetchone() is not None
        finally:
            cursor.close()
            connection.close()

    def content_projection(
        self, *, scope: DatasetPageScope, dataset_id: str, version_id: str
    ) -> DatasetPageVersionContentProjection | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT content_document
                  FROM dataset_registry.dataset_version_content_projections
                 WHERE organization_id = %s
                   AND project_id = %s
                   AND region_code = %s
                   AND dataset_id = %s
                   AND version_id = %s
                """,
                (*_version_key(scope, dataset_id, version_id),),
            )
            raw = cursor.fetchone()
            return (
                None if raw is None else _content_projection(_row(cursor, raw)["content_document"])
            )
        finally:
            cursor.close()
            connection.close()

    def episode_revision(
        self, *, scope: DatasetPageScope, dataset_id: str, version_id: str, revision_id: str
    ) -> DatasetPageEpisodeRevision | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT revision_document
                  FROM dataset_registry.dataset_version_episode_revisions
                 WHERE organization_id = %s
                   AND project_id = %s
                   AND region_code = %s
                   AND dataset_id = %s
                   AND version_id = %s
                   AND revision_id = %s
                """,
                (*_revision_key(scope, dataset_id, version_id, revision_id),),
            )
            raw = cursor.fetchone()
            return (
                None if raw is None else _episode_revision(_row(cursor, raw)["revision_document"])
            )
        finally:
            cursor.close()
            connection.close()

    def list_episode_revision_history(
        self, *, scope: DatasetPageScope, dataset_id: str, episode_id: str
    ) -> tuple[DatasetPageEpisodeRevisionHistoryItem, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT version.version_document, revision.revision_document
                  FROM dataset_registry.dataset_version_episode_revisions AS revision
                  JOIN dataset_registry.dataset_version_episodes AS episode
                    ON episode.organization_id = revision.organization_id
                   AND episode.project_id = revision.project_id
                   AND episode.region_code = revision.region_code
                   AND episode.dataset_id = revision.dataset_id
                   AND episode.version_id = revision.version_id
                   AND episode.episode_id = revision.episode_id
                   AND episode.revision_id = revision.revision_id
                  JOIN dataset_registry.dataset_versions AS version
                    ON version.organization_id = revision.organization_id
                   AND version.project_id = revision.project_id
                   AND version.region_code = revision.region_code
                   AND version.dataset_id = revision.dataset_id
                   AND version.version_id = revision.version_id
                 WHERE revision.organization_id = %s
                   AND revision.project_id = %s
                   AND revision.region_code = %s
                   AND revision.dataset_id = %s
                   AND revision.episode_id = %s
                """,
                (*_key(scope, dataset_id), episode_id),
            )
            return tuple(
                _episode_revision_history_item(
                    _version(row["version_document"]),
                    _episode_revision(row["revision_document"]),
                )
                for raw in cursor.fetchall()
                for row in (_row(cursor, raw),)
            )
        finally:
            cursor.close()
            connection.close()

    def schema_detail(
        self, *, scope: DatasetPageScope, dataset_id: str, version_id: str
    ) -> DatasetPageVersionSchemaDetail | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT schema_document
                  FROM dataset_registry.dataset_version_schema_details
                 WHERE organization_id = %s
                   AND project_id = %s
                   AND region_code = %s
                   AND dataset_id = %s
                   AND version_id = %s
                """,
                (*_version_key(scope, dataset_id, version_id),),
            )
            raw = cursor.fetchone()
            return None if raw is None else _schema_detail(_row(cursor, raw)["schema_document"])
        finally:
            cursor.close()
            connection.close()

    def list_manifest_entries(
        self, *, scope: DatasetPageScope, dataset_id: str, version_id: str
    ) -> tuple[DatasetPageManifestEntry, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT entry_document
                  FROM dataset_registry.dataset_version_manifest_entries
                 WHERE organization_id = %s
                   AND project_id = %s
                   AND region_code = %s
                   AND dataset_id = %s
                   AND version_id = %s
                """,
                (*_version_key(scope, dataset_id, version_id),),
            )
            return tuple(
                _manifest_entry(_row(cursor, raw)["entry_document"]) for raw in cursor.fetchall()
            )
        finally:
            cursor.close()
            connection.close()

    def list_required_storage(
        self, *, scope: DatasetPageScope, dataset_id: str, version_id: str
    ) -> tuple[DatasetPageRequiredStorageItem, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT storage_document
                  FROM dataset_registry.dataset_version_required_storage
                 WHERE organization_id = %s
                   AND project_id = %s
                   AND region_code = %s
                   AND dataset_id = %s
                   AND version_id = %s
                """,
                (*_version_key(scope, dataset_id, version_id),),
            )
            return tuple(
                _required_storage(_row(cursor, raw)["storage_document"])
                for raw in cursor.fetchall()
            )
        finally:
            cursor.close()
            connection.close()

    def list_operational_inventory(
        self,
        *,
        scope: DatasetPageScope,
        dataset_id: str,
        version_id: str,
        operational_revision: str,
    ) -> tuple[DatasetPageOperationalInventoryItem, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT inventory_document
                  FROM dataset_registry.dataset_version_operational_inventory
                 WHERE organization_id = %s
                   AND project_id = %s
                   AND region_code = %s
                   AND dataset_id = %s
                   AND version_id = %s
                   AND operational_revision = %s
                """,
                (*_version_key(scope, dataset_id, version_id), operational_revision),
            )
            return tuple(
                _operational_inventory(_row(cursor, raw)["inventory_document"])
                for raw in cursor.fetchall()
            )
        finally:
            cursor.close()
            connection.close()

    def get_async_job(self, *, scope: DatasetPageScope, job_id: str) -> DatasetPageAsyncJob | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT job_document
                  FROM dataset_registry.dataset_version_async_jobs
                 WHERE organization_id = %s
                   AND project_id = %s
                   AND region_code = %s
                   AND job_id = %s
                """,
                (scope.organization_id, scope.project_id, scope.region_code, job_id),
            )
            raw = cursor.fetchone()
            return None if raw is None else _async_job(_row(cursor, raw)["job_document"])
        finally:
            cursor.close()
            connection.close()

    def apply_review_approval(
        self,
        *,
        expected_version_etag: str,
        expected_dataset_etag: str,
        previous_version: DatasetPageReviewingVersion,
        next_version: DatasetPageReviewingVersion,
        next_record: DatasetPageRecord,
        decision: DatasetPageReviewDecision,
        job: DatasetPageAsyncJob,
        audit_event: DatasetPageAuditEvent,
    ) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            self._update_version(
                cursor,
                expected_etag=expected_version_etag,
                previous=previous_version,
                next_version=next_version,
            )
            self._update_record(cursor, expected_etag=expected_dataset_etag, record=next_record)
            self._insert_review_decision(
                cursor,
                scope=previous_version.scope,
                dataset_id=previous_version.dataset_id,
                version_id=previous_version.version_id,
                decision=decision,
            )
            self._insert_async_job(
                cursor,
                scope=previous_version.scope,
                dataset_id=previous_version.dataset_id,
                version_id=previous_version.version_id,
                job=job,
            )
            self._insert_audit(cursor, audit_event)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def apply_review_return(
        self,
        *,
        expected_version_etag: str,
        expected_dataset_etag: str,
        previous_version: DatasetPageReviewingVersion,
        next_version: DatasetPageReturnedVersion,
        next_record: DatasetPageRecord,
        decision: DatasetPageReviewDecision,
        findings: tuple[DatasetPageReviewFinding, ...],
        successor_draft_id: str,
        supersedes_draft_id: str,
        episode_updates: tuple[DatasetPageEpisodeRecord, ...],
        audit_event: DatasetPageAuditEvent,
    ) -> None:
        scope = previous_version.scope
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            self._update_version(
                cursor,
                expected_etag=expected_version_etag,
                previous=previous_version,
                next_version=next_version,
            )
            self._update_record(cursor, expected_etag=expected_dataset_etag, record=next_record)
            self._insert_review_decision(
                cursor,
                scope=scope,
                dataset_id=previous_version.dataset_id,
                version_id=previous_version.version_id,
                decision=decision,
            )
            for finding in findings:
                cursor.execute(
                    """
                    INSERT INTO dataset_registry.dataset_version_review_findings (
                        organization_id, project_id, region_code, dataset_id, version_id,
                        review_finding_id, review_decision_id, output_revision_id,
                        episode_stream_id, severity, created_at, finding_document
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                    )
                    """,
                    (
                        scope.organization_id,
                        scope.project_id,
                        scope.region_code,
                        previous_version.dataset_id,
                        previous_version.version_id,
                        finding.id,
                        decision.id,
                        finding.output_revision_id,
                        finding.episode_stream_id,
                        finding.severity,
                        finding.created_at,
                        _document(finding),
                    ),
                )
            cursor.execute(
                """
                INSERT INTO dataset_registry.dataset_version_successor_drafts (
                    organization_id, project_id, region_code, dataset_id, version_id,
                    successor_draft_id, supersedes_draft_id, review_decision_id, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    scope.organization_id,
                    scope.project_id,
                    scope.region_code,
                    previous_version.dataset_id,
                    previous_version.version_id,
                    successor_draft_id,
                    supersedes_draft_id,
                    decision.id,
                    decision.created_at,
                ),
            )
            for episode in episode_updates:
                cursor.execute(
                    """
                    UPDATE dataset_registry.dataset_version_episodes
                       SET review_status = %s,
                           review_finding_count = %s,
                           has_finding = %s,
                           episode_document = %s::jsonb
                     WHERE organization_id = %s
                       AND project_id = %s
                       AND region_code = %s
                       AND dataset_id = %s
                       AND version_id = %s
                       AND episode_id = %s
                    RETURNING episode_id
                    """,
                    (
                        episode.review_status,
                        int(episode.review_finding_count or "0"),
                        episode.has_finding,
                        _document(episode),
                        scope.organization_id,
                        scope.project_id,
                        scope.region_code,
                        previous_version.dataset_id,
                        previous_version.version_id,
                        episode.episode_id,
                    ),
                )
                if cursor.fetchone() is None:
                    raise DatasetPagePreconditionError
            self._insert_audit(cursor, audit_event)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def create_async_job(
        self,
        *,
        scope: DatasetPageScope,
        dataset_id: str,
        version_id: str,
        job: DatasetPageAsyncJob,
        audit_event: DatasetPageAuditEvent,
    ) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            self._insert_async_job(
                cursor,
                scope=scope,
                dataset_id=dataset_id,
                version_id=version_id,
                job=job,
            )
            self._insert_audit(cursor, audit_event)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def get_record(self, *, scope: DatasetPageScope, dataset_id: str) -> DatasetPageRecord | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT dataset_document
                  FROM dataset_registry.datasets
                 WHERE organization_id = %s
                   AND project_id = %s
                   AND region_code = %s
                   AND dataset_id = %s
                """,
                (scope.organization_id, scope.project_id, scope.region_code, dataset_id),
            )
            raw = cursor.fetchone()
            return None if raw is None else _record(_row(cursor, raw)["dataset_document"])
        finally:
            cursor.close()
            connection.close()

    def list_versions(
        self,
        *,
        scope: DatasetPageScope,
        dataset_id: str,
        filters: DatasetPageVersionFilters,
    ) -> tuple[DatasetPageVersion, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT version_document
                  FROM dataset_registry.dataset_versions
                 WHERE organization_id = %s
                   AND project_id = %s
                   AND region_code = %s
                   AND dataset_id = %s
                   AND (
                        %s::text IS NULL
                        OR version_id ILIKE '%%' || %s || '%%'
                        OR display_version ILIKE '%%' || %s || '%%'
                   )
                   AND (%s::text IS NULL OR version_kind = %s)
                   AND (%s::text IS NULL OR version_status = %s)
                   AND (%s OR version_scope = 'DATASET_RELEASE')
                """,
                (
                    scope.organization_id,
                    scope.project_id,
                    scope.region_code,
                    dataset_id,
                    filters.query,
                    filters.query,
                    filters.query,
                    filters.kind,
                    filters.kind,
                    filters.status,
                    filters.status,
                    filters.include_internal,
                ),
            )
            return tuple(
                _version(_row(cursor, raw)["version_document"]) for raw in cursor.fetchall()
            )
        finally:
            cursor.close()
            connection.close()

    def detail_facts(
        self, *, scope: DatasetPageScope, dataset_id: str
    ) -> DatasetPageDetailFacts | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT fact_document
                  FROM dataset_registry.dataset_detail_facts
                 WHERE organization_id = %s
                   AND project_id = %s
                   AND region_code = %s
                   AND dataset_id = %s
                """,
                (scope.organization_id, scope.project_id, scope.region_code, dataset_id),
            )
            raw = cursor.fetchone()
            return None if raw is None else _detail_facts(_row(cursor, raw)["fact_document"])
        finally:
            cursor.close()
            connection.close()

    def schema_summary(
        self, *, scope: DatasetPageScope, dataset_id: str, version_id: str
    ) -> DatasetPageVersionSchemaSummary | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT schema_document
                  FROM dataset_registry.dataset_version_schema_summaries
                 WHERE organization_id = %s
                   AND project_id = %s
                   AND region_code = %s
                   AND dataset_id = %s
                   AND version_id = %s
                """,
                (
                    scope.organization_id,
                    scope.project_id,
                    scope.region_code,
                    dataset_id,
                    version_id,
                ),
            )
            raw = cursor.fetchone()
            return None if raw is None else _schema_summary(_row(cursor, raw)["schema_document"])
        finally:
            cursor.close()
            connection.close()

    def list_source_provenance(
        self,
        *,
        scope: DatasetPageScope,
        dataset_id: str,
        version_id: str,
        filters: DatasetPageSourceProvenanceFilters,
    ) -> tuple[DatasetPageSourceProvenance, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT provenance_document
                  FROM dataset_registry.dataset_version_source_provenance
                 WHERE organization_id = %s
                   AND project_id = %s
                   AND region_code = %s
                   AND dataset_id = %s
                   AND version_id = %s
                   AND (%s::text IS NULL OR source_id = %s)
                   AND (
                        %s::text IS NULL
                        OR provenance_id ILIKE '%%' || %s || '%%'
                        OR upload_id ILIKE '%%' || %s || '%%'
                        OR COALESCE(source_id, '') ILIKE '%%' || %s || '%%'
                        OR COALESCE(source_display_name, '') ILIKE '%%' || %s || '%%'
                        OR source_manifest_id ILIKE '%%' || %s || '%%'
                   )
                """,
                (
                    scope.organization_id,
                    scope.project_id,
                    scope.region_code,
                    dataset_id,
                    version_id,
                    filters.source_id,
                    filters.source_id,
                    filters.query,
                    filters.query,
                    filters.query,
                    filters.query,
                    filters.query,
                    filters.query,
                ),
            )
            return tuple(
                _source_provenance(_row(cursor, raw)["provenance_document"])
                for raw in cursor.fetchall()
            )
        finally:
            cursor.close()
            connection.close()

    def capacity_facts(
        self, *, scope: DatasetPageScope, dataset_id: str, version_id: str
    ) -> DatasetPageVersionCapacityFacts | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT capacity_document
                  FROM dataset_registry.dataset_version_capacity_facts
                 WHERE organization_id = %s
                   AND project_id = %s
                   AND region_code = %s
                   AND dataset_id = %s
                   AND version_id = %s
                """,
                (
                    scope.organization_id,
                    scope.project_id,
                    scope.region_code,
                    dataset_id,
                    version_id,
                ),
            )
            raw = cursor.fetchone()
            return None if raw is None else _capacity_facts(_row(cursor, raw)["capacity_document"])
        finally:
            cursor.close()
            connection.close()

    def list_episodes(
        self,
        *,
        scope: DatasetPageScope,
        dataset_id: str,
        version_id: str,
        filters: DatasetPageEpisodeFilters,
    ) -> tuple[DatasetPageEpisodeRecord, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT episode_document
                  FROM dataset_registry.dataset_version_episodes
                 WHERE organization_id = %s
                   AND project_id = %s
                   AND region_code = %s
                   AND dataset_id = %s
                   AND version_id = %s
                   AND (
                        %s::text IS NULL
                        OR episode_id ILIKE '%%' || %s || '%%'
                        OR COALESCE(task, '') ILIKE '%%' || %s || '%%'
                        OR COALESCE(robot_id, '') ILIKE '%%' || %s || '%%'
                   )
                   AND (%s::text IS NULL OR task = %s)
                   AND (%s::text IS NULL OR robot_id = %s)
                   AND (%s::text IS NULL OR success_state = %s)
                   AND (%s::timestamptz IS NULL OR started_at >= %s::timestamptz)
                   AND (%s::timestamptz IS NULL OR started_at <= %s::timestamptz)
                   AND (%s::boolean IS NULL OR included = %s)
                   AND (cardinality(%s::text[]) = 0 OR review_status = ANY(%s::text[]))
                   AND (%s::boolean IS NULL OR has_finding = %s)
                   AND (cardinality(%s::text[]) = 0 OR change_type = ANY(%s::text[]))
                """,
                (
                    scope.organization_id,
                    scope.project_id,
                    scope.region_code,
                    dataset_id,
                    version_id,
                    filters.query,
                    filters.query,
                    filters.query,
                    filters.query,
                    filters.task,
                    filters.task,
                    filters.robot_id,
                    filters.robot_id,
                    filters.success_state,
                    filters.success_state,
                    filters.started_from,
                    filters.started_from,
                    filters.started_to,
                    filters.started_to,
                    filters.included,
                    filters.included,
                    list(filters.review_statuses),
                    list(filters.review_statuses),
                    filters.has_finding,
                    filters.has_finding,
                    list(filters.change_types),
                    list(filters.change_types),
                ),
            )
            return tuple(
                _episode(_row(cursor, raw)["episode_document"]) for raw in cursor.fetchall()
            )
        finally:
            cursor.close()
            connection.close()

    def list_records(
        self, *, scope: DatasetPageScope, filters: DatasetPageFilters
    ) -> tuple[DatasetPageRecord, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT dataset.dataset_document
                  FROM dataset_registry.datasets AS dataset
                 WHERE dataset.organization_id = %s
                   AND dataset.project_id = %s
                   AND dataset.region_code = %s
                   AND (
                        %s::text IS NULL
                        OR name ILIKE '%%' || %s || '%%'
                        OR dataset_id ILIKE '%%' || %s || '%%'
                   )
                   AND (
                        %s::text IS NULL
                        OR EXISTS (
                            SELECT 1
                              FROM jsonb_array_elements_text(dataset.labels) AS dataset_label(value)
                             WHERE STRPOS(LOWER(dataset_label.value), LOWER(%s)) > 0
                        )
                   )
                   AND (%s::text IS NULL OR robot_model_id = %s)
                   AND (%s::text IS NULL OR robot_id = %s)
                   AND (
                        %s::text IS NULL
                        OR dataset_registry.dataset_has_collection_task(
                            dataset.organization_id,
                            dataset.project_id,
                            dataset.dataset_id,
                            %s
                        )
                   )
                   AND (
                        %s::text IS NULL
                        OR STRPOS(LOWER(COALESCE(dataset.task, '')), LOWER(%s)) > 0
                        OR EXISTS (
                            SELECT 1
                              FROM dataset_registry.dataset_version_episodes AS episode
                             WHERE episode.organization_id = dataset.organization_id
                               AND episode.project_id = dataset.project_id
                               AND episode.region_code = dataset.region_code
                               AND episode.dataset_id = dataset.dataset_id
                               AND STRPOS(LOWER(COALESCE(episode.task, '')), LOWER(%s)) > 0
                        )
                   )
                   AND (%s::text IS NULL OR scene = %s)
                   AND (%s::text IS NULL OR asset_state = %s)
                   AND (
                        %s::text IS NULL
                        OR CASE %s
                            WHEN 'pendingReview' THEN pending_review_version_count > 0
                            WHEN 'returned' THEN returned_version_count > 0
                            WHEN 'actionableDraft' THEN actionable_draft_count > 0
                            ELSE FALSE
                           END
                   )
                   AND (%s::text IS NULL OR storage_class = %s)
                   AND (
                        cardinality(%s::text[]) = 0
                        OR CASE WHEN %s = 'all'
                            THEN channels @> %s::jsonb
                            ELSE channels ?| %s::text[]
                           END
                   )
                   AND (%s::date IS NULL OR created_at >= %s::date)
                   AND (
                        %s::date IS NULL
                        OR created_at < (%s::date + INTERVAL '1 day')
                   )
                """,
                (
                    scope.organization_id,
                    scope.project_id,
                    scope.region_code,
                    filters.query,
                    filters.query,
                    filters.query,
                    filters.tag,
                    filters.tag,
                    filters.robot_model_id,
                    filters.robot_model_id,
                    filters.robot_id,
                    filters.robot_id,
                    filters.collection_task_id,
                    filters.collection_task_id,
                    filters.task,
                    filters.task,
                    filters.task,
                    filters.scene,
                    filters.scene,
                    filters.asset_state,
                    filters.asset_state,
                    filters.workflow_state,
                    filters.workflow_state,
                    filters.storage_class,
                    filters.storage_class,
                    list(filters.channels),
                    filters.channel_match,
                    json.dumps(list(filters.channels)),
                    list(filters.channels),
                    filters.created_from,
                    filters.created_from,
                    filters.created_to,
                    filters.created_to,
                ),
            )
            return tuple(
                _record(_row(cursor, raw)["dataset_document"]) for raw in cursor.fetchall()
            )
        finally:
            cursor.close()
            connection.close()

    def create_record(
        self, *, record: DatasetPageRecord, audit_event: DatasetPageAuditEvent
    ) -> DatasetPageRecord:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            metadata = record.metadata
            cursor.execute(
                """
                INSERT INTO dataset_registry.datasets (
                    organization_id, project_id, region_code, dataset_id, folder_path,
                    name, description,
                    labels, availability, owner_id, owner_display_name, robot_model_id, robot_id,
                    collection_task_id, task, scene, asset_state, storage_class, channels,
                    episode_count,
                    pending_review_version_count, returned_version_count, actionable_draft_count,
                    version, dataset_document, created_at, updated_at, activity_at
                ) VALUES (
                    %s, %s, %s, %s, %s::text[], %s, %s, %s::jsonb,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s::jsonb, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s
                ) ON CONFLICT DO NOTHING
                RETURNING dataset_id
                """,
                (
                    record.scope.organization_id,
                    record.scope.project_id,
                    record.scope.region_code,
                    record.dataset_id,
                    list(record.folder_path),
                    record.name,
                    record.description,
                    _document(list(record.labels)),
                    record.availability,
                    record.owner.id,
                    record.owner.display_name,
                    metadata.robot_model_id,
                    metadata.robot_id,
                    metadata.collection_task_id,
                    metadata.task,
                    metadata.scene,
                    metadata.asset_state,
                    metadata.storage_class,
                    _document(list(metadata.channels)),
                    int(record.episode_count),
                    int(record.pending_review_version_count),
                    int(record.returned_version_count),
                    int(record.actionable_draft_count),
                    ResourceVersion.from_etag(record.etag).value,
                    _document(record),
                    record.created_at,
                    record.updated_at,
                    record.activity_at,
                ),
            )
            if cursor.fetchone() is None:
                raise DatasetPageDuplicateError
            self._insert_audit(cursor, audit_event)
            connection.commit()
            return record
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def append_audit(self, event: DatasetPageAuditEvent) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            self._insert_audit(cursor, event)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    @staticmethod
    def _update_version(
        cursor: DbApiCursor,
        *,
        expected_etag: str,
        previous: DatasetPageReviewingVersion,
        next_version: DatasetPageVersion,
    ) -> None:
        cursor.execute(
            """
            UPDATE dataset_registry.dataset_versions
               SET version_kind = %s,
                   version_status = %s,
                   created_at = %s,
                   published_at = %s,
                   version_document = %s::jsonb
             WHERE organization_id = %s
               AND project_id = %s
               AND region_code = %s
               AND dataset_id = %s
               AND version_id = %s
               AND version_document ->> 'etag' = %s
            RETURNING version_id
            """,
            (
                next_version.kind,
                next_version.status,
                next_version.created_at,
                next_version.published_at if hasattr(next_version, "published_at") else None,
                _document(next_version),
                previous.scope.organization_id,
                previous.scope.project_id,
                previous.scope.region_code,
                previous.dataset_id,
                previous.version_id,
                expected_etag,
            ),
        )
        if cursor.fetchone() is None:
            raise DatasetPagePreconditionError

    @staticmethod
    def _update_record(
        cursor: DbApiCursor,
        *,
        expected_etag: str,
        record: DatasetPageRecord,
    ) -> None:
        cursor.execute(
            """
            UPDATE dataset_registry.datasets
               SET episode_count = %s,
                   pending_review_version_count = %s,
                   returned_version_count = %s,
                   actionable_draft_count = %s,
                   version = version + 1,
                   dataset_document = %s::jsonb,
                   updated_at = %s,
                   activity_at = %s
             WHERE organization_id = %s
               AND project_id = %s
               AND region_code = %s
               AND dataset_id = %s
               AND dataset_document ->> 'etag' = %s
            RETURNING dataset_id
            """,
            (
                int(record.episode_count),
                int(record.pending_review_version_count),
                int(record.returned_version_count),
                int(record.actionable_draft_count),
                _document(record),
                record.updated_at,
                record.activity_at,
                record.scope.organization_id,
                record.scope.project_id,
                record.scope.region_code,
                record.dataset_id,
                expected_etag,
            ),
        )
        if cursor.fetchone() is None:
            raise DatasetPagePreconditionError

    @staticmethod
    def _insert_review_decision(
        cursor: DbApiCursor,
        *,
        scope: DatasetPageScope,
        dataset_id: str,
        version_id: str,
        decision: DatasetPageReviewDecision,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO dataset_registry.dataset_version_review_decisions (
                organization_id, project_id, region_code, dataset_id, version_id,
                review_decision_id, decision, created_at, decision_document
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                scope.organization_id,
                scope.project_id,
                scope.region_code,
                dataset_id,
                version_id,
                decision.id,
                decision.decision,
                decision.created_at,
                _document(decision),
            ),
        )

    @staticmethod
    def _insert_async_job(
        cursor: DbApiCursor,
        *,
        scope: DatasetPageScope,
        dataset_id: str,
        version_id: str,
        job: DatasetPageAsyncJob,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO dataset_registry.dataset_version_async_jobs (
                organization_id, project_id, region_code, dataset_id, version_id,
                job_id, job_type, job_status, resource_version, created_at, updated_at,
                job_document
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
            )
            """,
            (
                scope.organization_id,
                scope.project_id,
                scope.region_code,
                dataset_id,
                version_id,
                job.job_id,
                job.job_type,
                job.status,
                int(job.resource_version),
                job.created_at,
                job.updated_at,
                _document(job),
            ),
        )

    @staticmethod
    def _insert_audit(cursor: DbApiCursor, event: DatasetPageAuditEvent) -> None:
        cursor.execute(
            """
            INSERT INTO core.audit_events (
                audit_id, project_id, region_code, actor_id, action, resource_type,
                resource_id, request_id, before_hash, after_hash, details, occurred_at
            ) VALUES (%s, %s, %s, %s, %s, 'DATASET', %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                str(uuid4()),
                event.project_id,
                event.region_code,
                event.actor_id,
                event.action,
                event.resource_id,
                event.request_id,
                event.before_hash,
                event.after_hash,
                _document({"outcome": event.outcome, **(event.details or {})}),
                event.occurred_at,
            ),
        )
