"""Concurrent byte-identical uploads may only dispatch one whole Episode."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4, uuid5

import psycopg
import pytest

from hc_data_platform.continuous_recordings.asset_models import (
    CreateRecordingUploadCommand,
    RecordingAsset,
    RecordingUpload,
)
from hc_data_platform.continuous_recordings.models import RecordingScope
from hc_data_platform.continuous_recordings.quality import RecordingQualityService
from hc_data_platform.continuous_recordings.registration import (
    PostgresRecordingRegistration,
    VerifiedRecordingFacts,
)
from hc_data_platform.ingest.models import utc_now
from hc_data_platform.lerobot_imports.mcap_original import ORIGINAL_PATH
from hc_data_platform.security.audit import canonical_hash
from tests.continuous_recordings.test_continuous_recordings import _asset_manifest


def register(cursor, scope, original_bytes):
    uid, now = uuid4(), utc_now()
    command = CreateRecordingUploadCommand(
        recording_id=f"recording-{uid}",
        data_package_id=f"package-{uid}",
        rollout_id=f"rollout-{uid}",
        collection_job_id=f"job-{uid}",
        collection_task_id="task",
        robot_id="robot",
        device_id="robot",
        capture_started_at=now,
        capture_ended_at=now + timedelta(seconds=1),
        recording_config=dict(
            recorder_version="1",
            primary_clock_domain="robot",
            cameras=[
                dict(
                    camera_id="front",
                    topic="/front",
                    fps=30,
                    codec="h264",
                    clock_domain="robot",
                    time_base_denominator=30,
                )
            ],
        ),
        assets=(
            _asset_manifest(
                "front.mp4", "RAW_VIDEO", b"video", media_type="video/mp4", camera_id="front"
            ),
            _asset_manifest("sensor.mcap", "SENSOR_DATA", b"sensor", media_type="application/mcap"),
            _asset_manifest(
                "config.json", "RECORDING_CONFIG", b"{}", media_type="application/json"
            ),
            _asset_manifest(
                ORIGINAL_PATH, "AUXILIARY", original_bytes, media_type="application/mcap"
            ),
        ),
    )
    upload = RecordingUpload(
        scope=scope,
        upload_id=uid,
        command=command,
        created_by="test",
        manifest_sha256=canonical_hash(command.model_dump(mode="json")),
        status="COMMITTED",
    )
    assets = tuple(
        RecordingAsset(
            scope=scope,
            upload_id=uid,
            asset_id=uuid5(uid, a.path),
            manifest=a,
            object_key=f"{uid}/{a.path}",
            status="COMMITTED",
            committed_at=now,
            object_etag=a.sha256,
            multipart_upload_id="test",
        )
        for a in command.assets
    )
    recording = PostgresRecordingRegistration().register(
        cursor, VerifiedRecordingFacts(upload, assets)
    )
    return dict(
        **scope.model_dump(),
        recording_id=recording.recording_id,
        source_sha256=recording.source_sha256,
        workflow_id=recording.quality_workflow_id,
    )


@pytest.mark.integration
def test_one_episode_for_simultaneous_duplicate_uploads():
    dsn = os.environ.get("HC_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")

    def connect():
        return psycopg.connect(dsn)

    scope = RecordingScope(
        organization_id=f"org-{uuid4().hex}", project_id="project", region_code="global"
    )
    with connect() as db:
        db.execute(
            "INSERT INTO registry.organization_projects "
            "(organization_id,project_id,display_name) VALUES (%s,%s,'test')",
            (scope.organization_id, scope.project_id),
        )
        with db.cursor() as cursor:
            requests = [register(cursor, scope, data) for data in (b"same", b"same", b"diff")]

    barrier = Barrier(2)

    def inspect(request, **kwargs):
        if request["recording_id"] in [r["recording_id"] for r in requests[:2]]:
            barrier.wait(timeout=10)
        return dict(
            status="PASS", finding_codes=[], warning_codes=[], quality_report={"findings": []}
        )

    service = RecordingQualityService(
        connect,
        SimpleNamespace(resolve_recording=lambda **kw: kw),
        SimpleNamespace(inspect_quality=inspect),
    )
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(service.run, requests[:2]))
    assert sorted(r["status"] for r in results) == ["PASS", "REJECT"]
    winner, rejected = sorted(
        zip(requests[:2], results, strict=True), key=lambda pair: pair[1]["status"]
    )
    assert rejected[1]["finding_codes"] == ["MCAP_DUPLICATE_RECORDING"]
    assert service.run(rejected[0]) == rejected[1]
    assert service.run(requests[2])["status"] == "PASS"
    with connect() as db:
        episodes = db.execute(
            "SELECT recording_id FROM ingest.recording_episode_processing WHERE organization_id=%s",
            (scope.organization_id,),
        ).fetchall()
        assert sorted(r[0] for r in episodes) == sorted(
            [winner[0]["recording_id"], requests[2]["recording_id"]]
        )
        (doc,) = db.execute(
            "SELECT report_document FROM ingest.recording_quality_reports "
            "WHERE organization_id=%s AND recording_id=%s",
            (scope.organization_id, rejected[0]["recording_id"]),
        ).fetchone()
        assert doc["duplicate_of_recording_id"] == winner[0]["recording_id"]
