"""Bounded robot recording inventory and authenticated capability negotiation."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.robot_ingest.models import (
    CreateRobotIngestIdentity,
    IssueCredentialCommand,
    RobotIngestUploadPolicy,
)
from hc_data_platform.robot_ingest.recording_contract import OpenArmRecordingComplete
from hc_data_platform.robot_ingest.router import configure_robot_ingest

from .test_robot_ingest_api import ORG, PROJECT, ROBOT, _auth, _service


def marker(count):
    paths = ["manifest.json", "raw.mcap", "session.json"] + [
        f"auxiliary/{index:04d}.bin" for index in range(count - 3)
    ]
    return {
        "schema_version": "openarm-recording-complete/v2",
        "export_id": str(uuid4()),
        "session_id": "capacity",
        "robot_id": ROBOT,
        "local_robot_id": "local",
        "collection_task_id": "task",
        "synthetic": True,
        "content_sha256": "0" * 64,
        "recording_upload": {
            "schema_version": "openarm-raw-recording-upload/v1",
            "recording_id": "recording",
            "rollout_id": "rollout",
            "data_package_id": "package",
            "collection_job_id": "job",
            "collection_task_id": "task",
            "robot_id": ROBOT,
            "device_id": "local",
            "capture_started_at": "2026-09-29T00:00:00Z",
            "capture_ended_at": "2026-09-29T00:00:01Z",
            "assets": [
                {
                    "path": path,
                    "role": "AUXILIARY",
                    "media_type": "application/octet-stream",
                    "size": 1,
                    "sha256": "0" * 64,
                    "crc64": 0,
                    "part_count": 1,
                }
                for path in paths
            ],
        },
    }


def test_completion_marker_is_counted_in_total_capacity():
    assert (
        len(OpenArmRecordingComplete.model_validate(marker(4095)).recording_upload.assets) == 4095
    )
    with pytest.raises(ValidationError):
        OpenArmRecordingComplete.model_validate(marker(4096))
    assert len(OpenArmRecordingComplete.model_validate(marker(3)).recording_upload.assets) == 3


@pytest.mark.parametrize(
    "limit,formats,recording_limit",
    [
        (None, ("CAPTURE_BUNDLE",), 4095),
        (256, ("CAPTURE_BUNDLE",), 255),
        (4096, ("MCAP",), 0),
    ],
)
def test_capabilities_keep_authenticated_policy_and_existing_quotas(
    limit, formats, recording_limit
):
    service = _service()
    policy = (
        RobotIngestUploadPolicy() if limit is None else RobotIngestUploadPolicy(max_assets=limit)
    )
    identity = service.create_identity(
        auth=_auth(),
        organization_id=ORG,
        project_id=PROJECT,
        command=CreateRobotIngestIdentity(
            robot_id=ROBOT, allowed_formats=formats, upload_policy=policy
        ),
    ).data
    credential = service.issue_credential(
        auth=_auth(),
        organization_id=ORG,
        project_id=PROJECT,
        ingest_identity_id=identity.ingest_identity_id,
        command=IssueCredentialCommand(),
    ).credential
    configure_robot_ingest(service)
    app = create_app(settings=Settings(environment="test", runtime_backend="memory"))
    with TestClient(app) as client:
        path = "/api/v1/robot-ingest/uploads:capabilities"
        for headers in ({}, {"Authorization": "Bearer user-session"}):
            assert client.get(path, headers=headers).status_code == 401
        response = client.get(path, headers={"Authorization": f"Bearer {credential.token}"})
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {
            "schema_version": "robot-ingest-capabilities/v1",
            "robot_id": ROBOT,
            "max_total_assets": policy.max_assets,
            "max_recording_assets": recording_limit,
        }
