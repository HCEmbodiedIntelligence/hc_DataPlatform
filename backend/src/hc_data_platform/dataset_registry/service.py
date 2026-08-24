"""Application service for the P05 dataset page aggregate."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, TypeVar
from uuid import NAMESPACE_URL, uuid5

from hc_data_platform.core.errors import problem
from hc_data_platform.core.etag import make_etag
from hc_data_platform.core.pagination import CursorCodec
from hc_data_platform.security.audit import canonical_hash
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.idempotency import IdempotencyStore, InMemoryIdempotencyStore
from hc_data_platform.security.scope import ScopeGuard
from hc_data_platform.security.versioning import ResourceVersion

from .models import (
    CreateDatasetCommand,
    DatasetPageAllowedAction,
    DatasetPageApproveReviewCommand,
    DatasetPageApproveReviewMutationRecord,
    DatasetPageApproveReviewOutputVersion,
    DatasetPageAsyncJob,
    DatasetPageAsyncJobMutationRecord,
    DatasetPageBlockedReason,
    DatasetPageBootstrapData,
    DatasetPageBootstrapEnvelope,
    DatasetPageCapabilities,
    DatasetPageCapabilitiesEnvelope,
    DatasetPageCurrentReadyVersion,
    DatasetPageDataset,
    DatasetPageDatasetEnvelope,
    DatasetPageDeletionAsyncImpact,
    DatasetPageDeletionCheck,
    DatasetPageDeletionPreflight,
    DatasetPageDeletionPreflightCommand,
    DatasetPageDetailFacts,
    DatasetPageDetailSummary,
    DatasetPageDiffJobCommand,
    DatasetPageEpisodeListEnvelope,
    DatasetPageEpisodeListItem,
    DatasetPageEpisodeRecord,
    DatasetPageEpisodeRevisionEnvelope,
    DatasetPageEpisodeRevisionHistoryEnvelope,
    DatasetPageFacets,
    DatasetPageFacetsEnvelope,
    DatasetPageFacetValue,
    DatasetPageInfo,
    DatasetPageListEnvelope,
    DatasetPageListItem,
    DatasetPageMeta,
    DatasetPageMetadata,
    DatasetPageMutationRecord,
    DatasetPageOperationalInventoryEnvelope,
    DatasetPageRecord,
    DatasetPageRequiredStorageEnvelope,
    DatasetPageReturnedVersion,
    DatasetPageReturnReviewCommand,
    DatasetPageReturnReviewMutationRecord,
    DatasetPageReviewChecksCommand,
    DatasetPageReviewChecksData,
    DatasetPageReviewChecksEnvelope,
    DatasetPageReviewCheckTarget,
    DatasetPageReviewDecision,
    DatasetPageReviewFinding,
    DatasetPageReviewFindingCatalog,
    DatasetPageReviewFindingType,
    DatasetPageReviewingVersion,
    DatasetPageReviewSeverity,
    DatasetPageScope,
    DatasetPageSourceProvenance,
    DatasetPageSourceProvenanceListEnvelope,
    DatasetPageSummary,
    DatasetPageSummaryEnvelope,
    DatasetPageVersion,
    DatasetPageVersionBootstrapData,
    DatasetPageVersionBootstrapEnvelope,
    DatasetPageVersionCapacityEnvelope,
    DatasetPageVersionContentProjection,
    DatasetPageVersionListEnvelope,
    DatasetPageVersionManifestEnvelope,
    DatasetPageVersionSchema,
    DatasetPageVersionSchemaEnvelope,
    DatasetPageVersionSchemaSummaryEnvelope,
)
from .repository import (
    DatasetPageAuditEvent,
    DatasetPageDuplicateError,
    DatasetPageEpisodeFilters,
    DatasetPageFilters,
    DatasetPagePreconditionError,
    DatasetPageRepository,
    DatasetPageSourceProvenanceFilters,
    DatasetPageVersionFilters,
    InMemoryDatasetPageRepository,
)

Clock = Callable[[], datetime]
DatasetSort = Literal[
    "activity_at:desc,dataset_id:desc",
    "created_at:desc,dataset_id:desc",
    "name:asc,dataset_id:asc",
]
DatasetVersionSort = Literal[
    "created_at:desc,version_id:desc",
    "created_at:asc,version_id:asc",
    "display_version:desc,version_id:desc",
    "display_version:asc,version_id:asc",
]
DatasetSourceProvenanceSort = Literal[
    "registered_at:desc,provenance_id:desc",
    "registered_at:asc,provenance_id:asc",
    "source_display_name:asc,provenance_id:asc",
]
DatasetEpisodeSort = Literal[
    "ordinal:asc,episode_id:asc",
    "started_at_ns:desc,episode_id:desc",
    "started_at_ns:asc,episode_id:asc",
]
ProjectionRecord = TypeVar("ProjectionRecord")


@dataclass(frozen=True, slots=True)
class DatasetPageMutationOutcome:
    record: DatasetPageMutationRecord
    replayed: bool


@dataclass(frozen=True, slots=True)
class DatasetPageApproveReviewOutcome:
    record: DatasetPageApproveReviewMutationRecord
    replayed: bool


@dataclass(frozen=True, slots=True)
class DatasetPageReturnReviewOutcome:
    record: DatasetPageReturnReviewMutationRecord
    replayed: bool


@dataclass(frozen=True, slots=True)
class DatasetPageAsyncJobOutcome:
    record: DatasetPageAsyncJobMutationRecord
    replayed: bool


class DatasetPageService:
    def __init__(
        self,
        repository: DatasetPageRepository,
        *,
        cursor_secret: str = "dataset-page-local-cursor-secret",
        idempotency: IdempotencyStore | None = None,
        clock: Clock = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._repository = repository
        self._cursor = CursorCodec(cursor_secret)
        self._idempotency = idempotency or InMemoryIdempotencyStore()
        self._clock = clock

    @classmethod
    def in_memory(cls) -> DatasetPageService:
        return cls(InMemoryDatasetPageRepository())

    def list_datasets(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        filters: DatasetPageFilters,
        sort: DatasetSort,
        after: str | None,
        before: str | None,
        limit: int,
        request_id: str,
    ) -> DatasetPageListEnvelope:
        scope = self._authorize_read(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        normalized = _validated_filters(filters)
        if after is not None and before is not None:
            raise problem(
                status=422,
                code="CURSOR_DIRECTION_CONFLICT",
                title="Invalid pagination request",
                detail="Use either after or before, not both.",
            )
        if limit not in {20, 50, 100}:
            raise problem(
                status=422,
                code="PAGE_LIMIT_INVALID",
                title="Invalid page size",
                detail="Page size must be one of 20, 50, or 100.",
            )
        records = self._sort(self._repository.list_records(scope=scope, filters=normalized), sort)
        visible, page_info = self._page(
            records,
            scope=scope,
            filters=normalized,
            sort=sort,
            after=after,
            before=before,
            limit=limit,
        )
        now = self._clock()
        self._audit(
            auth=auth,
            scope=scope,
            action="dataset.listed",
            resource_id=project_id,
            request_id=request_id,
            outcome="SUCCEEDED",
            details={"result_count": len(visible)},
        )
        return DatasetPageListEnvelope(
            items=tuple(self._list_item(record, auth) for record in visible),
            page_info=page_info,
            snapshot_at=now,
            snapshot_id=_snapshot_id(records, normalized),
            scope=scope,
            request_id=request_id,
        )

    def summary(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        filters: DatasetPageFilters,
        request_id: str,
    ) -> DatasetPageSummaryEnvelope:
        scope = self._authorize_read(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        normalized = _validated_filters(filters)
        records = self._repository.list_records(scope=scope, filters=normalized)
        now = self._clock()
        self._audit(
            auth=auth,
            scope=scope,
            action="dataset.summary_viewed",
            resource_id=project_id,
            request_id=request_id,
            outcome="SUCCEEDED",
        )
        return DatasetPageSummaryEnvelope(
            data=DatasetPageSummary(
                scope=scope,
                dataset_count=str(len(records)),
                episode_count=str(sum(int(record.episode_count) for record in records)),
                pending_review_version_count=str(
                    sum(int(record.pending_review_version_count) for record in records)
                ),
                returned_version_count=str(
                    sum(int(record.returned_version_count) for record in records)
                ),
                actionable_draft_count=str(
                    sum(int(record.actionable_draft_count) for record in records)
                ),
                normalized_filters=_filters_document(normalized),
            ),
            meta=self._meta(request_id=request_id, now=now),
        )

    def facets(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        filters: DatasetPageFilters,
        request_id: str,
    ) -> DatasetPageFacetsEnvelope:
        scope = self._authorize_read(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        normalized = _validated_filters(filters)
        records = self._repository.list_records(scope=scope, filters=normalized)
        now = self._clock()
        self._audit(
            auth=auth,
            scope=scope,
            action="dataset.facets_viewed",
            resource_id=project_id,
            request_id=request_id,
            outcome="SUCCEEDED",
        )
        return DatasetPageFacetsEnvelope(
            data=DatasetPageFacets(
                scope=scope,
                normalized_filters=_filters_document(normalized),
                robots=_facets(record.metadata.robot_id for record in records),
                robot_models=_facets(record.metadata.robot_model_id for record in records),
                tasks=_facets(record.metadata.task for record in records),
                scenes=_facets(record.metadata.scene for record in records),
                asset_states=_facets(record.metadata.asset_state for record in records),
                storage_classes=_facets(record.metadata.storage_class for record in records),
                channels=_facets(
                    channel for record in records for channel in record.metadata.channels
                ),
            ),
            meta=self._meta(request_id=request_id, now=now),
        )

    def capabilities(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        request_id: str,
    ) -> DatasetPageCapabilitiesEnvelope:
        scope = self._authorize_read(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        now = self._clock()
        can_create = _can_create(auth, project_id)
        self._audit(
            auth=auth,
            scope=scope,
            action="dataset.page_capabilities_viewed",
            resource_id=project_id,
            request_id=request_id,
            outcome="SUCCEEDED",
        )
        return DatasetPageCapabilitiesEnvelope(
            data=DatasetPageCapabilities(
                scope=scope,
                authorization_revision=str(auth.capability_revision),
                allowed_actions=("CREATE_DATASET",) if can_create else (),
                blocked_reasons=(
                    ()
                    if can_create
                    else (
                        DatasetPageBlockedReason(
                            code="DATASET_CREATE_CAPABILITY_REQUIRED",
                            message="The current scope cannot create datasets.",
                        ),
                    )
                ),
            ),
            meta=self._meta(request_id=request_id, now=now),
        )

    def create_dataset(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        command: CreateDatasetCommand,
        idempotency_key: str,
        request_id: str,
    ) -> DatasetPageMutationOutcome:
        scope = self._authorize_create(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        payload = command.model_dump(mode="json")

        def action() -> DatasetPageMutationRecord:
            now = self._clock()
            dataset_id = _dataset_id(scope, idempotency_key)
            labels = tuple(
                dict.fromkeys(label.strip() for label in command.labels if label.strip())
            )
            record = DatasetPageRecord(
                scope=scope,
                dataset_id=dataset_id,
                name=command.name.strip(),
                description=command.description,
                labels=labels,
                availability="ACTIVE",
                owner={"id": auth.subject_id, "display_name": auth.subject_id},
                created_at=now,
                updated_at=now,
                activity_at=now,
                etag=ResourceVersion(1).etag,
                metadata=DatasetPageMetadata(
                    asset_state="EMPTY",
                    storage_class="STANDARD",
                ),
            )
            audit = DatasetPageAuditEvent(
                project_id=scope.project_id,
                region_code=scope.region_code,
                actor_id=auth.subject_id,
                action="dataset.created",
                resource_id=dataset_id,
                request_id=request_id,
                outcome="SUCCEEDED",
                occurred_at=now,
                after_hash=canonical_hash(record.model_dump(mode="json")),
                details={"label_count": len(labels), "version_created": False},
            )
            try:
                saved = self._repository.create_record(record=record, audit_event=audit)
            except DatasetPageDuplicateError as exc:
                raise problem(
                    status=409,
                    code="DATASET_CONFLICT",
                    title="Dataset already exists",
                    detail="A dataset with this name already exists in the selected scope.",
                ) from exc
            return DatasetPageMutationRecord(dataset=saved)

        result = self._idempotency.execute(
            scope=project_id,
            key=f"dataset-page:create:{idempotency_key}",
            payload=payload,
            action=action,
        )
        return DatasetPageMutationOutcome(record=result.value, replayed=result.replayed)

    def bootstrap(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        dataset_id: str,
        request_id: str,
    ) -> DatasetPageBootstrapEnvelope:
        scope = self._authorize_read(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        record = self._required_dataset(scope=scope, dataset_id=dataset_id)
        versions = self._repository.list_versions(
            scope=scope,
            dataset_id=dataset_id,
            filters=DatasetPageVersionFilters(),
        )
        facts = self._repository.detail_facts(scope=scope, dataset_id=dataset_id)
        now = self._clock()
        self._audit(
            auth=auth,
            scope=scope,
            action="dataset.bootstrap_viewed",
            resource_id=dataset_id,
            request_id=request_id,
            outcome="SUCCEEDED",
            details={"version_count": len(versions), "has_detail_facts": facts is not None},
        )
        return DatasetPageBootstrapEnvelope(
            data=DatasetPageBootstrapData(
                scope=scope,
                dataset=self._dataset(record, auth),
                current_ready_version=self._current_ready_version(versions),
                suggested_version_id=self._suggested_version_id(versions),
                summary=self._detail_summary(record=record, facts=facts),
            ),
            meta=self._meta(request_id=request_id, now=now),
        )

    def list_versions(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        dataset_id: str,
        filters: DatasetPageVersionFilters,
        sort: DatasetVersionSort,
        after: str | None,
        before: str | None,
        limit: int,
        request_id: str,
    ) -> DatasetPageVersionListEnvelope:
        scope = self._authorize_dataset_version_read(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        self._required_dataset(scope=scope, dataset_id=dataset_id)
        normalized = _validated_version_filters(filters)
        _require_page_limit(limit, allowed={10, 20, 50})
        records = self._sort_versions(
            self._repository.list_versions(
                scope=scope,
                dataset_id=dataset_id,
                filters=normalized,
            ),
            sort,
        )
        visible, page_info = self._projection_page(
            records,
            kind="p06-versions",
            scope=scope,
            filters=_version_filters_document(normalized),
            sort=sort,
            after=after,
            before=before,
            limit=limit,
            identifier=lambda item: item.version_id,
        )
        now = self._clock()
        self._audit(
            auth=auth,
            scope=scope,
            action="dataset.versions_listed",
            resource_id=dataset_id,
            request_id=request_id,
            outcome="SUCCEEDED",
            details={"result_count": len(visible)},
        )
        return DatasetPageVersionListEnvelope(
            items=visible,
            page_info=page_info,
            snapshot_at=now,
            snapshot_id=_projection_snapshot_id(
                kind="p06-versions",
                filters=_version_filters_document(normalized),
                identifiers=tuple(f"{item.version_id}:{item.etag}" for item in records),
            ),
            scope=scope,
            request_id=request_id,
        )

    def schema_summary(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        dataset_id: str,
        version_id: str,
        request_id: str,
    ) -> DatasetPageVersionSchemaSummaryEnvelope:
        scope = self._authorize_schema_read(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        self._required_version(scope=scope, dataset_id=dataset_id, version_id=version_id)
        summary = self._repository.schema_summary(
            scope=scope,
            dataset_id=dataset_id,
            version_id=version_id,
        )
        if summary is None:
            raise problem(
                status=404,
                code="VERSION_SCHEMA_SUMMARY_NOT_FOUND",
                title="Version schema summary not found",
                detail="No schema summary has been recorded for this immutable version.",
            )
        now = self._clock()
        self._audit(
            auth=auth,
            scope=scope,
            action="dataset.version_schema_summary_viewed",
            resource_id=version_id,
            request_id=request_id,
            outcome="SUCCEEDED",
        )
        return DatasetPageVersionSchemaSummaryEnvelope(
            data=summary,
            meta=self._meta(request_id=request_id, now=now),
        )

    def list_source_provenance(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        dataset_id: str,
        version_id: str,
        filters: DatasetPageSourceProvenanceFilters,
        sort: DatasetSourceProvenanceSort,
        after: str | None,
        before: str | None,
        limit: int,
        request_id: str,
    ) -> DatasetPageSourceProvenanceListEnvelope:
        scope = self._authorize_dataset_version_read(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        self._required_version(scope=scope, dataset_id=dataset_id, version_id=version_id)
        normalized = _validated_source_provenance_filters(filters)
        _require_page_limit(limit, allowed={10, 20, 50})
        records = self._sort_source_provenance(
            self._repository.list_source_provenance(
                scope=scope,
                dataset_id=dataset_id,
                version_id=version_id,
                filters=normalized,
            ),
            sort,
        )
        visible, page_info = self._projection_page(
            records,
            kind="p06-source-provenance",
            scope=scope,
            filters=_source_provenance_filters_document(normalized),
            sort=sort,
            after=after,
            before=before,
            limit=limit,
            identifier=lambda item: item.provenance_id,
        )
        now = self._clock()
        self._audit(
            auth=auth,
            scope=scope,
            action="dataset.version_source_provenance_listed",
            resource_id=version_id,
            request_id=request_id,
            outcome="SUCCEEDED",
            details={"result_count": len(visible)},
        )
        return DatasetPageSourceProvenanceListEnvelope(
            items=visible,
            page_info=page_info,
            snapshot_at=now,
            snapshot_id=_projection_snapshot_id(
                kind="p06-source-provenance",
                filters=_source_provenance_filters_document(normalized),
                identifiers=tuple(
                    f"{item.provenance_id}:{item.registered_at.isoformat()}" for item in records
                ),
            ),
            scope=scope,
            request_id=request_id,
        )

    def capacity(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        dataset_id: str,
        version_id: str,
        request_id: str,
    ) -> DatasetPageVersionCapacityEnvelope:
        scope = self._authorize_storage_overview_read(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        self._required_version(scope=scope, dataset_id=dataset_id, version_id=version_id)
        facts = self._repository.capacity_facts(
            scope=scope,
            dataset_id=dataset_id,
            version_id=version_id,
        )
        if facts is None:
            raise problem(
                status=404,
                code="VERSION_CAPACITY_FACTS_NOT_FOUND",
                title="Version capacity facts not found",
                detail="No capacity calculation has been recorded for this immutable version.",
            )
        now = self._clock()
        self._audit(
            auth=auth,
            scope=scope,
            action="dataset.version_capacity_viewed",
            resource_id=version_id,
            request_id=request_id,
            outcome="SUCCEEDED",
        )
        return DatasetPageVersionCapacityEnvelope(
            data=facts,
            meta=self._meta(request_id=request_id, now=now),
        )

    def list_episodes(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        dataset_id: str,
        version_id: str,
        filters: DatasetPageEpisodeFilters,
        sort: DatasetEpisodeSort,
        after: str | None,
        before: str | None,
        limit: int,
        snapshot_token: str | None = None,
        request_id: str,
    ) -> DatasetPageEpisodeListEnvelope:
        scope = self._authorize_episode_read(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        version = self._required_version(scope=scope, dataset_id=dataset_id, version_id=version_id)
        if snapshot_token is not None:
            projection = self._required_content_projection(
                scope=scope,
                dataset_id=dataset_id,
                version_id=version_id,
            )
            self._require_snapshot_token(
                scope=scope,
                version=version,
                projection=projection,
                snapshot_token=snapshot_token,
            )
        normalized = _validated_episode_filters(filters)
        _require_page_limit(limit, allowed={10, 20, 50, 100})
        records = self._sort_episodes(
            self._repository.list_episodes(
                scope=scope,
                dataset_id=dataset_id,
                version_id=version_id,
                filters=normalized,
            ),
            sort,
        )
        visible, page_info = self._projection_page(
            records,
            kind="p06-episodes",
            scope=scope,
            filters=_episode_filters_document(normalized),
            sort=sort,
            after=after,
            before=before,
            limit=limit,
            identifier=lambda item: item.episode_id,
        )
        now = self._clock()
        self._audit(
            auth=auth,
            scope=scope,
            action="dataset.version_episodes_listed",
            resource_id=version_id,
            request_id=request_id,
            outcome="SUCCEEDED",
            details={"result_count": len(visible)},
        )
        return DatasetPageEpisodeListEnvelope(
            items=tuple(_public_episode(item) for item in visible),
            page_info=page_info,
            snapshot_at=now,
            snapshot_id=_projection_snapshot_id(
                kind="p06-episodes",
                filters=_episode_filters_document(normalized),
                identifiers=tuple(
                    f"{item.episode_id}:{item.selected_revision.revision_id}" for item in records
                ),
            ),
            scope=scope,
            request_id=request_id,
        )

    def version_bootstrap(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        dataset_id: str,
        version_id: str,
        request_id: str,
    ) -> DatasetPageVersionBootstrapEnvelope:
        scope = self._authorize_dataset_version_read(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        version = self._required_version(scope=scope, dataset_id=dataset_id, version_id=version_id)
        projection = self._required_content_projection(
            scope=scope,
            dataset_id=dataset_id,
            version_id=version_id,
        )
        now = self._clock()
        self._audit(
            auth=auth,
            scope=scope,
            action="dataset.version_bootstrap_viewed",
            resource_id=version_id,
            request_id=request_id,
            outcome="SUCCEEDED",
            details={"content_snapshot_id": projection.content_snapshot.content_snapshot_id},
        )
        return DatasetPageVersionBootstrapEnvelope(
            data=DatasetPageVersionBootstrapData(
                scope=scope,
                dataset_id=dataset_id,
                version_id=version_id,
                snapshot_token=self._snapshot_token(
                    scope=scope, version=version, projection=projection
                ),
                operational_revision=projection.operational_revision,
                version=version,
            ),
            meta=self._meta(request_id=request_id, now=now),
        )

    def episode_revision(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        dataset_id: str,
        version_id: str,
        revision_id: str,
        snapshot_token: str,
        request_id: str,
    ) -> DatasetPageEpisodeRevisionEnvelope:
        scope = self._authorize_episode_read(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        version = self._required_version(scope=scope, dataset_id=dataset_id, version_id=version_id)
        projection = self._required_content_projection(
            scope=scope,
            dataset_id=dataset_id,
            version_id=version_id,
        )
        self._require_snapshot_token(
            scope=scope,
            version=version,
            projection=projection,
            snapshot_token=snapshot_token,
        )
        revision = self._repository.episode_revision(
            scope=scope,
            dataset_id=dataset_id,
            version_id=version_id,
            revision_id=revision_id,
        )
        if revision is None:
            raise problem(
                status=404,
                code="EPISODE_REVISION_NOT_FOUND",
                title="Episode revision not found",
                detail="The selected revision is not part of this immutable version snapshot.",
            )
        now = self._clock()
        self._audit(
            auth=auth,
            scope=scope,
            action="dataset.episode_revision_viewed",
            resource_id=revision_id,
            request_id=request_id,
            outcome="SUCCEEDED",
        )
        return DatasetPageEpisodeRevisionEnvelope(
            data=revision,
            meta=self._meta(request_id=request_id, now=now),
        )

    def episode_revision_history(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        dataset_id: str,
        episode_id: str,
        after: str | None,
        before: str | None,
        limit: int,
        request_id: str,
    ) -> DatasetPageEpisodeRevisionHistoryEnvelope:
        scope = self._authorize_episode_read(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        self._required_dataset(scope=scope, dataset_id=dataset_id)
        _require_page_limit(limit, allowed={10, 20, 50})
        records = tuple(
            sorted(
                self._repository.list_episode_revision_history(
                    scope=scope,
                    dataset_id=dataset_id,
                    episode_id=episode_id,
                ),
                key=lambda item: (
                    item.version_created_at,
                    item.version_id,
                    item.selected_revision.revision_id,
                ),
                reverse=True,
            )
        )
        filters: dict[str, object] = {"dataset_id": dataset_id, "episode_id": episode_id}
        visible, page_info = self._projection_page(
            records,
            kind="p07-episode-revision-history",
            scope=scope,
            filters=filters,
            sort="version_created_at:desc,version_id:desc,revision_id:desc",
            after=after,
            before=before,
            limit=limit,
            identifier=lambda item: f"{item.version_id}:{item.selected_revision.revision_id}",
        )
        now = self._clock()
        self._audit(
            auth=auth,
            scope=scope,
            action="dataset.episode_revision_history_viewed",
            resource_id=episode_id,
            request_id=request_id,
            outcome="SUCCEEDED",
            details={"result_count": len(visible)},
        )
        return DatasetPageEpisodeRevisionHistoryEnvelope(
            items=visible,
            page_info=page_info,
            snapshot_at=now,
            snapshot_id=_projection_snapshot_id(
                kind="p07-episode-revision-history",
                filters=filters,
                identifiers=tuple(
                    f"{item.version_id}:{item.selected_revision.revision_id}:"
                    f"{item.selected_revision.content_sha256}"
                    for item in records
                ),
            ),
            scope=scope,
            request_id=request_id,
        )

    def manifest(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        dataset_id: str,
        version_id: str,
        after: str | None,
        before: str | None,
        limit: int,
        request_id: str,
    ) -> DatasetPageVersionManifestEnvelope:
        scope = self._authorize_dataset_version_read(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        self._required_version(scope=scope, dataset_id=dataset_id, version_id=version_id)
        projection = self._required_content_projection(
            scope=scope,
            dataset_id=dataset_id,
            version_id=version_id,
        )
        _require_page_limit(limit, allowed={20, 50, 100})
        records = tuple(
            sorted(
                self._repository.list_manifest_entries(
                    scope=scope,
                    dataset_id=dataset_id,
                    version_id=version_id,
                ),
                key=lambda item: item.entry_id,
            )
        )
        if int(projection.manifest.entry_count) != len(records):
            raise problem(
                status=500,
                code="VERSION_MANIFEST_INCONSISTENT",
                title="Version manifest facts are inconsistent",
                detail="The durable manifest summary does not match its entry projection.",
            )
        visible, page_info = self._projection_page(
            records,
            kind="p07-manifest",
            scope=scope,
            filters={"content_snapshot_id": projection.content_snapshot.content_snapshot_id},
            sort="entry_id:asc",
            after=after,
            before=before,
            limit=limit,
            identifier=lambda item: item.entry_id,
        )
        now = self._clock()
        self._audit(
            auth=auth,
            scope=scope,
            action="dataset.version_manifest_viewed",
            resource_id=version_id,
            request_id=request_id,
            outcome="SUCCEEDED",
            details={"result_count": len(visible)},
        )
        return DatasetPageVersionManifestEnvelope(
            items=visible,
            page_info=page_info,
            snapshot_at=now,
            snapshot_id=projection.content_snapshot.content_snapshot_id,
            scope=scope,
            request_id=request_id,
            dataset_id=dataset_id,
            version_id=version_id,
            content_snapshot_id=projection.content_snapshot.content_snapshot_id,
            manifest=projection.manifest,
        )

    def version_schema(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        dataset_id: str,
        version_id: str,
        snapshot_token: str,
        request_id: str,
    ) -> DatasetPageVersionSchemaEnvelope:
        scope = self._authorize_dataset_version_read(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        version = self._required_version(scope=scope, dataset_id=dataset_id, version_id=version_id)
        projection = self._required_content_projection(
            scope=scope,
            dataset_id=dataset_id,
            version_id=version_id,
        )
        self._require_snapshot_token(
            scope=scope,
            version=version,
            projection=projection,
            snapshot_token=snapshot_token,
        )
        detail = self._repository.schema_detail(
            scope=scope,
            dataset_id=dataset_id,
            version_id=version_id,
        )
        if detail is None:
            raise problem(
                status=404,
                code="VERSION_SCHEMA_NOT_FOUND",
                title="Version schema not found",
                detail="No fixed schema channel projection has been recorded for this version.",
            )
        if int(detail.channel_count) != len(detail.channels):
            raise problem(
                status=500,
                code="VERSION_SCHEMA_INCONSISTENT",
                title="Version schema facts are inconsistent",
                detail="The durable schema count does not match its channel projection.",
            )
        now = self._clock()
        self._audit(
            auth=auth,
            scope=scope,
            action="dataset.version_schema_viewed",
            resource_id=version_id,
            request_id=request_id,
            outcome="SUCCEEDED",
        )
        return DatasetPageVersionSchemaEnvelope(
            data=DatasetPageVersionSchema(
                **detail.model_dump(),
                snapshot_token=snapshot_token,
            ),
            meta=self._meta(request_id=request_id, now=now),
        )

    def required_storage(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        dataset_id: str,
        version_id: str,
        snapshot_token: str,
        after: str | None,
        before: str | None,
        limit: int,
        request_id: str,
    ) -> DatasetPageRequiredStorageEnvelope:
        scope = self._authorize_dataset_version_read(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        version = self._required_version(scope=scope, dataset_id=dataset_id, version_id=version_id)
        projection = self._required_content_projection(
            scope=scope,
            dataset_id=dataset_id,
            version_id=version_id,
        )
        self._require_snapshot_token(
            scope=scope,
            version=version,
            projection=projection,
            snapshot_token=snapshot_token,
        )
        _require_page_limit(limit, allowed={20, 50, 100})
        records = tuple(
            sorted(
                self._repository.list_required_storage(
                    scope=scope,
                    dataset_id=dataset_id,
                    version_id=version_id,
                ),
                key=lambda item: (item.role, item.object_id),
            )
        )
        visible, page_info = self._projection_page(
            records,
            kind="p07-required-storage",
            scope=scope,
            filters={"snapshot_token": snapshot_token},
            sort="role:asc,object_id:asc",
            after=after,
            before=before,
            limit=limit,
            identifier=lambda item: item.object_id,
        )
        now = self._clock()
        self._audit(
            auth=auth,
            scope=scope,
            action="dataset.version_required_storage_viewed",
            resource_id=version_id,
            request_id=request_id,
            outcome="SUCCEEDED",
            details={"result_count": len(visible)},
        )
        return DatasetPageRequiredStorageEnvelope(
            items=visible,
            page_info=page_info,
            snapshot_at=now,
            snapshot_id=projection.content_snapshot.content_snapshot_id,
            scope=scope,
            request_id=request_id,
        )

    def operational_inventory(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        dataset_id: str,
        version_id: str,
        operational_revision: str,
        after: str | None,
        before: str | None,
        limit: int,
        request_id: str,
    ) -> DatasetPageOperationalInventoryEnvelope:
        scope = self._authorize_dataset_version_read(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        self._required_version(scope=scope, dataset_id=dataset_id, version_id=version_id)
        projection = self._required_content_projection(
            scope=scope,
            dataset_id=dataset_id,
            version_id=version_id,
        )
        if operational_revision != projection.operational_revision:
            raise problem(
                status=409,
                code="OPERATIONAL_REVISION_STALE",
                title="Operational revision is stale",
                detail="Reload the fixed-version bootstrap before viewing operational inventory.",
            )
        _require_page_limit(limit, allowed={20, 50, 100})
        records = tuple(
            sorted(
                self._repository.list_operational_inventory(
                    scope=scope,
                    dataset_id=dataset_id,
                    version_id=version_id,
                    operational_revision=operational_revision,
                ),
                key=lambda item: (item.created_at, item.inventory_id),
                reverse=True,
            )
        )
        visible, page_info = self._projection_page(
            records,
            kind="p07-operational-inventory",
            scope=scope,
            filters={"operational_revision": operational_revision},
            sort="created_at:desc,inventory_id:desc",
            after=after,
            before=before,
            limit=limit,
            identifier=lambda item: item.inventory_id,
        )
        now = self._clock()
        self._audit(
            auth=auth,
            scope=scope,
            action="dataset.version_operational_inventory_viewed",
            resource_id=version_id,
            request_id=request_id,
            outcome="SUCCEEDED",
            details={"result_count": len(visible)},
        )
        return DatasetPageOperationalInventoryEnvelope(
            items=visible,
            page_info=page_info,
            snapshot_at=now,
            snapshot_id=operational_revision,
            scope=scope,
            request_id=request_id,
        )

    def get_async_job(
        self,
        *,
        auth: AuthContext,
        organization_id: str | None,
        project_id: str | None,
        region_code: str | None,
        job_id: str,
    ) -> DatasetPageAsyncJob | None:
        if organization_id is None or project_id is None or region_code is None:
            return None
        scope = self._authorize_dataset_version_read(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        return self._repository.get_async_job(scope=scope, job_id=job_id)

    def review_checks(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        dataset_id: str,
        version_id: str,
        command: DatasetPageReviewChecksCommand,
        if_match: str,
        request_id: str,
    ) -> DatasetPageReviewChecksEnvelope:
        scope = self._authorize_review(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        version = self._reviewing_version(scope=scope, dataset_id=dataset_id, version_id=version_id)
        self._require_version_etag(version=version, if_match=if_match)
        if command.expected_status != version.status:
            raise problem(
                status=409,
                code="REVIEW_STATUS_STALE",
                title="Review status changed",
                detail="Reload the fixed version before starting review checks.",
            )
        blockers, targets = self._review_prerequisites(
            scope=scope,
            dataset_id=dataset_id,
            version=version,
        )
        if not targets:
            raise problem(
                status=409,
                code="REVIEW_TARGETS_UNAVAILABLE",
                title="Review targets are unavailable",
                detail="No durable revision and stream targets are available for this version.",
            )
        now = self._clock()
        catalog = _review_catalog()
        expires_at = now + timedelta(minutes=10)
        token = self._review_token(
            scope=scope,
            version=version,
            catalog=catalog,
            expires_at=expires_at,
        )
        self._audit(
            auth=auth,
            scope=scope,
            action="dataset.version_review_checked",
            resource_id=version_id,
            request_id=request_id,
            outcome="SUCCEEDED",
            details={"blocker_count": len(blockers), "target_count": len(targets)},
        )
        return DatasetPageReviewChecksEnvelope(
            data=DatasetPageReviewChecksData(
                scope=scope,
                dataset_id=dataset_id,
                output_version_id=version_id,
                source_draft_id=version.source_draft_id,
                review_token=token,
                review_token_expires_at=expires_at,
                version_token=version.version_token,
                blockers=blockers,
                finding_catalog=catalog,
                eligible_targets=targets,
            ),
            meta=self._meta(request_id=request_id, now=now),
        )

    def approve_review(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        dataset_id: str,
        version_id: str,
        command: DatasetPageApproveReviewCommand,
        if_match: str,
        idempotency_key: str,
        request_id: str,
    ) -> DatasetPageApproveReviewOutcome:
        scope = self._authorize_review(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        self._authorize_publish(auth=auth, project_id=project_id, region_code=region_code)
        catalog = _review_catalog()
        payload = {
            "command": command.model_dump(mode="json"),
            "if_match": if_match,
            "version_id": version_id,
        }

        def action() -> DatasetPageApproveReviewMutationRecord:
            version = self._reviewing_version(
                scope=scope,
                dataset_id=dataset_id,
                version_id=version_id,
            )
            record = self._required_dataset(scope=scope, dataset_id=dataset_id)
            self._require_version_etag(version=version, if_match=if_match)
            self._require_review_token(
                scope=scope,
                version=version,
                review_token=command.review_token,
                catalog=catalog,
            )
            blockers, targets = self._review_prerequisites(
                scope=scope,
                dataset_id=dataset_id,
                version=version,
            )
            if not targets or blockers:
                raise problem(
                    status=409,
                    code="REVIEW_BLOCKED",
                    title="Version review is blocked",
                    detail="Resolve the current review blockers and run review checks again.",
                    details={"blocker_count": len(blockers)},
                )
            projection = self._required_content_projection(
                scope=scope,
                dataset_id=dataset_id,
                version_id=version_id,
            )
            now = self._clock()
            decision = DatasetPageReviewDecision(
                id=_review_decision_id(
                    scope=scope,
                    version_id=version_id,
                    idempotency_key=idempotency_key,
                    decision="approved",
                ),
                output_version_id=version_id,
                decision="APPROVED",
                created_at=now,
            )
            next_version = version.model_copy(
                update={
                    "etag": _next_etag(version.etag, "approved", decision.id),
                    "version_token": _next_version_token(
                        version.version_token,
                        "approved",
                        decision.id,
                    ),
                    "delivery_status": "CANDIDATE_READY",
                    "approved_review_decision_id": decision.id,
                }
            )
            next_record = record.model_copy(
                update={
                    "etag": _next_etag(record.etag, "review-approved", decision.id),
                    "updated_at": now,
                    "activity_at": now,
                }
            )
            job = DatasetPageAsyncJob(
                job_id=_dataset_job_id(
                    scope=scope,
                    dataset_id=dataset_id,
                    version_id=version_id,
                    idempotency_key=idempotency_key,
                    kind="manifest-materialization",
                ),
                job_type="MANIFEST_MATERIALIZATION",
                status="SUCCEEDED",
                resource_type="DATASET_VERSION",
                resource_id=version_id,
                progress={"completed_steps": "1", "total_steps": "1"},
                result_ref={
                    "state": "CANDIDATE_READY",
                    "manifest_id": projection.manifest.manifest_id,
                },
                created_at=now,
                updated_at=now,
                resource_version="1",
            )
            audit = DatasetPageAuditEvent(
                project_id=scope.project_id,
                region_code=scope.region_code,
                actor_id=auth.subject_id,
                action="dataset.version_review_approved",
                resource_id=version_id,
                request_id=request_id,
                outcome="SUCCEEDED",
                occurred_at=now,
                before_hash=canonical_hash(version.model_dump(mode="json")),
                after_hash=canonical_hash(next_version.model_dump(mode="json")),
                details={"review_decision_id": decision.id, "job_id": job.job_id},
            )
            try:
                self._repository.apply_review_approval(
                    expected_version_etag=version.etag,
                    expected_dataset_etag=record.etag,
                    previous_version=version,
                    next_version=next_version,
                    next_record=next_record,
                    decision=decision,
                    job=job,
                    audit_event=audit,
                )
            except DatasetPagePreconditionError as exc:
                raise _version_precondition_failed() from exc
            return DatasetPageApproveReviewMutationRecord(
                scope=scope,
                review_decision=decision,
                output_version=DatasetPageApproveReviewOutputVersion(
                    id=version_id,
                    version_token=next_version.version_token,
                ),
                job=job,
            )

        result = self._idempotency.execute(
            scope=project_id,
            key=f"dataset-version:approve:{dataset_id}:{version_id}:{idempotency_key}",
            payload=payload,
            action=action,
        )
        return DatasetPageApproveReviewOutcome(
            record=result.value,
            replayed=result.replayed,
        )

    def return_review(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        dataset_id: str,
        version_id: str,
        command: DatasetPageReturnReviewCommand,
        if_match: str,
        idempotency_key: str,
        request_id: str,
    ) -> DatasetPageReturnReviewOutcome:
        scope = self._authorize_review(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        catalog = _review_catalog()
        payload = {
            "command": command.model_dump(mode="json"),
            "if_match": if_match,
            "version_id": version_id,
        }

        def action() -> DatasetPageReturnReviewMutationRecord:
            version = self._reviewing_version(
                scope=scope,
                dataset_id=dataset_id,
                version_id=version_id,
            )
            record = self._required_dataset(scope=scope, dataset_id=dataset_id)
            self._require_version_etag(version=version, if_match=if_match)
            self._require_review_token(
                scope=scope,
                version=version,
                review_token=command.review_token,
                catalog=catalog,
            )
            _blockers, targets = self._review_prerequisites(
                scope=scope,
                dataset_id=dataset_id,
                version=version,
            )
            if not targets:
                raise problem(
                    status=409,
                    code="REVIEW_TARGETS_UNAVAILABLE",
                    title="Review targets are unavailable",
                    detail="No durable revision and stream targets are available for this version.",
                )
            if command.finding_catalog_version != catalog.version:
                raise problem(
                    status=409,
                    code="FINDING_CATALOG_STALE",
                    title="Finding catalog changed",
                    detail="Run review checks again before returning this version.",
                )
            self._validate_return_findings(command=command, catalog=catalog, targets=targets)
            now = self._clock()
            decision = DatasetPageReviewDecision(
                id=_review_decision_id(
                    scope=scope,
                    version_id=version_id,
                    idempotency_key=idempotency_key,
                    decision="returned",
                ),
                output_version_id=version_id,
                decision="RETURNED",
                created_at=now,
            )
            findings = tuple(
                DatasetPageReviewFinding(
                    id=_review_finding_id(decision.id, index),
                    output_revision_id=finding.output_revision_id,
                    episode_stream_id=finding.episode_stream_id,
                    start_ns=finding.start_ns,
                    end_ns=finding.end_ns,
                    finding_type=finding.finding_type,
                    severity=finding.severity,
                    note=finding.note.strip(),
                    created_at=now,
                )
                for index, finding in enumerate(command.findings)
            )
            successor_draft_id = _successor_draft_id(decision.id)
            next_version = DatasetPageReturnedVersion(
                scope=scope,
                dataset_id=dataset_id,
                version_id=version_id,
                display_version=version.display_version,
                kind=version.kind,
                created_at=version.created_at,
                etag=_next_etag(version.etag, "returned", decision.id),
                version_token=_next_version_token(version.version_token, "returned", decision.id),
                source_draft_id=version.source_draft_id,
                review_decision_id=decision.id,
                review_finding_ids=tuple(finding.id for finding in findings),
                successor_draft_id=successor_draft_id,
                supersedes_draft_id=version.source_draft_id,
                returned_from_version_id=version_id,
                returned_from_review_decision_id=decision.id,
                allowed_actions=(DatasetPageAllowedAction(action="OPEN_VERSION", allowed=True),),
            )
            next_record = record.model_copy(
                update={
                    "etag": _next_etag(record.etag, "review-returned", decision.id),
                    "updated_at": now,
                    "activity_at": now,
                    "pending_review_version_count": str(
                        max(0, int(record.pending_review_version_count) - 1)
                    ),
                    "returned_version_count": str(int(record.returned_version_count) + 1),
                    "actionable_draft_count": str(int(record.actionable_draft_count) + 1),
                }
            )
            episode_updates = self._returned_episode_updates(
                scope=scope,
                dataset_id=dataset_id,
                version_id=version_id,
                findings=findings,
            )
            audit = DatasetPageAuditEvent(
                project_id=scope.project_id,
                region_code=scope.region_code,
                actor_id=auth.subject_id,
                action="dataset.version_review_returned",
                resource_id=version_id,
                request_id=request_id,
                outcome="SUCCEEDED",
                occurred_at=now,
                before_hash=canonical_hash(version.model_dump(mode="json")),
                after_hash=canonical_hash(next_version.model_dump(mode="json")),
                details={"review_decision_id": decision.id, "finding_count": len(findings)},
            )
            try:
                self._repository.apply_review_return(
                    expected_version_etag=version.etag,
                    expected_dataset_etag=record.etag,
                    previous_version=version,
                    next_version=next_version,
                    next_record=next_record,
                    decision=decision,
                    findings=findings,
                    successor_draft_id=successor_draft_id,
                    supersedes_draft_id=version.source_draft_id,
                    episode_updates=episode_updates,
                    audit_event=audit,
                )
            except DatasetPagePreconditionError as exc:
                raise _version_precondition_failed() from exc
            return DatasetPageReturnReviewMutationRecord(
                scope=scope,
                review_decision=decision,
                findings=findings,
                review_finding_ids=tuple(finding.id for finding in findings),
                output_version_id=version_id,
                version_token=next_version.version_token,
                successor_draft_id=successor_draft_id,
                supersedes_draft_id=version.source_draft_id,
                returned_from_version_id=version_id,
                returned_from_review_decision_id=decision.id,
            )

        result = self._idempotency.execute(
            scope=project_id,
            key=f"dataset-version:return:{dataset_id}:{version_id}:{idempotency_key}",
            payload=payload,
            action=action,
        )
        return DatasetPageReturnReviewOutcome(
            record=result.value,
            replayed=result.replayed,
        )

    def create_diff_job(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        dataset_id: str,
        version_id: str,
        command: DatasetPageDiffJobCommand,
        if_match: str,
        idempotency_key: str,
        request_id: str,
    ) -> DatasetPageAsyncJobOutcome:
        scope = self._authorize_dataset_version_read(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        payload = {
            "compare_to": command.compare_to,
            "snapshot_token": command.snapshot_token,
            "if_match": if_match,
        }

        def action() -> DatasetPageAsyncJobMutationRecord:
            version = self._required_version(
                scope=scope,
                dataset_id=dataset_id,
                version_id=version_id,
            )
            self._require_version_etag(version=version, if_match=if_match)
            projection = self._required_content_projection(
                scope=scope,
                dataset_id=dataset_id,
                version_id=version_id,
            )
            self._require_snapshot_token(
                scope=scope,
                version=version,
                projection=projection,
                snapshot_token=command.snapshot_token,
            )
            if command.compare_to == version_id:
                raise problem(
                    status=422,
                    code="VERSION_DIFF_SELF_COMPARE",
                    title="Invalid version comparison",
                    detail="A fixed version cannot be compared with itself.",
                )
            compare_version = self._required_version(
                scope=scope,
                dataset_id=dataset_id,
                version_id=command.compare_to,
            )
            compare_projection = self._required_content_projection(
                scope=scope,
                dataset_id=dataset_id,
                version_id=compare_version.version_id,
            )
            current_entries = self._repository.list_manifest_entries(
                scope=scope,
                dataset_id=dataset_id,
                version_id=version_id,
            )
            compare_entries = self._repository.list_manifest_entries(
                scope=scope,
                dataset_id=dataset_id,
                version_id=compare_version.version_id,
            )
            now = self._clock()
            current_ids = {item.entry_id: item.sha256 for item in current_entries}
            compare_ids = {item.entry_id: item.sha256 for item in compare_entries}
            job = DatasetPageAsyncJob(
                job_id=_dataset_job_id(
                    scope=scope,
                    dataset_id=dataset_id,
                    version_id=version_id,
                    idempotency_key=idempotency_key,
                    kind="version-diff",
                ),
                job_type="VERSION_DIFF",
                status="SUCCEEDED",
                resource_type="DATASET_VERSION",
                resource_id=version_id,
                progress={"completed_steps": "1", "total_steps": "1"},
                result_ref={
                    "compare_to": compare_version.version_id,
                    "content_snapshot_id": projection.content_snapshot.content_snapshot_id,
                    "compare_content_snapshot_id": (
                        compare_projection.content_snapshot.content_snapshot_id
                    ),
                    "added_entry_count": str(len(current_ids.keys() - compare_ids.keys())),
                    "removed_entry_count": str(len(compare_ids.keys() - current_ids.keys())),
                    "changed_entry_count": str(
                        sum(
                            current_ids[key] != compare_ids[key]
                            for key in current_ids.keys() & compare_ids.keys()
                        )
                    ),
                },
                created_at=now,
                updated_at=now,
                resource_version="1",
            )
            audit = DatasetPageAuditEvent(
                project_id=scope.project_id,
                region_code=scope.region_code,
                actor_id=auth.subject_id,
                action="dataset.version_diff_created",
                resource_id=version_id,
                request_id=request_id,
                outcome="SUCCEEDED",
                occurred_at=now,
                details={"job_id": job.job_id, "compare_to": compare_version.version_id},
            )
            try:
                self._repository.create_async_job(
                    scope=scope,
                    dataset_id=dataset_id,
                    version_id=version_id,
                    job=job,
                    audit_event=audit,
                )
            except DatasetPagePreconditionError as exc:
                raise _version_precondition_failed() from exc
            return DatasetPageAsyncJobMutationRecord(scope=scope, job=job)

        result = self._idempotency.execute(
            scope=project_id,
            key=f"dataset-version:diff:{dataset_id}:{version_id}:{idempotency_key}",
            payload=payload,
            action=action,
        )
        return DatasetPageAsyncJobOutcome(
            record=result.value,
            replayed=result.replayed,
        )

    def deletion_preflight(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        dataset_id: str,
        version_id: str | None,
        command: DatasetPageDeletionPreflightCommand,
        if_match: str,
        idempotency_key: str,
        request_id: str,
    ) -> DatasetPageDeletionPreflight:
        del idempotency_key
        scope = self._authorize_dataset_version_read(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        record = self._required_dataset(scope=scope, dataset_id=dataset_id)
        version = (
            None
            if version_id is None
            else self._required_version(scope=scope, dataset_id=dataset_id, version_id=version_id)
        )
        expected_etag = record.etag if version is None else version.etag
        if if_match != expected_etag or command.expected_etag != expected_etag:
            raise _version_precondition_failed()
        target_versions = (
            (version,)
            if version is not None
            else self._repository.list_versions(
                scope=scope,
                dataset_id=dataset_id,
                filters=DatasetPageVersionFilters(),
            )
        )
        has_active_projection = any(
            self._repository.content_projection(
                scope=scope,
                dataset_id=dataset_id,
                version_id=item.version_id,
            )
            is not None
            for item in target_versions
        )
        is_current_ready = any(item.status == "READY" for item in target_versions)
        can_delete = auth.has_capability("dataset.delete", project_id)
        active_reasons = (
            (
                DatasetPageBlockedReason(
                    code="ACTIVE_CONTENT_PROJECTION",
                    message="The selected resource still has durable content projections.",
                ),
            )
            if has_active_projection
            else ()
        )
        checks = (
            DatasetPageDeletionCheck(
                check_type="ACTIVE_REFERENCES",
                passed=not has_active_projection,
                blocked_reasons=active_reasons,
            ),
            DatasetPageDeletionCheck(
                check_type="RETENTION",
                passed=False,
                blocked_reasons=(
                    DatasetPageBlockedReason(
                        code="RETENTION_POLICY_UNRESOLVED",
                        message="Deletion retention evaluation is not an approved workflow.",
                    ),
                ),
            ),
            DatasetPageDeletionCheck(
                check_type="LEGAL_HOLD",
                passed=False,
                blocked_reasons=(
                    DatasetPageBlockedReason(
                        code="LEGAL_HOLD_UNRESOLVED",
                        message="Legal-hold evaluation is not an approved workflow.",
                    ),
                ),
            ),
            DatasetPageDeletionCheck(
                check_type="PERMISSION",
                passed=can_delete,
                blocked_reasons=(
                    ()
                    if can_delete
                    else (
                        DatasetPageBlockedReason(
                            code="DATASET_DELETE_CAPABILITY_REQUIRED",
                            message="The current scope cannot execute dataset deletion.",
                        ),
                    )
                ),
            ),
            DatasetPageDeletionCheck(
                check_type="CONCURRENT_JOBS",
                passed=False,
                blocked_reasons=(
                    DatasetPageBlockedReason(
                        code="DELETION_EXECUTION_UNAVAILABLE",
                        message=(
                            "No approved deletion executor is configured for this product flow."
                        ),
                    ),
                ),
            ),
            DatasetPageDeletionCheck(
                check_type="CURRENT_READY",
                passed=not is_current_ready,
                blocked_reasons=(
                    ()
                    if not is_current_ready
                    else (
                        DatasetPageBlockedReason(
                            code="CURRENT_READY_PROTECTED",
                            message="A current ready version cannot be deleted by this preflight.",
                        ),
                    )
                ),
            ),
            DatasetPageDeletionCheck(
                check_type="AUDIT_PROTECTION",
                passed=False,
                blocked_reasons=(
                    DatasetPageBlockedReason(
                        code="AUDIT_PROTECTED",
                        message="Audit retention prevents an executable deletion decision.",
                    ),
                ),
            ),
        )
        storage_items = tuple(
            item
            for candidate in target_versions
            for item in self._repository.list_required_storage(
                scope=scope,
                dataset_id=dataset_id,
                version_id=candidate.version_id,
            )
        )
        blockers = tuple(reason for check in checks for reason in check.blocked_reasons)
        now = self._clock()
        expires_at = now + timedelta(minutes=10)
        resource_id = dataset_id if version is None else version.version_id
        preflight_token = self._cursor.encode(
            {
                "kind": "p07-deletion-preflight",
                "organization_id": scope.organization_id,
                "project_id": scope.project_id,
                "region_code": scope.region_code,
                "resource_id": resource_id,
                "expected_etag": expected_etag,
                "expires_at": expires_at.isoformat(),
            }
        )
        self._audit(
            auth=auth,
            scope=scope,
            action="dataset.deletion_preflight_viewed",
            resource_id=resource_id,
            request_id=request_id,
            outcome="RESERVED_CONDITIONAL",
            details={"resource_type": "DATASET" if version is None else "DATASET_VERSION"},
        )
        return DatasetPageDeletionPreflight(
            scope=scope,
            resource_type="DATASET" if version is None else "DATASET_VERSION",
            resource_id=resource_id,
            domain_clear=not blockers,
            preflight_token=preflight_token,
            expires_at=expires_at,
            checks=checks,
            async_impact=DatasetPageDeletionAsyncImpact(
                object_count=str(len(storage_items)),
                estimated_bytes=str(sum(int(item.size_bytes) for item in storage_items)),
                dependent_projection_count=str(
                    sum(
                        1
                        for candidate in target_versions
                        if self._repository.content_projection(
                            scope=scope,
                            dataset_id=dataset_id,
                            version_id=candidate.version_id,
                        )
                        is not None
                    )
                ),
            ),
            blocked_reasons=blockers,
        )

    def dataset_envelope(
        self,
        *,
        record: DatasetPageRecord,
        auth: AuthContext,
        request_id: str,
    ) -> DatasetPageDatasetEnvelope:
        now = self._clock()
        return DatasetPageDatasetEnvelope(
            data=self._dataset(record, auth),
            meta=self._meta(request_id=request_id, now=now),
        )

    def mutation_meta(self, *, request_id: str) -> DatasetPageMeta:
        """Build response metadata for mutation envelopes without exposing clock internals."""

        return self._meta(request_id=request_id, now=self._clock())

    def _dataset(self, record: DatasetPageRecord, auth: AuthContext) -> DatasetPageDataset:
        return DatasetPageDataset(
            scope=record.scope,
            dataset_id=record.dataset_id,
            name=record.name,
            description=record.description,
            labels=record.labels,
            availability=record.availability,
            owner=record.owner,
            created_at=record.created_at,
            updated_at=record.updated_at,
            etag=record.etag,
            allowed_actions=self._actions(record, auth),
        )

    @staticmethod
    def _fallback_detail_summary(record: DatasetPageRecord) -> DatasetPageDetailSummary:
        """Represent absent measured facts as explicit unknowns rather than guessed zeroes."""

        return DatasetPageDetailSummary(
            episode_count=record.episode_count,
            effective_duration_ns="0",
            source_bytes="0",
            required_physical_bytes="0",
            actual_oss_bytes=None,
            pending_review_version_count=record.pending_review_version_count,
            returned_version_count=record.returned_version_count,
            actionable_draft_count=record.actionable_draft_count,
            calculated_at=record.updated_at,
            calculation_state="PARTIAL",
        )

    @classmethod
    def _detail_summary(
        cls,
        *,
        record: DatasetPageRecord,
        facts: DatasetPageDetailFacts | None,
    ) -> DatasetPageDetailSummary:
        """Keep live review counters authoritative while retaining measured capacity facts."""

        baseline = facts.summary if facts is not None else cls._fallback_detail_summary(record)
        return baseline.model_copy(
            update={
                "episode_count": record.episode_count,
                "pending_review_version_count": record.pending_review_version_count,
                "returned_version_count": record.returned_version_count,
                "actionable_draft_count": record.actionable_draft_count,
            }
        )

    @staticmethod
    def _current_ready_version(
        versions: tuple[DatasetPageVersion, ...],
    ) -> DatasetPageCurrentReadyVersion | None:
        ready = [version for version in versions if version.status == "READY"]
        if not ready:
            return None
        current = max(ready, key=lambda version: (version.published_at, version.version_id))
        return DatasetPageCurrentReadyVersion(
            version_id=current.version_id,
            display_version=current.display_version,
            kind=current.kind,
            published_at=current.published_at,
            manifest_sha256=current.manifest.sha256,
        )

    @staticmethod
    def _suggested_version_id(
        versions: tuple[DatasetPageVersion, ...],
    ) -> str | None:
        if not versions:
            return None
        reviewing = [version for version in versions if version.status == "REVIEWING"]
        candidates: tuple[DatasetPageVersion, ...] = tuple(reviewing) if reviewing else versions
        current = max(candidates, key=lambda version: (version.created_at, version.version_id))
        return current.version_id

    @staticmethod
    def _sort_versions(
        records: tuple[DatasetPageVersion, ...], sort: DatasetVersionSort
    ) -> tuple[DatasetPageVersion, ...]:
        if sort == "created_at:asc,version_id:asc":
            return tuple(sorted(records, key=lambda item: (item.created_at, item.version_id)))
        if sort == "display_version:desc,version_id:desc":
            return tuple(
                sorted(
                    records,
                    key=lambda item: (item.display_version.casefold(), item.version_id),
                    reverse=True,
                )
            )
        if sort == "display_version:asc,version_id:asc":
            return tuple(
                sorted(records, key=lambda item: (item.display_version.casefold(), item.version_id))
            )
        return tuple(
            sorted(records, key=lambda item: (item.created_at, item.version_id), reverse=True)
        )

    @staticmethod
    def _sort_source_provenance(
        records: tuple[DatasetPageSourceProvenance, ...],
        sort: DatasetSourceProvenanceSort,
    ) -> tuple[DatasetPageSourceProvenance, ...]:
        if sort == "registered_at:asc,provenance_id:asc":
            return tuple(sorted(records, key=lambda item: (item.registered_at, item.provenance_id)))
        if sort == "source_display_name:asc,provenance_id:asc":
            return tuple(
                sorted(
                    records,
                    key=lambda item: (
                        (item.source_display_name or "").casefold(),
                        item.provenance_id,
                    ),
                )
            )
        return tuple(
            sorted(records, key=lambda item: (item.registered_at, item.provenance_id), reverse=True)
        )

    @staticmethod
    def _sort_episodes(
        records: tuple[DatasetPageEpisodeRecord, ...], sort: DatasetEpisodeSort
    ) -> tuple[DatasetPageEpisodeRecord, ...]:
        if sort == "started_at_ns:desc,episode_id:desc":
            return tuple(
                sorted(
                    records,
                    key=lambda item: (int(item.started_at_ns), item.episode_id),
                    reverse=True,
                )
            )
        if sort == "started_at_ns:asc,episode_id:asc":
            return tuple(
                sorted(records, key=lambda item: (int(item.started_at_ns), item.episode_id))
            )
        return tuple(
            sorted(
                records,
                key=lambda item: (item.selected_revision.ordinal, item.episode_id),
            )
        )

    def _projection_page(
        self,
        records: tuple[ProjectionRecord, ...],
        *,
        kind: str,
        scope: DatasetPageScope,
        filters: dict[str, object],
        sort: str,
        after: str | None,
        before: str | None,
        limit: int,
        identifier: Callable[[ProjectionRecord], str],
    ) -> tuple[tuple[ProjectionRecord, ...], DatasetPageInfo]:
        if after is not None and before is not None:
            raise problem(
                status=422,
                code="CURSOR_DIRECTION_CONFLICT",
                title="Invalid pagination request",
                detail="Use either after or before, not both.",
            )
        cursor = after or before
        index = -1
        if cursor is not None:
            payload = self._cursor.decode(cursor)
            expected = {
                "kind": kind,
                "organization_id": scope.organization_id,
                "project_id": scope.project_id,
                "region_code": scope.region_code,
                "sort": sort,
                "filters": canonical_hash(filters),
            }
            if any(payload.get(key) != value for key, value in expected.items()):
                raise problem(
                    status=400,
                    code="INVALID_CURSOR",
                    title="Invalid pagination cursor",
                    detail="The cursor belongs to a different page query.",
                )
            boundary = payload.get("resource_id")
            if not isinstance(boundary, str):
                raise problem(
                    status=400,
                    code="INVALID_CURSOR",
                    title="Invalid pagination cursor",
                    detail="The cursor does not contain a valid resource boundary.",
                )
            for candidate_index, item in enumerate(records):
                if identifier(item) == boundary:
                    index = candidate_index
                    break
            if index < 0:
                raise problem(
                    status=400,
                    code="INVALID_CURSOR",
                    title="Invalid pagination cursor",
                    detail="The cursor boundary is no longer available.",
                )
        if not records:
            return (), DatasetPageInfo(
                has_next=False,
                has_previous=False,
                limit=limit,
                total_count="0",
            )
        if after is not None:
            start = index + 1
            visible = records[start : start + limit]
            has_previous = start > 0
            has_next = start + len(visible) < len(records)
        elif before is not None:
            end = index
            start = max(0, end - limit)
            visible = records[start:end]
            has_previous = start > 0
            has_next = end < len(records)
        else:
            visible = records[:limit]
            has_previous = False
            has_next = len(visible) < len(records)
        if not visible:
            return (), DatasetPageInfo(
                has_next=False,
                has_previous=bool(after),
                limit=limit,
                total_count=str(len(records)),
            )

        def encode(item: ProjectionRecord) -> str:
            return self._cursor.encode(
                {
                    "kind": kind,
                    "organization_id": scope.organization_id,
                    "project_id": scope.project_id,
                    "region_code": scope.region_code,
                    "sort": sort,
                    "filters": canonical_hash(filters),
                    "resource_id": identifier(item),
                }
            )

        return (
            visible,
            DatasetPageInfo(
                after=encode(visible[-1]),
                before=encode(visible[0]),
                has_next=has_next,
                has_previous=has_previous,
                limit=limit,
                total_count=str(len(records)),
            ),
        )

    def _required_dataset(self, *, scope: DatasetPageScope, dataset_id: str) -> DatasetPageRecord:
        record = self._repository.get_record(scope=scope, dataset_id=dataset_id)
        if record is None:
            raise problem(
                status=404,
                code="DATASET_NOT_FOUND",
                title="Dataset not found",
                detail="The requested dataset is not available in the selected scope.",
            )
        return record

    def _required_version(
        self, *, scope: DatasetPageScope, dataset_id: str, version_id: str
    ) -> DatasetPageVersion:
        self._required_dataset(scope=scope, dataset_id=dataset_id)
        for version in self._repository.list_versions(
            scope=scope,
            dataset_id=dataset_id,
            filters=DatasetPageVersionFilters(),
        ):
            if version.version_id == version_id:
                return version
        raise problem(
            status=404,
            code="DATASET_VERSION_NOT_FOUND",
            title="Dataset version not found",
            detail="The requested immutable version is not available in the selected scope.",
        )

    def _reviewing_version(
        self, *, scope: DatasetPageScope, dataset_id: str, version_id: str
    ) -> DatasetPageReviewingVersion:
        version = self._required_version(scope=scope, dataset_id=dataset_id, version_id=version_id)
        if isinstance(version, DatasetPageReviewingVersion):
            return version
        raise problem(
            status=409,
            code="REVIEW_STATUS_STALE",
            title="Version is not reviewable",
            detail="Only a current REVIEWING version can receive a review decision.",
        )

    def _required_content_projection(
        self, *, scope: DatasetPageScope, dataset_id: str, version_id: str
    ) -> DatasetPageVersionContentProjection:
        projection = self._repository.content_projection(
            scope=scope,
            dataset_id=dataset_id,
            version_id=version_id,
        )
        if projection is not None:
            return projection
        raise problem(
            status=409,
            code="VERSION_CONTENT_PROJECTION_UNAVAILABLE",
            title="Version content projection is unavailable",
            detail="This version has no durable fixed content projection yet.",
        )

    def _snapshot_token(
        self,
        *,
        scope: DatasetPageScope,
        version: DatasetPageVersion,
        projection: DatasetPageVersionContentProjection,
    ) -> str:
        return self._cursor.encode(
            {
                "kind": "p07-version-snapshot",
                "organization_id": scope.organization_id,
                "project_id": scope.project_id,
                "region_code": scope.region_code,
                "dataset_id": version.dataset_id,
                "version_id": version.version_id,
                "version_token": version.version_token,
                "content_snapshot_id": projection.content_snapshot.content_snapshot_id,
            }
        )

    def _require_snapshot_token(
        self,
        *,
        scope: DatasetPageScope,
        version: DatasetPageVersion,
        projection: DatasetPageVersionContentProjection,
        snapshot_token: str,
    ) -> None:
        payload = self._cursor.decode(snapshot_token)
        expected = {
            "kind": "p07-version-snapshot",
            "organization_id": scope.organization_id,
            "project_id": scope.project_id,
            "region_code": scope.region_code,
            "dataset_id": version.dataset_id,
            "version_id": version.version_id,
            "version_token": version.version_token,
            "content_snapshot_id": projection.content_snapshot.content_snapshot_id,
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise problem(
                status=409,
                code="VERSION_SNAPSHOT_EXPIRED",
                title="Version snapshot expired",
                detail="Reload the fixed-version bootstrap before continuing.",
            )

    def _review_token(
        self,
        *,
        scope: DatasetPageScope,
        version: DatasetPageReviewingVersion,
        catalog: DatasetPageReviewFindingCatalog,
        expires_at: datetime,
    ) -> str:
        return self._cursor.encode(
            {
                "kind": "p07-review-token",
                "organization_id": scope.organization_id,
                "project_id": scope.project_id,
                "region_code": scope.region_code,
                "dataset_id": version.dataset_id,
                "version_id": version.version_id,
                "etag": version.etag,
                "version_token": version.version_token,
                "finding_catalog_version": catalog.version,
                "expires_at": expires_at.isoformat(),
            }
        )

    def _require_review_token(
        self,
        *,
        scope: DatasetPageScope,
        version: DatasetPageReviewingVersion,
        review_token: str,
        catalog: DatasetPageReviewFindingCatalog,
    ) -> None:
        payload = self._cursor.decode(review_token)
        expires_at_value = payload.get("expires_at")
        try:
            expires_at = (
                datetime.fromisoformat(expires_at_value)
                if isinstance(expires_at_value, str)
                else None
            )
        except ValueError:
            expires_at = None
        expected = {
            "kind": "p07-review-token",
            "organization_id": scope.organization_id,
            "project_id": scope.project_id,
            "region_code": scope.region_code,
            "dataset_id": version.dataset_id,
            "version_id": version.version_id,
            "etag": version.etag,
            "version_token": version.version_token,
            "finding_catalog_version": catalog.version,
        }
        if (
            expires_at is None
            or expires_at.tzinfo is None
            or expires_at <= self._clock()
            or any(payload.get(key) != value for key, value in expected.items())
        ):
            raise problem(
                status=409,
                code="REVIEW_TOKEN_EXPIRED",
                title="Review token expired",
                detail="Run review checks again before submitting a decision.",
            )

    def _review_prerequisites(
        self,
        *,
        scope: DatasetPageScope,
        dataset_id: str,
        version: DatasetPageReviewingVersion,
    ) -> tuple[tuple[DatasetPageBlockedReason, ...], tuple[DatasetPageReviewCheckTarget, ...]]:
        blockers: list[DatasetPageBlockedReason] = []
        projection = self._repository.content_projection(
            scope=scope,
            dataset_id=dataset_id,
            version_id=version.version_id,
        )
        if projection is None:
            blockers.append(
                DatasetPageBlockedReason(
                    code="CONTENT_PROJECTION_MISSING",
                    message="Fixed content projection has not been recorded for this version.",
                )
            )
        elif not self._repository.list_manifest_entries(
            scope=scope,
            dataset_id=dataset_id,
            version_id=version.version_id,
        ):
            blockers.append(
                DatasetPageBlockedReason(
                    code="MANIFEST_ENTRIES_MISSING",
                    message="A version manifest must contain durable entries before approval.",
                )
            )
        if (
            self._repository.schema_detail(
                scope=scope,
                dataset_id=dataset_id,
                version_id=version.version_id,
            )
            is None
        ):
            blockers.append(
                DatasetPageBlockedReason(
                    code="SCHEMA_DETAIL_MISSING",
                    message="Fixed schema detail has not been recorded for this version.",
                )
            )
        capacity = self._repository.capacity_facts(
            scope=scope,
            dataset_id=dataset_id,
            version_id=version.version_id,
        )
        if capacity is None or capacity.state != "SETTLED":
            blockers.append(
                DatasetPageBlockedReason(
                    code="CAPACITY_NOT_SETTLED",
                    message="Capacity facts must be settled before an approval decision.",
                )
            )
        targets: list[DatasetPageReviewCheckTarget] = []
        episodes = self._repository.list_episodes(
            scope=scope,
            dataset_id=dataset_id,
            version_id=version.version_id,
            filters=DatasetPageEpisodeFilters(),
        )
        for episode in episodes:
            revision = self._repository.episode_revision(
                scope=scope,
                dataset_id=dataset_id,
                version_id=version.version_id,
                revision_id=episode.selected_revision.revision_id,
            )
            if revision is None:
                continue
            targets.append(
                DatasetPageReviewCheckTarget(
                    output_revision_id=revision.revision_id,
                    episode_id=revision.episode_id,
                    streams=revision.streams,
                )
            )
        if not targets:
            blockers.append(
                DatasetPageBlockedReason(
                    code="REVIEW_TARGETS_MISSING",
                    message="No durable episode revision stream can receive a review finding.",
                )
            )
        return tuple(blockers), tuple(targets)

    @staticmethod
    def _validate_return_findings(
        *,
        command: DatasetPageReturnReviewCommand,
        catalog: DatasetPageReviewFindingCatalog,
        targets: tuple[DatasetPageReviewCheckTarget, ...],
    ) -> None:
        allowed_types = {item.code: set(item.allowed_severities) for item in catalog.finding_types}
        available = {
            target.output_revision_id: {
                stream.episode_stream_id: stream for stream in target.streams
            }
            for target in targets
        }
        editable_bases = {finding.output_revision_id for finding in command.findings}
        if len(editable_bases) != 1:
            raise problem(
                status=422,
                code="REVIEW_RETURN_MULTI_BASE_UNSUPPORTED",
                title="Review Return must target one editable base",
                detail=(
                    "A P11 review-return successor has exactly one editable base revision; "
                    "split findings into separate return decisions."
                ),
            )
        seen: set[tuple[str, str, str, str, str]] = set()
        for finding in command.findings:
            stream = available.get(finding.output_revision_id, {}).get(finding.episode_stream_id)
            if stream is None:
                raise problem(
                    status=422,
                    code="REVIEW_FINDING_TARGET_INVALID",
                    title="Review finding target is invalid",
                    detail=(
                        "Each finding must target a revision and stream returned by review checks."
                    ),
                )
            if (
                finding.finding_type not in allowed_types
                or finding.severity not in allowed_types[finding.finding_type]
            ):
                raise problem(
                    status=422,
                    code="REVIEW_FINDING_CATALOG_INVALID",
                    title="Review finding catalog value is invalid",
                    detail="Finding type and severity must be allowed by the current catalog.",
                )
            if not finding.note.strip():
                raise problem(
                    status=422,
                    code="REVIEW_FINDING_NOTE_INVALID",
                    title="Review finding note is invalid",
                    detail="A non-empty review finding note is required.",
                )
            if not (
                int(stream.t_start_ns)
                <= int(finding.start_ns)
                < int(finding.end_ns)
                <= int(stream.t_end_ns)
            ):
                raise problem(
                    status=422,
                    code="REVIEW_FINDING_RANGE_INVALID",
                    title="Review finding range is invalid",
                    detail="Finding ranges must be non-empty and contained by their target stream.",
                )
            identity = (
                finding.output_revision_id,
                finding.episode_stream_id,
                finding.start_ns,
                finding.end_ns,
                finding.finding_type,
            )
            if identity in seen:
                raise problem(
                    status=422,
                    code="REVIEW_FINDING_DUPLICATE",
                    title="Duplicate review finding",
                    detail="A review command cannot repeat the same finding interval and type.",
                )
            seen.add(identity)

    def _returned_episode_updates(
        self,
        *,
        scope: DatasetPageScope,
        dataset_id: str,
        version_id: str,
        findings: tuple[DatasetPageReviewFinding, ...],
    ) -> tuple[DatasetPageEpisodeRecord, ...]:
        counts = Counter(finding.output_revision_id for finding in findings)
        records = self._repository.list_episodes(
            scope=scope,
            dataset_id=dataset_id,
            version_id=version_id,
            filters=DatasetPageEpisodeFilters(),
        )
        updates = tuple(
            record.model_copy(
                update={
                    "review_status": "HAS_FINDING",
                    "review_finding_count": str(
                        int(record.review_finding_count or "0")
                        + counts[record.selected_revision.revision_id]
                    ),
                    "has_finding": True,
                }
            )
            for record in records
            if record.selected_revision.revision_id in counts
        )
        if len(updates) != len(counts):
            raise problem(
                status=409,
                code="REVIEW_TARGETS_STALE",
                title="Review targets changed",
                detail="Run review checks again before returning this version.",
            )
        return updates

    @staticmethod
    def _require_version_etag(*, version: DatasetPageVersion, if_match: str) -> None:
        if if_match != version.etag:
            raise _version_precondition_failed()

    def _authorize_read(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
    ) -> DatasetPageScope:
        ScopeGuard.require(auth, project_id, region_code)
        if not _can_read(auth, project_id):
            auth.require_capability("dataset.read", project_id)
        return self._scope(organization_id, project_id, region_code)

    def _authorize_dataset_version_read(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
    ) -> DatasetPageScope:
        ScopeGuard.require(auth, project_id, region_code)
        if not _can_dataset_version_read(auth, project_id):
            auth.require_capability("dataset_version.read", project_id)
        return self._scope(organization_id, project_id, region_code)

    def _authorize_schema_read(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
    ) -> DatasetPageScope:
        ScopeGuard.require(auth, project_id, region_code)
        if not _can_schema_read(auth, project_id):
            auth.require_capability("data_schema.read", project_id)
        return self._scope(organization_id, project_id, region_code)

    def _authorize_storage_overview_read(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
    ) -> DatasetPageScope:
        ScopeGuard.require(auth, project_id, region_code)
        if not _can_storage_overview_read(auth, project_id):
            auth.require_capability("storage.overview.read", project_id)
        return self._scope(organization_id, project_id, region_code)

    def _authorize_episode_read(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
    ) -> DatasetPageScope:
        ScopeGuard.require(auth, project_id, region_code)
        if not _can_episode_read(auth, project_id):
            auth.require_capability("episode.read", project_id)
        return self._scope(organization_id, project_id, region_code)

    def _authorize_review(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
    ) -> DatasetPageScope:
        ScopeGuard.require(auth, project_id, region_code)
        if not _can_review(auth, project_id):
            auth.require_capability("dataset_version.review", project_id)
        return self._scope(organization_id, project_id, region_code)

    @staticmethod
    def _authorize_publish(*, auth: AuthContext, project_id: str, region_code: str) -> None:
        ScopeGuard.require(auth, project_id, region_code)
        if not _can_publish(auth, project_id):
            auth.require_capability("dataset_version.publish", project_id)

    def _authorize_create(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
    ) -> DatasetPageScope:
        ScopeGuard.require(auth, project_id, region_code)
        if not _can_create(auth, project_id):
            auth.require_capability("dataset.create", project_id)
        return self._scope(organization_id, project_id, region_code)

    def _scope(self, organization_id: str, project_id: str, region_code: str) -> DatasetPageScope:
        if not self._repository.has_organization_project(
            organization_id=organization_id,
            project_id=project_id,
        ):
            raise problem(
                status=403,
                code="ORGANIZATION_SCOPE_DENIED",
                title="Organization access denied",
                detail="The selected project is not a member of this organization.",
            )
        return DatasetPageScope(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )

    def _list_item(self, record: DatasetPageRecord, auth: AuthContext) -> DatasetPageListItem:
        return DatasetPageListItem(
            scope=record.scope,
            dataset_id=record.dataset_id,
            name=record.name,
            dataset_created_at=record.created_at,
            dataset_activity_at=record.activity_at,
            current_version=record.current_ready_version,
            episode_count=record.episode_count,
            pending_review_version_count=record.pending_review_version_count,
            returned_version_count=record.returned_version_count,
            actionable_draft_count=record.actionable_draft_count,
            allowed_actions=self._actions(record, auth),
        )

    @staticmethod
    def _actions(
        record: DatasetPageRecord, auth: AuthContext
    ) -> tuple[DatasetPageAllowedAction, ...]:
        can_open_episode = record.current_ready_version is not None and _can_episode_read(
            auth, record.scope.project_id
        )
        reasons: tuple[DatasetPageBlockedReason, ...] = ()
        if record.current_ready_version is None:
            reasons = (
                DatasetPageBlockedReason(
                    code="READY_VERSION_REQUIRED",
                    message=(
                        "A ready dataset version is required before opening collection entries."
                    ),
                ),
            )
        elif not can_open_episode:
            reasons = (
                DatasetPageBlockedReason(
                    code="EPISODE_READ_CAPABILITY_REQUIRED",
                    message="The current scope cannot read collection entries.",
                ),
            )
        return (
            DatasetPageAllowedAction(action="OPEN_DATASET", allowed=True),
            DatasetPageAllowedAction(
                action="OPEN_VERSION", allowed=record.current_ready_version is not None
            ),
            DatasetPageAllowedAction(
                action="OPEN_EPISODE",
                allowed=can_open_episode,
                blocked_reasons=() if can_open_episode else reasons,
            ),
        )

    @staticmethod
    def _sort(
        records: tuple[DatasetPageRecord, ...], sort: DatasetSort
    ) -> tuple[DatasetPageRecord, ...]:
        if sort == "name:asc,dataset_id:asc":
            return tuple(
                sorted(records, key=lambda record: (record.name.casefold(), record.dataset_id))
            )
        if sort == "created_at:desc,dataset_id:desc":
            return tuple(
                sorted(
                    records, key=lambda record: (record.created_at, record.dataset_id), reverse=True
                )
            )
        return tuple(
            sorted(
                records, key=lambda record: (record.activity_at, record.dataset_id), reverse=True
            )
        )

    def _page(
        self,
        records: tuple[DatasetPageRecord, ...],
        *,
        scope: DatasetPageScope,
        filters: DatasetPageFilters,
        sort: DatasetSort,
        after: str | None,
        before: str | None,
        limit: int,
    ) -> tuple[tuple[DatasetPageRecord, ...], DatasetPageInfo]:
        cursor = after or before
        index = self._cursor_index(
            records,
            scope=scope,
            filters=filters,
            sort=sort,
            cursor=cursor,
        )
        if not records:
            return (), DatasetPageInfo(
                has_next=False, has_previous=False, limit=limit, total_count="0"
            )
        if after is not None:
            start = index + 1
            visible = records[start : start + limit]
            has_previous = start > 0
            has_next = start + len(visible) < len(records)
        elif before is not None:
            end = index
            start = max(0, end - limit)
            visible = records[start:end]
            has_previous = start > 0
            has_next = end < len(records)
        else:
            visible = records[:limit]
            has_previous = False
            has_next = len(visible) < len(records)
        if not visible:
            return (), DatasetPageInfo(
                has_next=False,
                has_previous=bool(after),
                limit=limit,
                total_count=str(len(records)),
            )
        return (
            visible,
            DatasetPageInfo(
                after=self._encode_cursor(
                    scope=scope,
                    filters=filters,
                    sort=sort,
                    dataset_id=visible[-1].dataset_id,
                ),
                before=self._encode_cursor(
                    scope=scope,
                    filters=filters,
                    sort=sort,
                    dataset_id=visible[0].dataset_id,
                ),
                has_next=has_next,
                has_previous=has_previous,
                limit=limit,
                total_count=str(len(records)),
            ),
        )

    def _cursor_index(
        self,
        records: tuple[DatasetPageRecord, ...],
        *,
        scope: DatasetPageScope,
        filters: DatasetPageFilters,
        sort: DatasetSort,
        cursor: str | None,
    ) -> int:
        if cursor is None:
            return -1
        payload = self._cursor.decode(cursor)
        expected = {
            "kind": "p05-datasets",
            "organization_id": scope.organization_id,
            "project_id": scope.project_id,
            "region_code": scope.region_code,
            "sort": sort,
            "filters": canonical_hash(_filters_document(filters)),
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise problem(
                status=400,
                code="INVALID_CURSOR",
                title="Invalid pagination cursor",
                detail="The cursor belongs to a different dataset query.",
            )
        dataset_id = payload.get("dataset_id")
        if not isinstance(dataset_id, str):
            raise problem(
                status=400,
                code="INVALID_CURSOR",
                title="Invalid pagination cursor",
                detail="The cursor does not contain a valid dataset boundary.",
            )
        for index, record in enumerate(records):
            if record.dataset_id == dataset_id:
                return index
        raise problem(
            status=400,
            code="INVALID_CURSOR",
            title="Invalid pagination cursor",
            detail="The cursor boundary is no longer available.",
        )

    def _encode_cursor(
        self,
        *,
        scope: DatasetPageScope,
        filters: DatasetPageFilters,
        sort: DatasetSort,
        dataset_id: str,
    ) -> str:
        return self._cursor.encode(
            {
                "kind": "p05-datasets",
                "organization_id": scope.organization_id,
                "project_id": scope.project_id,
                "region_code": scope.region_code,
                "sort": sort,
                "filters": canonical_hash(_filters_document(filters)),
                "dataset_id": dataset_id,
            }
        )

    def _audit(
        self,
        *,
        auth: AuthContext,
        scope: DatasetPageScope,
        action: str,
        resource_id: str,
        request_id: str,
        outcome: str,
        details: dict[str, object] | None = None,
    ) -> None:
        self._repository.append_audit(
            DatasetPageAuditEvent(
                project_id=scope.project_id,
                region_code=scope.region_code,
                actor_id=auth.subject_id,
                action=action,
                resource_id=resource_id,
                request_id=request_id,
                outcome=outcome,
                occurred_at=self._clock(),
                details=details,
            )
        )

    @staticmethod
    def _meta(*, request_id: str, now: datetime) -> DatasetPageMeta:
        return DatasetPageMeta(
            request_id=request_id,
            trace_id=request_id,
            correlation_id=request_id,
            generated_at=now,
            as_of=now,
        )


def _can_read(auth: AuthContext, project_id: str) -> bool:
    return auth.has_capability("dataset.read", project_id) or auth.has_capability(
        "datasets.read", project_id
    )


def _can_create(auth: AuthContext, project_id: str) -> bool:
    return auth.has_capability("dataset.create", project_id) or auth.has_capability(
        "datasets.write", project_id
    )


def _can_dataset_version_read(auth: AuthContext, project_id: str) -> bool:
    return auth.has_capability("dataset_version.read", project_id) or auth.has_capability(
        "datasets.read", project_id
    )


def _can_schema_read(auth: AuthContext, project_id: str) -> bool:
    return auth.has_capability("data_schema.read", project_id) or auth.has_capability(
        "datasets.read", project_id
    )


def _can_storage_overview_read(auth: AuthContext, project_id: str) -> bool:
    return auth.has_capability("storage.overview.read", project_id) or auth.has_capability(
        "datasets.read", project_id
    )


def _can_episode_read(auth: AuthContext, project_id: str) -> bool:
    return auth.has_capability("episode.read", project_id) or auth.has_capability(
        "datasets.read", project_id
    )


def _can_review(auth: AuthContext, project_id: str) -> bool:
    return auth.has_capability("dataset_version.review", project_id) or auth.has_capability(
        "datasets.write", project_id
    )


def _can_publish(auth: AuthContext, project_id: str) -> bool:
    return (
        auth.has_capability("datasets.publish", project_id)
        or auth.has_capability("dataset_version.publish", project_id)
        or auth.has_capability("datasets.write", project_id)
    )


def _review_catalog() -> DatasetPageReviewFindingCatalog:
    """The server-owned, versioned finding catalog used for P07 review commands."""

    severities = (
        DatasetPageReviewSeverity(code="LOW", label="Low", rank=1),
        DatasetPageReviewSeverity(code="MEDIUM", label="Medium", rank=2),
        DatasetPageReviewSeverity(code="HIGH", label="High", rank=3),
        DatasetPageReviewSeverity(code="CRITICAL", label="Critical", rank=4),
    )
    return DatasetPageReviewFindingCatalog(
        version="dataset-version-review-catalog/v1",
        finding_types=(
            DatasetPageReviewFindingType(
                code="RANGE_QUALITY",
                label="Range quality",
                allowed_severities=("LOW", "MEDIUM", "HIGH", "CRITICAL"),
            ),
            DatasetPageReviewFindingType(
                code="DATA_GAP",
                label="Data gap",
                allowed_severities=("MEDIUM", "HIGH", "CRITICAL"),
            ),
        ),
        severities=severities,
        note_min_length=1,
        note_max_length=8192,
    )


def _next_etag(current: str, action: str, identity: str) -> str:
    return str(make_etag({"current": current, "action": action, "identity": identity}))


def _next_version_token(current: str, action: str, identity: str) -> str:
    return canonical_hash({"current": current, "action": action, "identity": identity})


def _review_decision_id(
    *, scope: DatasetPageScope, version_id: str, idempotency_key: str, decision: str
) -> str:
    seed = "/".join(
        (
            "p07-review-decision",
            scope.organization_id,
            scope.project_id,
            scope.region_code,
            version_id,
            decision,
            idempotency_key,
        )
    )
    return f"review_decision_{uuid5(NAMESPACE_URL, seed)}"


def _review_finding_id(review_decision_id: str, ordinal: int) -> str:
    return f"review_finding_{uuid5(NAMESPACE_URL, f'{review_decision_id}:{ordinal}')}"


def _successor_draft_id(review_decision_id: str) -> str:
    return f"draft_{uuid5(NAMESPACE_URL, f'{review_decision_id}:successor')}"


def _dataset_job_id(
    *,
    scope: DatasetPageScope,
    dataset_id: str,
    version_id: str,
    idempotency_key: str,
    kind: str,
) -> str:
    seed = "/".join(
        (
            "p07-dataset-job",
            scope.organization_id,
            scope.project_id,
            scope.region_code,
            dataset_id,
            version_id,
            kind,
            idempotency_key,
        )
    )
    return f"job_{uuid5(NAMESPACE_URL, seed)}"


def _version_precondition_failed() -> Exception:
    return problem(
        status=412,
        code="ETAG_MISMATCH",
        title="Resource version changed",
        detail="Reload the fixed version before retrying this mutation.",
    )


def _dataset_id(scope: DatasetPageScope, idempotency_key: str) -> str:
    seed = "/".join(
        ("p05-dataset", scope.organization_id, scope.project_id, scope.region_code, idempotency_key)
    )
    return f"dataset_{uuid5(NAMESPACE_URL, seed)}"


def _validated_filters(filters: DatasetPageFilters) -> DatasetPageFilters:
    if filters.channel_match not in {"all", "any"}:
        raise problem(
            status=422,
            code="CHANNEL_MATCH_INVALID",
            title="Invalid channel match",
            detail="Channel matching must be either all or any.",
        )
    if (
        filters.created_from is not None
        and filters.created_to is not None
        and filters.created_from > filters.created_to
    ):
        raise problem(
            status=422,
            code="CREATED_RANGE_INVALID",
            title="Invalid creation range",
            detail="The start date must not be after the end date.",
        )
    channels = tuple(
        dict.fromkeys(channel.strip() for channel in filters.channels if channel.strip())
    )
    if len(channels) > 64 or any(len(channel) > 256 for channel in channels):
        raise problem(
            status=422,
            code="CHANNEL_FILTER_INVALID",
            title="Invalid channel filter",
            detail="At most 64 channel filters of 256 characters are allowed.",
        )
    return DatasetPageFilters(
        query=_clean(filters.query),
        robot_model_id=_clean(filters.robot_model_id),
        robot_id=_clean(filters.robot_id),
        task=_clean(filters.task),
        scene=_clean(filters.scene),
        asset_state=_clean(filters.asset_state),
        storage_class=_clean(filters.storage_class),
        channels=channels,
        channel_match=filters.channel_match,
        created_from=filters.created_from,
        created_to=filters.created_to,
    )


def _validated_version_filters(filters: DatasetPageVersionFilters) -> DatasetPageVersionFilters:
    kind = _clean(filters.kind)
    status = _clean(filters.status)
    if kind is not None:
        kind = kind.upper()
    if status is not None:
        status = status.upper()
    if kind not in {None, "RAW", "CLEANED"}:
        raise problem(
            status=422,
            code="VERSION_KIND_INVALID",
            title="Invalid version kind",
            detail="Version kind must be RAW or CLEANED.",
        )
    if status not in {None, "REVIEWING", "RETURNED", "READY"}:
        raise problem(
            status=422,
            code="VERSION_STATUS_INVALID",
            title="Invalid version status",
            detail="Version status is not supported by this page projection.",
        )
    return DatasetPageVersionFilters(query=_clean(filters.query), kind=kind, status=status)


def _validated_source_provenance_filters(
    filters: DatasetPageSourceProvenanceFilters,
) -> DatasetPageSourceProvenanceFilters:
    query = _clean(filters.query)
    source_id = _clean(filters.source_id)
    if (query is not None and len(query) > 256) or (source_id is not None and len(source_id) > 128):
        raise problem(
            status=422,
            code="SOURCE_PROVENANCE_FILTER_INVALID",
            title="Invalid source provenance filter",
            detail="Source provenance filter values exceed their permitted length.",
        )
    return DatasetPageSourceProvenanceFilters(query=query, source_id=source_id)


def _validated_episode_filters(filters: DatasetPageEpisodeFilters) -> DatasetPageEpisodeFilters:
    if (
        filters.started_from is not None
        and filters.started_to is not None
        and filters.started_from > filters.started_to
    ):
        raise problem(
            status=422,
            code="EPISODE_STARTED_RANGE_INVALID",
            title="Invalid episode start range",
            detail="The start boundary must not be after the end boundary.",
        )
    success_state = _clean(filters.success_state)
    if success_state is not None:
        success_state = success_state.upper()
    if success_state not in {None, "SUCCEEDED", "FAILED", "UNKNOWN"}:
        raise problem(
            status=422,
            code="EPISODE_SUCCESS_STATE_INVALID",
            title="Invalid episode success state",
            detail="Episode success state is not supported by this page projection.",
        )
    review_statuses = _normalized_text_values(filters.review_statuses, maximum=64, item_maximum=64)
    change_types = _normalized_text_values(filters.change_types, maximum=64, item_maximum=128)
    return DatasetPageEpisodeFilters(
        query=_bounded_clean(filters.query, maximum=256, code="EPISODE_FILTER_INVALID"),
        task=_bounded_clean(filters.task, maximum=256, code="EPISODE_FILTER_INVALID"),
        robot_id=_bounded_clean(filters.robot_id, maximum=256, code="EPISODE_FILTER_INVALID"),
        success_state=success_state,
        started_from=filters.started_from,
        started_to=filters.started_to,
        included=filters.included,
        review_statuses=tuple(value.upper() for value in review_statuses),
        has_finding=filters.has_finding,
        change_types=change_types,
    )


def _bounded_clean(value: str | None, *, maximum: int, code: str) -> str | None:
    cleaned = _clean(value)
    if cleaned is not None and len(cleaned) > maximum:
        raise problem(
            status=422,
            code=code,
            title="Invalid page filter",
            detail="A page filter exceeds its permitted length.",
        )
    return cleaned


def _normalized_text_values(
    values: tuple[str, ...], *, maximum: int, item_maximum: int
) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(value.strip() for value in values if value.strip()))
    if len(normalized) > maximum or any(len(value) > item_maximum for value in normalized):
        raise problem(
            status=422,
            code="EPISODE_FILTER_INVALID",
            title="Invalid episode filter",
            detail="Episode filter values exceed their permitted count or length.",
        )
    return normalized


def _require_page_limit(limit: int, *, allowed: set[int]) -> None:
    if limit not in allowed:
        raise problem(
            status=422,
            code="PAGE_LIMIT_INVALID",
            title="Invalid page size",
            detail="Page size is not supported by this page projection.",
        )


def _version_filters_document(filters: DatasetPageVersionFilters) -> dict[str, object]:
    return {"q": filters.query, "version_kind": filters.kind, "version_status": filters.status}


def _source_provenance_filters_document(
    filters: DatasetPageSourceProvenanceFilters,
) -> dict[str, object]:
    return {"q": filters.query, "source_id": filters.source_id}


def _episode_filters_document(filters: DatasetPageEpisodeFilters) -> dict[str, object]:
    return {
        "q": filters.query,
        "task": filters.task,
        "robot_id": filters.robot_id,
        "success_state": filters.success_state,
        "started_from": (
            None if filters.started_from is None else filters.started_from.isoformat()
        ),
        "started_to": None if filters.started_to is None else filters.started_to.isoformat(),
        "included": filters.included,
        "review_status": list(filters.review_statuses),
        "has_finding": filters.has_finding,
        "change_type": list(filters.change_types),
    }


def _projection_snapshot_id(
    *, kind: str, filters: dict[str, object], identifiers: tuple[str, ...]
) -> str:
    return f"snapshot_{canonical_hash({'kind': kind, 'filters': filters, 'records': identifiers})}"


def _public_episode(record: DatasetPageEpisodeRecord) -> DatasetPageEpisodeListItem:
    return DatasetPageEpisodeListItem(
        scope=record.scope,
        dataset_id=record.dataset_id,
        version_id=record.version_id,
        episode_id=record.episode_id,
        selected_revision=record.selected_revision,
        included=record.included,
        success_state=record.success_state,
        task=record.task,
        robot_id=record.robot_id,
        review_status=record.review_status,
        review_finding_count=record.review_finding_count,
    )


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _filters_document(filters: DatasetPageFilters) -> dict[str, object]:
    return {
        "q": filters.query,
        "robot_model_id": filters.robot_model_id,
        "robot_id": filters.robot_id,
        "task": filters.task,
        "scene": filters.scene,
        "asset_state": filters.asset_state,
        "storage_class": filters.storage_class,
        "channels": list(filters.channels),
        "channel_match": filters.channel_match,
        "created_from": None if filters.created_from is None else filters.created_from.isoformat(),
        "created_to": None if filters.created_to is None else filters.created_to.isoformat(),
    }


def _facets(values: Iterable[object]) -> tuple[DatasetPageFacetValue, ...]:
    counts = Counter(str(value) for value in values if isinstance(value, str) and value)
    return tuple(
        DatasetPageFacetValue(value=value, count=str(count))
        for value, count in sorted(counts.items())
    )


def _snapshot_id(records: tuple[DatasetPageRecord, ...], filters: DatasetPageFilters) -> str:
    payload = {
        "filters": _filters_document(filters),
        "records": [f"{record.dataset_id}:{record.etag}" for record in records],
    }
    return f"snapshot_{canonical_hash(payload)}"
