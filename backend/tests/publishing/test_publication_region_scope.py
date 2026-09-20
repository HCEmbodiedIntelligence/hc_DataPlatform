from unittest.mock import Mock

import pytest

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    current_request_context,
    reset_request_context,
)
from hc_data_platform.publishing import router
from hc_data_platform.publishing.models import PublishDatasetRequestV1
from hc_data_platform.security.auth import AuthContext


@pytest.mark.parametrize("operation", ["publication_preflight", "publish_dataset"])
def test_publication_preserves_verified_region_for_approved_annotation_reads(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    def consume(_request: PublishDatasetRequestV1) -> object:
        context = current_request_context()
        assert (context.project_id, context.region_code) == ("project-g1", "global")
        return object()

    publisher = Mock(preflight=consume, publish=consume)
    monkeypatch.setattr(router, "_publisher", publisher)
    token = bind_request_context(RequestContext(project_id="project-g1", region_code="global"))
    try:
        getattr(router, operation)(
            PublishDatasetRequestV1(
                project_id="project-g1",
                dataset_id="dataset-g1",
                dataset_version="v1",
                base_lance_version="1",
            ),
            AuthContext(
                subject_id="publisher",
                project_ids=frozenset({"project-g1"}),
                region_codes=frozenset({"global"}),
                scope_pairs=frozenset({("project-g1", "global")}),
                capabilities=frozenset({"dataset_version.publish"}),
            ),
            region_code="global",
        )
    finally:
        reset_request_context(token)


@pytest.mark.parametrize("changed", [False, True])
def test_approved_episode_survives_append_only_dataset_versions(changed: bool) -> None:
    from datetime import datetime, timezone

    from hc_data_platform.annotation.models import AnnotationApprovedV1
    from hc_data_platform.core.errors import ProblemException
    from hc_data_platform.lance_catalog.models import RolloutLineage
    from hc_data_platform.publishing.adapters import ApprovedAnnotationSnapshotAdapter

    approved = AnnotationApprovedV1(
        project_id="project-g1",
        dataset_id="dataset-g1",
        dataset_version=1,
        rollout_id="episode-0",
        task_id="annotation-0",
        annotation_revision=1,
        submission_id="submission-0",
        review_id="review-0",
        reviewer_id="reviewer",
        excluded_ranges=(),
        approved_at=datetime.now(timezone.utc),
    )

    def lineage(_dataset: str, _rollout: str, *, version: int, project_id: str) -> RolloutLineage:
        return RolloutLineage(
            project_id=project_id,
            dataset_id="dataset-g1",
            dataset_version=version,
            rollout_id="episode-0",
            source_sha256="a" * 64,
            converter_version="native/1",
            schema_snapshot_id="g1",
            schema_fingerprint="b" * 64,
            fragment_uri="local/fragment",
            fragment_content_hash=("d" if changed and version == 2 else "c") * 64,
            dataset_uri="local/dataset",
            lance_version=version,
        )

    adapter = ApprovedAnnotationSnapshotAdapter(
        annotations=Mock(get_approved_revision=Mock(return_value=approved)),
        catalog=Mock(lineage=lineage),
    )
    args = dict(
        project_id="project-g1", dataset_id="dataset-g1", rollout_id="episode-0", lance_version="2"
    )
    if changed:
        with pytest.raises(ProblemException) as exc:
            adapter.get_approved_revision(**args)
        assert exc.value.problem.code == "ANNOTATION_SNAPSHOT_SCOPE_MISMATCH"
    else:
        snapshot = adapter.get_approved_revision(**args)
        assert snapshot is not None and snapshot.annotation_submission_id == "submission-0"
