from datetime import datetime, timezone

import pytest

from hc_data_platform.collection_tasks.capture_facts import combine_capture_facts
from hc_data_platform.collection_tasks.captures import CaptureProgress
from hc_data_platform.collection_tasks.models import ProgressFacts


@pytest.mark.parametrize(
    ("legacy_durations", "capture_durations", "received", "expected_duration"),
    [
        pytest.param((), (), True, None, id="empty-task"),
        pytest.param((), (12.0,), False, None, id="uncommitted-upload"),
        pytest.param((), (0.0,), True, 0.0, id="observed-zero-duration"),
        pytest.param((5.0,), (), True, 5.0, id="legacy-only"),
        pytest.param((), (12.0,), True, 12.0, id="capture-only"),
        pytest.param((5.0,), (12.0,), True, 17.0, id="combined-known-durations"),
        pytest.param((5.0,), (None,), True, None, id="capture-duration-missing"),
        pytest.param((None,), (12.0,), True, None, id="legacy-duration-missing"),
    ],
)
def test_progress_duration_requires_complete_observations(
    legacy_durations, capture_durations, received, expected_duration
):
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    legacy_known = tuple(value for value in legacy_durations if value is not None)
    legacy = ProgressFacts(
        as_of=now,
        received_package_count=len(legacy_durations),
        pass_count=0,
        risk_count=0,
        reject_count=0,
        captured_duration_seconds=(
            sum(legacy_known)
            if legacy_known and len(legacy_known) == len(legacy_durations)
            else None
        ),
        duration_observed_package_count=len(legacy_known),
        duration_unknown_package_count=len(legacy_durations) - len(legacy_known),
    )
    captures = tuple(
        CaptureProgress(
            upload_id=f"upload-{index}",
            capture_mode="CONTINUOUS_RECORDING",
            rollout_id=f"rollout-{index}",
            data_package_id=f"package-{index}",
            collection_task_id="task-1",
            robot_id="robot-1",
            dataset_id="dataset_task_1",
            upload_state="COMMITTED" if received else "UPLOADING",
            stage="RECEIVED" if received else "UPLOADING",
            received=received,
            duration_seconds=duration,
            episode_count=0,
            ready_episode_count=0,
            failed_episode_count=0,
            created_at=now,
        )
        for index, duration in enumerate(capture_durations)
    )

    progress = combine_capture_facts(legacy, captures)

    assert progress.captured_duration_seconds == expected_duration
    assert progress.received_package_count == len(legacy_durations) + (
        len(captures) if received else 0
    )
    assert progress.pending_count == progress.received_package_count
    assert progress.captures.upload_count == len(captures)
    assert progress.captures.received_count == (len(captures) if received else 0)
