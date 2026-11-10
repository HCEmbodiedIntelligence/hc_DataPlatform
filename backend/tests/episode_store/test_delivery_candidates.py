"""Completed annotations filter exact references before counting and paging."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.episode_store.delivery import EpisodeDeliveryService
from hc_data_platform.episode_store.models import (
    CollectionSnapshot,
    DataVersionRef,
    EpisodeIdentity,
)
from hc_data_platform.security import AuthContext


@pytest.fixture
def candidates():
    entries = tuple(
        DataVersionRef(
            identity=EpisodeIdentity(
                organization_id="org",
                project_id="project",
                region_code="region",
                dataset_id="dataset",
                episode_id=f"episode-{i}",
                rollout_id=f"rollout-{i}",
            ),
            data_version_id=f"dv-{i}",
            version=1,
            content_hash="a" * 64,
            schema_snapshot_id="schema",
            package_prefix=f"episodes/{i}",
            frame_count=10,
        )
        for i in range(44)
    )
    snapshot = CollectionSnapshot(
        organization_id="org",
        project_id="project",
        region_code="region",
        dataset_id="dataset",
        collection_version_id="cv-fixed",
        version=1,
        schema_snapshot_id="schema",
        schema_fingerprint="b" * 64,
        frequency_hz=30,
        episodes=entries,
    )
    collections = Mock()
    collections.current.return_value = collections.get.return_value = snapshot
    repository = Mock()
    repository.approved_data_versions.return_value = (entries[40], entries[43])
    service = EpisodeDeliveryService(
        SimpleNamespace(collections=collections, files=SimpleNamespace(storage=Mock())),
        repository,
    )
    auth = AuthContext(
        subject_id="reader",
        service_identity=True,
        organization_ids=frozenset({"org"}),
        project_ids=frozenset({"project"}),
        region_codes=frozenset({"region"}),
        scope_pairs=frozenset({("project", "region")}),
        organization_scope_triples=frozenset({("org", "project", "region")}),
        capabilities=frozenset({"dataset_version.read", "export.download", "annotation_task.read"}),
    )
    token = bind_request_context(
        RequestContext(
            organization_id="org",
            project_id="project",
            region_code="region",
            subject_id="reader",
        )
    )
    try:
        yield service, auth, entries
    finally:
        reset_request_context(token)


def test_approved_entries_after_first_raw_page_are_counted_and_paginated(candidates):
    service, auth, entries = candidates
    first = service.candidates("dataset", auth, data_stage="annotated", limit=1)
    assert first.total == 2
    assert first.items == (entries[40],)
    assert first.next_offset == 1
    second = service.candidates(
        "dataset",
        auth,
        data_stage="annotated",
        collection_version_id=first.collection_version_id,
        offset=first.next_offset,
        limit=1,
    )
    assert second.total == 2
    assert second.items == (entries[43],)
    assert second.next_offset is None
    assert service.candidates("dataset", auth, limit=100).items == entries


def test_old_approval_cannot_qualify_a_changed_data_reference(candidates):
    service, auth, entries = candidates
    service.repository.approved_data_versions.return_value = (
        entries[40].model_copy(update={"version": 2}),
        entries[43].model_copy(update={"data_version_id": "old-data-version"}),
    )
    page = service.candidates("dataset", auth, data_stage="annotated")
    assert page.total == 0
    assert page.items == ()
    assert page.next_offset is None


def test_annotated_candidates_require_annotation_access(candidates):
    service, auth, _ = candidates
    auth = replace(auth, capabilities=auth.capabilities - {"annotation_task.read"})
    with pytest.raises(ProblemException, match="CAPABILITY_REQUIRED"):
        service.candidates("dataset", auth, data_stage="annotated")
    service.repository.approved_data_versions.assert_not_called()
    assert service.candidates("dataset", auth).total == 44
