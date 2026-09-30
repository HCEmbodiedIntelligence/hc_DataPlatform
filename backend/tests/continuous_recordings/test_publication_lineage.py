from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from hc_data_platform.continuous_recordings.lineage import PostgresRecordingPublicationLineage
from hc_data_platform.core.errors import ProblemException

SOURCE = ("org-a", "cn", "job-a", "robot-a", "recording-a", datetime.now(timezone.utc))


@pytest.mark.parametrize(
    "rows,owner,existing,code",
    [
        ([SOURCE, SOURCE], None, None, "CONTINUOUS_ROLLOUT_LINEAGE_AMBIGUOUS"),
        ([SOURCE], None, None, "CONTINUOUS_COLLECTION_JOB_MISSING"),
        ([SOURCE], ("other-robot",), None, "CONTINUOUS_COLLECTION_JOB_MISSING"),
        (
            [SOURCE],
            ("robot-a",),
            ("cn", "job-a", "robot-a", "different-hash"),
            "CONTINUOUS_ROLLOUT_IMMUTABLE",
        ),
    ],
)
def test_conflicting_source_returns_stable_error_before_writing(rows, owner, existing, code):
    cursor = Mock()
    cursor.fetchall.return_value = rows
    cursor.fetchone.side_effect = [owner, existing]
    adapter = PostgresRecordingPublicationLineage(cursor)
    with pytest.raises(ProblemException) as captured:
        adapter.prepare_rollout(
            project_id="project-a",
            dataset_id="dataset-a",
            rollout_id="rollout-a",
            source_sha256="a" * 64,
        )
    assert captured.value.problem.code == code
    assert captured.value.problem.status == 409
    assert all("INSERT" not in call.args[0] for call in cursor.execute.call_args_list)
