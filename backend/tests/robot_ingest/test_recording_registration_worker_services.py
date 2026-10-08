"""Real runtime outbox/Temporal worker registers verified Raw for HTTP slicing."""

import time

import httpx
import pytest

from hc_data_platform.continuous_recordings.asset_models import RecordingAsset
from hc_data_platform.robot_ingest import raw_recording
from hc_data_platform.robot_ingest.recording_bridge import recording_upload_id, result
from tests.episode_store.test_http_services import register_and_grant
from tests.robot_ingest.test_raw_derivation_services import uploaded_raw  # noqa: F401

pytest_plugins = ["tests.episode_store.test_commit_services"]


@pytest.mark.integration
def test_runtime_worker_registers_raw_and_http_reads_verified_assets(uploaded_raw):  # noqa: F811
    repository, storage, upload, _, command, _ = uploaded_raw
    deadline = time.monotonic() + 120
    while True:
        state = result(repository.connections, upload)
        if state["terminal"] or time.monotonic() >= deadline:
            break
        time.sleep(0.5)
    assert state["status"] == "AWAITING_SLICE", state
    assert state["recording_id"] == command.recording_id
    assert state["error_code"] is None
    scope = upload.target
    path = (
        f"/api/v1/projects/{scope.project_id}/regions/{scope.region_code}"
        f"/continuous-recordings/{command.recording_id}"
    )
    with httpx.Client(base_url="http://gateway:8080", timeout=45, trust_env=False) as client:
        principal, headers = register_and_grant(
            client, repository, scope, capabilities=["upload.read"]
        )
        response = client.get(path, headers=headers)
        assert response.status_code == 200, response.text
        record = response.json()["data"]
        assert record["recording_upload_id"] == str(recording_upload_id(upload.upload_id))
        assert record["robot_id"] == upload.authenticated_robot_id
        assert record["collection_job_id"] == upload.collection_job_id
        assert record["current_revision"] == 0
        assert client.get(path).status_code == 401
        assert (
            client.get(path, headers={**headers, "X-Organization-Id": "foreign"}).status_code == 403
        )
        with repository.transaction() as cursor:
            cursor.execute(
                "SELECT asset_document FROM ingest.recording_upload_assets "
                "WHERE organization_id=%s AND upload_id=%s",
                (scope.organization_id, recording_upload_id(upload.upload_id)),
            )
            assets = [RecordingAsset.model_validate(row[0]) for row in cursor.fetchall()]
            cursor.execute(
                "UPDATE access_control.capability_grants "
                "SET active=false,revoked_at=clock_timestamp() "
                "WHERE principal_id=%s AND capability_key='upload.read'",
                (principal,),
            )
        assert assets
        for asset in assets:
            raw_recording.verify(
                storage, asset.object_key, asset.manifest.size, asset.manifest.sha256, lambda: None
            )
        assert client.get(path, headers=headers).status_code == 403
