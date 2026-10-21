"""Robot operations can use a plan provider without database or decoder internals."""

import pytest

from hc_data_platform.capture_schemes.ports import TaskCapturePreview
from hc_data_platform.capture_schemes.references import PrepareRecording
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.robot_ingest.service import RobotIngestService
from tests.robot_ingest.test_connections import harness
from tests.robot_ingest.test_robot_ingest_service import (
    ORG,
    PROJECT_A,
    REGION,
    ROBOT,
    TASK_A,
    _target,
)


class PlanProvider:
    def __init__(self):
        self.reads = []
        self.preparations = []

    def preview(self, scope, task_id, robot_id, source_format):
        self.reads.append((scope, task_id, robot_id, source_format))
        return TaskCapturePreview(
            plan={"revision": 7, "scheme": {"scheme_id": "shared", "version": 3}},
            processing_config={"source": "TASK_PLAN"},
        )

    def prepare(self, scope, task_id, robot_id, command, actor):
        self.preparations.append((scope, task_id, robot_id, command, actor))
        return {
            "recording_id": command.recording_id,
            "plan_revision": command.expected_plan_revision,
        }


def test_injected_recording_provider_keeps_discovery_read_only_and_scoped():
    h, provider = harness(), PlanProvider()
    service = RobotIngestService(
        h.repository,
        h.storage,
        credential_hmac_key="robot-ingest-test-hmac-key",
        clock=lambda: h.now,
        capture_plans=provider,
    )
    auth = service.authenticate(h.token)
    task = service.connections.task(auth, TASK_A, "MCAP", require_processing_config=True)
    assert task["selectable"] and task["capture_plan"]["revision"] == 7
    assert provider.reads == [((ORG, PROJECT_A, REGION), TASK_A, ROBOT, "MCAP")]
    assert provider.preparations == []
    command = PrepareRecording(
        expected_plan_revision=7, recording_id="recording-1", source_format="MCAP"
    )
    result = service.connections.prepare_recording(auth, TASK_A, command)
    assert result == {"recording_id": "recording-1", "plan_revision": 7}
    assert provider.preparations == [
        ((ORG, PROJECT_A, REGION), TASK_A, ROBOT, command, "robot:" + h.identity_id)
    ]
    h.repository.add_target(
        _target("foreign", PROJECT_A).model_copy(update={"organization_id": "other"})
    )
    with pytest.raises(ProblemException) as exc:
        service.connections.prepare_recording(auth, "foreign", command)
    assert exc.value.problem.status == 404
    assert len(provider.preparations) == 1


def test_missing_provider_is_visible_and_cannot_start_recording():
    h = harness()
    auth = h.service.authenticate(h.token)
    task = h.service.connections.task(auth, TASK_A, "MCAP")
    assert not task["selectable"] and task["reason"] == "CAPTURE_STORE_UNAVAILABLE"
    with pytest.raises(ProblemException) as exc:
        h.service.connections.prepare_recording(
            auth,
            TASK_A,
            PrepareRecording(
                expected_plan_revision=1, recording_id="recording-1", source_format="MCAP"
            ),
        )
    assert exc.value.problem.code == "CAPTURE_STORE_UNAVAILABLE"
