"""Adopt verified robot Raw objects into the existing platform slicing workbench.

No bytes are re-uploaded and no Episode is fabricated before human slicing.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import contextmanager
from uuid import NAMESPACE_URL, uuid5

from hc_data_platform.continuous_recordings.asset_models import RecordingAsset, RecordingUpload
from hc_data_platform.continuous_recordings.asset_repository import _asset_values
from hc_data_platform.continuous_recordings.models import (
    ContinuousRecording,
    RecordingScope,
    recording_duration_ns,
)
from hc_data_platform.continuous_recordings.repository import _recording_values
from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.events import DomainEventEnvelope
from hc_data_platform.security.audit import canonical_hash

from .models import RobotIngestUpload
from .recording_contract import OpenArmRecordingComplete

EVENT_TYPE = "robot.recording.register.requested.v1"


class RecordingBridgeError(RuntimeError):
    code = "ROBOT_RECORDING_REGISTRATION_FAILED"


def eligible(upload):
    return (
        upload.state.value == "COMMITTED"
        and upload.source_format == "CAPTURE_BUNDLE"
        and upload.source_format_version == "2"
        and upload.capture_mode.value == "CONTINUOUS"
        and upload.manifest.format_metadata.get("openarm_recording", {}).get("version") == 1
    )


def enqueue(cursor, upload):
    event = DomainEventEnvelope(
        event_id=str(uuid5(NAMESPACE_URL, "recording-register:" + upload.upload_id)),
        event_type=EVENT_TYPE,
        aggregate_type="raw_source",
        aggregate_id=upload.raw_source_id,
        organization_id=upload.target.organization_id,
        project_id=upload.target.project_id,
        region_code=upload.target.region_code,
        payload={"upload_id": upload.upload_id},
    )
    cursor.execute(
        """INSERT INTO core.outbox_events
        (event_id,organization_id,project_id,region_code,event_type,envelope,occurred_at,available_at)
        VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s) ON CONFLICT (event_id) DO NOTHING""",
        (
            event.event_id,
            event.organization_id,
            event.project_id,
            event.region_code,
            event.event_type,
            event.model_dump_json(),
            event.occurred_at,
            event.occurred_at,
        ),
    )


@contextmanager
def scope_context(organization_id, project_id, region_code):
    token = bind_request_context(
        RequestContext(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            subject_id="robot-recording-bridge",
            service_identity=True,
        )
    )
    try:
        yield
    finally:
        reset_request_context(token)


def recording_upload_id(upload_id):
    return uuid5(NAMESPACE_URL, "robot-recording:" + upload_id)


class RecordingOutboxHandler:
    EVENT_TYPE = EVENT_TYPE

    def __init__(self, connections, storage):
        self.connections, self.storage = connections, storage

    async def __call__(self, event):
        await asyncio.to_thread(self.adopt, event)

    def _json_asset(self, asset):
        if asset.expected_size_bytes > 16 * 1024 * 1024:
            raise RecordingBridgeError("recording metadata exceeds limit")
        value = bytearray()
        for chunk in self.storage.read_chunks(asset.object_key):
            if len(value) + len(chunk) > asset.expected_size_bytes:
                raise RecordingBridgeError("recording metadata exceeds declared size")
            value.extend(chunk)
        if (
            len(value) != asset.expected_size_bytes
            or hashlib.sha256(value).hexdigest() != asset.expected_sha256
        ):
            raise RecordingBridgeError("recording metadata differs from verified receipt")
        return json.loads(value)

    def adopt(self, event):
        if event.event_type != EVENT_TYPE:
            raise RecordingBridgeError("unexpected event")
        scope = (event.organization_id, event.project_id, event.region_code)
        with scope_context(*scope), self.connections() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT upload_document FROM ingest.robot_ingest_uploads
                WHERE organization_id=%s
                AND project_id=%s
                AND region_code=%s
                AND upload_id=%s FOR UPDATE""",
                (*scope, event.payload["upload_id"]),
            )
            row = cursor.fetchone()
            if row is None:
                raise RecordingBridgeError("upload not found in event scope")
            upload = RobotIngestUpload.model_validate(row[0])
            if not eligible(upload) or upload.raw_source_id != event.aggregate_id:
                raise RecordingBridgeError("source is not a supported completed recording")
            marker_path = upload.manifest.format_metadata["openarm_recording"]["marker_path"]
            assets = {a.path: a for a in upload.assets}
            if marker_path not in assets or any(
                a.state.value != "COMPLETED" for a in assets.values()
            ):
                raise RecordingBridgeError("assets not verified")
            marker = self._json_asset(assets[marker_path])
            command = OpenArmRecordingComplete.model_validate(marker).recording_upload
            if (
                command.robot_id != upload.authenticated_robot_id
                or command.robot_id != upload.request_robot_id
                or command.collection_task_id != upload.target.collection_task_id
                or marker["robot_id"] != command.robot_id
                or marker["collection_task_id"] != command.collection_task_id
                or marker["local_robot_id"] != command.device_id
                or command.capture_started_at != upload.manifest.capture_started_at
                or command.capture_ended_at != upload.manifest.capture_ended_at
                or command.recording_id != upload.manifest.format_metadata["recording_id"]
            ):
                raise RecordingBridgeError("recording attribution mismatch")
            if {a.path for a in command.assets} != (set(assets) - {marker_path}):
                raise RecordingBridgeError("recording inventory mismatch")
            if marker["content_sha256"] != canonical_hash(marker["recording_upload"]["assets"]):
                raise RecordingBridgeError("recording seal mismatch")
            config = command.recording_config
            if config.recorder_version != "openarm-session/v2" or not {
                "/observation/state",
                "/action",
                "/openarm/capture_validity",
            } <= {s.topic for s in config.sensors if s.required}:
                raise RecordingBridgeError("recording validity sources are required")
            declarations = {a.path: a for a in upload.manifest.assets}
            for declared in command.assets:
                actual = assets[declared.path]
                if (
                    declared.size,
                    declared.sha256,
                    str(declared.crc64),
                    declared.role.value,
                    declared.camera_id,
                ) != (
                    actual.expected_size_bytes,
                    actual.expected_sha256,
                    str(actual.expected_crc64),
                    actual.role,
                    declarations[declared.path].camera_id,
                ):
                    raise RecordingBridgeError("recording asset receipt mismatch")
                if declared.role.value == "RECORDING_CONFIG" and self._json_asset(
                    actual
                ) != command.recording_config.model_dump(mode="json"):
                    # JSON nanoseconds may be supplied as int or string. Compare typed documents.
                    from hc_data_platform.continuous_recordings.asset_models import (
                        RecordingConfigurationV1,
                    )

                    if (
                        RecordingConfigurationV1.model_validate(self._json_asset(actual))
                        != command.recording_config
                    ):
                        raise RecordingBridgeError("recording configuration mismatch")
            # The authenticated ingest service owns the collection job. Keep the
            # original client marker immutable and project the authoritative job.
            command = command.model_copy(update={"collection_job_id": upload.collection_job_id})
            target = RecordingScope(
                organization_id=scope[0], project_id=scope[1], region_code=scope[2]
            )
            uid = recording_upload_id(upload.upload_id)
            now = upload.committed_at
            recorded = RecordingUpload(
                scope=target,
                upload_id=uid,
                command=command,
                manifest_sha256=canonical_hash(command.model_dump(mode="json")),
                status="COMMITTED",
                created_by=upload.authenticated_robot_id,
                created_at=now,
                updated_at=now,
            )
            cursor.execute(
                """SELECT manifest_sha256 FROM ingest.recording_uploads
                WHERE organization_id=%s AND project_id=%s AND region_code=%s AND upload_id=%s""",
                (*scope, uid),
            )
            existing = cursor.fetchone()
            if existing and existing[0] != recorded.manifest_sha256:
                raise RecordingBridgeError("immutable recording identity conflict")
            cursor.execute(
                """INSERT INTO ingest.recording_uploads
                (organization_id,project_id,region_code,upload_id,recording_id,manifest_sha256,status,upload_document,created_by,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,'COMMITTED',%s::jsonb,%s,%s,%s)
                ON CONFLICT (organization_id,project_id,region_code,upload_id) DO NOTHING""",
                (
                    *scope,
                    uid,
                    command.recording_id,
                    recorded.manifest_sha256,
                    recorded.model_dump_json(),
                    recorded.created_by,
                    now,
                    now,
                ),
            )
            projected = []
            for declared in command.assets:
                actual = assets[declared.path]
                asset = RecordingAsset(
                    scope=target,
                    upload_id=uid,
                    asset_id=uuid5(uid, declared.path),
                    manifest=declared,
                    object_key=actual.object_key,
                    multipart_upload_id=actual.multipart_upload_id,
                    status="COMMITTED",
                    object_etag=actual.etag,
                    committed_at=now,
                )
                projected.append(asset)
                cursor.execute(
                    """INSERT INTO ingest.recording_upload_assets
                    (organization_id,project_id,region_code,upload_id,asset_id,asset_path,role,camera_id,media_type,
                     expected_size,expected_sha256,expected_crc64,object_key,multipart_upload_id,status,object_etag,committed_at,asset_document)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                    ON CONFLICT (organization_id,project_id,region_code,upload_id,asset_id)
                    DO NOTHING""",
                    (
                        *_asset_values(asset)[:-1],
                        asset.object_etag,
                        asset.committed_at,
                        asset.model_dump_json(),
                    ),
                )
            recording = ContinuousRecording(
                schema_version="continuous-recording/v2",
                scope=target,
                recording_id=command.recording_id,
                recording_upload_id=uid,
                rollout_id=command.rollout_id,
                data_package_id=command.data_package_id,
                collection_task_id=command.collection_task_id,
                collection_job_id=command.collection_job_id,
                robot_id=command.robot_id,
                device_id=command.device_id,
                capture_started_at=command.capture_started_at,
                capture_ended_at=command.capture_ended_at,
                duration_ns=recording_duration_ns(
                    command.capture_started_at, command.capture_ended_at
                ),
                source_sha256=canonical_hash(
                    [{"asset_id": str(a.asset_id), "sha256": a.manifest.sha256} for a in projected]
                ),
                manifest_fingerprint=recorded.manifest_sha256,
                video_asset_count=len(command.recording_config.cameras),
                etag='"v1"',
                created_at=now,
                updated_at=now,
            )
            cursor.execute(
                """SELECT recording_upload_id FROM ingest.continuous_recordings
                WHERE organization_id=%s
                AND project_id=%s
                AND region_code=%s
                AND recording_id=%s""",
                (*scope, recording.recording_id),
            )
            prior = cursor.fetchone()
            if prior and str(prior[0]) != str(uid):
                raise RecordingBridgeError("recording owned by another upload")
            cursor.execute(
                """INSERT INTO ingest.continuous_recordings
                (organization_id,project_id,region_code,recording_id,upload_session_id,recording_upload_id,
                 rollout_id,data_package_id,collection_task_id,collection_job_id,robot_id,device_id,
                 capture_started_at,capture_ended_at,duration_ns,source_sha256,manifest_fingerprint,video_asset_count,
                 status,current_revision,finalized_revision,resource_version,recording_document,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                %s,%s,%s::jsonb,%s,%s)
                ON CONFLICT (organization_id,project_id,region_code,recording_id) DO NOTHING""",
                _recording_values(recording),
            )
            cursor.execute(
                """UPDATE ingest.raw_ingest_jobs
                SET status='SUCCEEDED',last_error_code=NULL,updated_at=clock_timestamp()
                WHERE organization_id=%s
                AND project_id=%s
                AND region_code=%s
                AND raw_source_id=%s""",
                (*scope, upload.raw_source_id),
            )
            connection.commit()


