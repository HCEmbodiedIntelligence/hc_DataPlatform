from unittest.mock import Mock
from uuid import UUID

import pytest

from hc_data_platform.continuous_recordings.publication import RecordingPublicationLineage
from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.errors import ProblemException, problem
from hc_data_platform.dataset_registry.publication import (
    DatasetOwnerLookup,
    DatasetReleaseProjectionPort,
)
from hc_data_platform.integrations.publication import ComposedPublicationSideEffects
from hc_data_platform.security.notification_writer import AccountNotificationWriter
from tests.publishing.test_export_job_api import _manifest


@pytest.mark.parametrize("source", ["1", "v1", "lance-v1", "version_lance_1"])
def test_participants_can_be_replaced_without_publication_or_database_internals(source):
    lineage = Mock(spec=RecordingPublicationLineage)
    datasets = Mock(spec=DatasetReleaseProjectionPort)
    owners = Mock(spec=DatasetOwnerLookup)
    notifications = Mock(spec=AccountNotificationWriter)
    owners.owner_id.return_value = "11111111-1111-4111-8111-111111111111"
    effects = ComposedPublicationSideEffects(lineage, datasets, owners, notifications)
    manifest = _manifest().model_copy(update={"base_lance_version": source})
    token = bind_request_context(
        RequestContext(
            organization_id="org-a",
            project_id=manifest.project_id,
            region_code="cn-test",
            subject_id="publisher",
            request_id="publication-test",
        )
    )
    try:
        effects.prepare_sources(manifest)
        effects.project_release(manifest)
        effects.notify_owner(manifest)
    finally:
        reset_request_context(token)
    request = datasets.project_release.call_args.args[0]
    assert request.source_version_id == "version_lance_1"
    assert request.publication_hash == manifest.content_hash
    assert request.publication_version == manifest.dataset_version
    assert request.rollout_count == 1
    lineage.prepare_rollout.assert_called_once_with(
        project_id=manifest.project_id,
        dataset_id=manifest.dataset_id,
        rollout_id="rollout-a",
        source_sha256="d" * 64,
    )
    owners.owner_id.assert_called_once_with(
        organization_id="org-a",
        project_id=manifest.project_id,
        region_code="cn-test",
        dataset_id=manifest.dataset_id,
    )
    notice = notifications.put_once.call_args.args[0]
    assert notice.recipient_id == UUID(owners.owner_id.return_value)
    assert notice.resource_id == "dataset-a:v1"
    assert notice.organization_id == "org-a"
    assert notice.project_id == manifest.project_id


def test_projection_translation_keeps_legacy_error_details():
    datasets = Mock(spec=DatasetReleaseProjectionPort)
    datasets.project_release.side_effect = problem(
        status=409,
        code="PUBLICATION_BASE_PROJECTION_MISSING",
        title="Missing projection",
        detail="No internal source version",
        details={"source_version_id": "version_lance_7"},
    )
    effects = ComposedPublicationSideEffects(Mock(), datasets, Mock(), Mock())
    manifest = _manifest().model_copy(update={"base_lance_version": "lance-v7"})
    with pytest.raises(ProblemException) as captured:
        effects.project_release(manifest)
    assert captured.value.problem.details == {"base_lance_version": "lance-v7"}


@pytest.mark.parametrize("owner", [None, "robot-owner"])
def test_missing_or_non_account_owner_does_not_manufacture_a_recipient(owner):
    owners, notifications = Mock(spec=DatasetOwnerLookup), Mock(spec=AccountNotificationWriter)
    owners.owner_id.return_value = owner
    effects = ComposedPublicationSideEffects(Mock(), Mock(), owners, notifications)
    token = bind_request_context(
        RequestContext(
            organization_id="org-a",
            project_id="project-a",
            region_code="cn-test",
            request_id="test",
        )
    )
    try:
        effects.notify_owner(_manifest())
    finally:
        reset_request_context(token)
    notifications.put_once.assert_not_called()
