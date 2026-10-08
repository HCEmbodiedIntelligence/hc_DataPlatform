"""Atomic publication composition: participants own their facts and SQL.

Only the PostgreSQL factory knows the shared cursor. The orchestration below
consumes domain ports and can use different implementations without importing
routers, private methods, or internal table layouts.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from hc_data_platform.continuous_recordings.publication import RecordingPublicationLineage
from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.dataset_registry.publication import (
    DatasetOwnerLookup,
    DatasetReleaseProjection,
    DatasetReleaseProjectionPort,
)
from hc_data_platform.publishing.models import PublishedDatasetManifestV1
from hc_data_platform.publishing.transactions import PublicationSideEffects
from hc_data_platform.security.access_models import (
    AccountNotificationKind,
    AccountNotificationResourceType,
)
from hc_data_platform.security.notification_writer import (
    AccountNotificationCommand,
    AccountNotificationWriter,
)

if TYPE_CHECKING:
    from psycopg import Cursor


@dataclass(frozen=True)
class ComposedPublicationSideEffects:
    lineage: RecordingPublicationLineage
    datasets: DatasetReleaseProjectionPort
    owners: DatasetOwnerLookup
    notifications: AccountNotificationWriter

    def prepare_sources(self, manifest: PublishedDatasetManifestV1) -> None:
        for rollout in manifest.rollouts:
            self.lineage.prepare_rollout(
                project_id=manifest.project_id,
                dataset_id=manifest.dataset_id,
                rollout_id=rollout.rollout_id,
                source_sha256=rollout.source_mcap_sha256,
            )

    def project_release(self, manifest: PublishedDatasetManifestV1) -> None:
        # Compatibility translation belongs at the integration boundary. The
        # dataset projection accepts only its own stable internal version ID.
        base = manifest.base_lance_version
        source_version_id = (
            base
            if base.startswith("version_lance_")
            else ("version_lance_" + base.removeprefix("lance-v").removeprefix("v"))
        )
        request = DatasetReleaseProjection(
            project_id=manifest.project_id,
            dataset_id=manifest.dataset_id,
            source_version_id=source_version_id,
            publication_version=manifest.dataset_version,
            publication_hash=manifest.content_hash,
            published_at=manifest.created_at,
            rollout_count=len(manifest.rollouts),
        )
        try:
            self.datasets.project_release(request)
        except ProblemException as exc:
            if exc.problem.code != "PUBLICATION_BASE_PROJECTION_MISSING":
                raise
            # Keep the old wire diagnostic while the dataset-owned API uses its ID.
            raise ProblemException(
                exc.problem.model_copy(
                    update={"details": {"base_lance_version": manifest.base_lance_version}}
                )
            ) from exc

    def notify_owner(self, manifest: PublishedDatasetManifestV1) -> None:
        context = current_request_context()
        if context.organization_id is None or context.region_code is None:
            return
        owner = self.owners.owner_id(
            organization_id=context.organization_id,
            project_id=manifest.project_id,
            region_code=context.region_code,
            dataset_id=manifest.dataset_id,
        )
        if owner is None:
            return
        try:
            recipient = UUID(owner)
        except ValueError:
            return
        resource_id = f"{manifest.dataset_id}:{manifest.dataset_version}"
        if len(resource_id) > 512:
            resource_id = f"sha256:{manifest.content_hash}"
        event_identity = "\0".join(
            (
                manifest.project_id,
                manifest.dataset_id,
                manifest.dataset_version,
                manifest.content_hash,
            )
        )
        event_key = (
            "dataset-publication:v1:" + hashlib.sha256(event_identity.encode("utf-8")).hexdigest()
        )
        self.notifications.put_once(
            AccountNotificationCommand(
                recipient_id=recipient,
                organization_id=context.organization_id,
                project_id=manifest.project_id,
                kind=AccountNotificationKind.DATASET_VERSION_PUBLISHED,
                resource_type=AccountNotificationResourceType.DATASET_VERSION,
                resource_id=resource_id,
                event_key=event_key,
            )
        )


def postgres_publication_effects(cursor: Cursor[Any]) -> PublicationSideEffects:
    """Bind every adapter to exactly the cursor the publication repository owns."""
    from hc_data_platform.continuous_recordings.lineage import PostgresRecordingPublicationLineage
    from hc_data_platform.dataset_registry.publication_postgres import (
        PostgresDatasetPublicationProjection,
    )
    from hc_data_platform.security.notification_postgres import PostgresAccountNotificationWriter

    dataset = PostgresDatasetPublicationProjection(cursor)
    return ComposedPublicationSideEffects(
        lineage=PostgresRecordingPublicationLineage(cursor),
        datasets=dataset,
        owners=dataset,
        notifications=PostgresAccountNotificationWriter(cursor),
    )