def result(connections, upload):
    with (
        scope_context(
            upload.target.organization_id, upload.target.project_id, upload.target.region_code
        ),
        connections() as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            """SELECT recording_id,status FROM ingest.continuous_recordings
            WHERE organization_id=%s
            AND project_id=%s
            AND region_code=%s
            AND recording_upload_id=%s""",
            (
                upload.target.organization_id,
                upload.target.project_id,
                upload.target.region_code,
                recording_upload_id(upload.upload_id),
            ),
        )
        row = cursor.fetchone()
        cursor.execute(
            """SELECT last_error_code,publish_attempts FROM core.outbox_events
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND event_id=%s""",
            (
                upload.target.organization_id,
                upload.target.project_id,
                upload.target.region_code,
                str(uuid5(NAMESPACE_URL, "recording-register:" + upload.upload_id)),
            ),
        )
        delivery = cursor.fetchone()
    return {
        "upload_id": upload.upload_id,
        "raw_source_id": upload.raw_source_id,
        "recording_id": row[0] if row else None,
        "status": ("SLICED" if row[1] == "SLICED" else "AWAITING_SLICE") if row else "REGISTERING",
        "page_path": "/recordings/" + row[0] + "/slice" if row else None,
        "terminal": bool(row),
        "poll_after_seconds": 0 if row else 5,
        "error_code": delivery[0] if delivery and not row else None,
        "registration_attempts": int(delivery[1]) if delivery else 0,
    }
