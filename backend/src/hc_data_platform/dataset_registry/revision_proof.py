"""Dataset-owned proof of one persisted immutable revision row."""

from typing import Any

from hc_data_platform.episode_store.models import digest

from .models import DatasetPageEpisodeRevision


def revision_row_hash(cursor: Any, revision: DatasetPageEpisodeRevision) -> str:
    cursor.execute(
        """SELECT to_jsonb(r) FROM dataset_registry.dataset_version_episode_revisions r
        WHERE organization_id=%s AND project_id=%s AND region_code=%s
          AND dataset_id=%s AND version_id=%s AND revision_id=%s""",
        (
            revision.scope.organization_id,
            revision.scope.project_id,
            revision.scope.region_code,
            revision.dataset_id,
            revision.version_id,
            revision.revision_id,
        ),
    )
    row = cursor.fetchone()
    if (
        row is None
        or DatasetPageEpisodeRevision.model_validate(row[0]["revision_document"]) != revision
    ):
        raise ValueError("authorized revision differs from its persisted proof")
    return str(digest(row[0]))
