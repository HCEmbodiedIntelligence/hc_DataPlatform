from __future__ import annotations

from datetime import datetime, timezone

import pytest

from hc_data_platform.cleaning_drafts.models import CleaningDraftScope
from hc_data_platform.cleaning_drafts.repository import (
    CleaningDraftProjectionIntegrityError,
    _projection_from_row,
)

NOW = datetime(2026, 8, 19, 22, tzinfo=timezone.utc)


def _scope() -> CleaningDraftScope:
    return CleaningDraftScope(
        organization_id="p10-repository-organization",
        project_id="p10-repository-project",
        region_code="p10-repository-region",
    )


def _returned_row() -> dict[str, object]:
    return {
        "draft_id": "draft_p10repository",
        "origin_type": "ISSUE_DERIVED",
        "source_issue_id": "issue_p10repository",
        "dataset_id": "dataset_p10repository",
        "base_version_id": "version_p10base",
        "episode_id": "episode_p10repository",
        "base_revision_id": "revision_p10base",
        "selected_stream_id": "stream_p10repository",
        "selected_channel_path": "joint.position",
        "start_ns": "100",
        "end_ns": "1000",
        "status": "COMMITTED",
        "projection_version": 2,
        "created_at": NOW,
        "updated_at": NOW,
        "creator_id": "p10-repository-operator",
        "robot_id": "robot_p10repository",
        "commit_id": "commit_p10repository",
        "commit_status": "SUCCEEDED",
        "output_version_id": "version_p10returned",
        "output_version_status": "RETURNED",
        "review_decision_id": "review_decision_p10repository",
        "successor_draft_id": "draft_p10successor",
        "review_supersedes_draft_id": "draft_p10repository",
        "review_returned_from_version_id": "version_p10returned",
        "review_returned_from_review_decision_id": "review_decision_p10repository",
        "review_finding_ids": ["review_finding_p10repository"],
        "output_revision_ids": ["revision_p10output"],
        "review_finding_types": ["POSE_DISCONTINUITY"],
        "review_finding_severities": ["HIGH"],
    }


def test_p10_projects_p07_return_onto_the_real_p09_source_draft() -> None:
    projection = _projection_from_row(_returned_row(), _scope())

    assert projection.draft.draft_id == "draft_p10repository"
    assert projection.draft.etag == '"draft_p10repository:workbench:2"'
    assert projection.draft.origin.origin_type == "ISSUE_DERIVED"
    assert projection.draft.output_version_status == "RETURNED"
    assert projection.draft.successor_draft_id == "draft_p10successor"
    assert projection.draft.allowed_actions == ()
    assert projection.relationships.output_revision_ids == ("revision_p10output",)
    assert projection.relationships.review_finding_ids == ("review_finding_p10repository",)
    assert projection.review_finding_types == ("POSE_DISCONTINUITY",)
    assert projection.review_finding_severities == ("HIGH",)


def test_p10_rejects_a_partial_p07_return_instead_of_fabricating_a_successor() -> None:
    row = _returned_row() | {"review_finding_ids": [], "output_revision_ids": []}

    with pytest.raises(CleaningDraftProjectionIntegrityError):
        _projection_from_row(row, _scope())
