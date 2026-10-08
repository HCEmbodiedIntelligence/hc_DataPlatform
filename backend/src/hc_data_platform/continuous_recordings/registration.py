"""Enlisted target-domain registration of already verified immutable Raw facts.

The caller owns one database transaction including its source completion status.
This adapter never commits, creates a connection, uploads bytes, or makes Episodes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID, uuid5

from hc_data_platform.security.audit import canonical_hash

from .asset_models import RecordingAsset, RecordingUpload
from .asset_repository import _asset_values
from .models import ContinuousRecording, RecordingScope, recording_duration_ns
from .repository import _recording_values


class RecordingRegistrationConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class VerifiedRecordingFacts:
    upload: RecordingUpload
    assets: tuple[RecordingAsset, ...]

    def __post_init__(self) -> None:
        upload = self.upload
        if upload.status.value != "COMMITTED" or upload.manifest_sha256 != canonical_hash(
            upload.command.model_dump(mode="json")
        ):
            raise RecordingRegistrationConflict("recording command is not verified and committed")
        declared = {a.path: a for a in upload.command.assets}
        if len(self.assets) != len(declared) or {a.manifest.path for a in self.assets} != set(
            declared
        ):
            raise RecordingRegistrationConflict("verified recording asset set differs from command")
        for asset in self.assets:
            if (
                asset.scope != upload.scope
                or asset.upload_id != upload.upload_id
                or asset.asset_id != uuid5(upload.upload_id, asset.manifest.path)
                or asset.manifest != declared[asset.manifest.path]
                or asset.status.value != "COMMITTED"
                or asset.committed_at is None
            ):
                raise RecordingRegistrationConflict("recording asset differs from verified facts")


class RecordingRegistrationPort(Protocol):
    def find_upload(
        self, cursor: Any, scope: RecordingScope, upload_id: UUID
    ) -> RecordingUpload | None: ...
    def object_keys(
        self, cursor: Any, scope: RecordingScope, upload_id: UUID
    ) -> dict[str, str]: ...
    def register(self, cursor: Any, facts: VerifiedRecordingFacts) -> ContinuousRecording: ...


class PostgresRecordingRegistration:
    @staticmethod
    def find_upload(cursor: Any, scope: RecordingScope, upload_id: UUID) -> RecordingUpload | None:
        cursor.execute(
            "SELECT upload_document FROM ingest.recording_uploads "
            "WHERE organization_id=%s AND project_id=%s AND region_code=%s AND upload_id=%s",
            (scope.organization_id, scope.project_id, scope.region_code, upload_id),
        )
        row = cursor.fetchone()
        return RecordingUpload.model_validate(row[0]) if row else None

    @staticmethod
    def object_keys(cursor: Any, scope: RecordingScope, upload_id: UUID) -> dict[str, str]:
        cursor.execute(
            "SELECT asset_path,object_key FROM ingest.recording_upload_assets "
            "WHERE organization_id=%s AND project_id=%s AND region_code=%s AND upload_id=%s",
            (scope.organization_id, scope.project_id, scope.region_code, upload_id),
        )
        return dict(cursor.fetchall())

    @staticmethod
    def state_for_upload(
        cursor: Any, scope: RecordingScope, upload_id: UUID
    ) -> tuple[str, str] | None:
        cursor.execute(
            "SELECT recording_id,status FROM ingest.continuous_recordings "
            "WHERE organization_id=%s AND project_id=%s AND region_code=%s "
            "AND recording_upload_id=%s",
            (scope.organization_id, scope.project_id, scope.region_code, upload_id),
        )
        row = cursor.fetchone()
        return (str(row[0]), str(row[1])) if row else None

    def register(self, cursor: Any, facts: VerifiedRecordingFacts) -> ContinuousRecording:
        # Validate again even if a caller bypassed construction via object mutation.
        facts = VerifiedRecordingFacts(facts.upload, facts.assets)
        recorded, projected = facts.upload, facts.assets
        target, command, uid, now = (
            recorded.scope,
            recorded.command,
            recorded.upload_id,
            recorded.updated_at,
        )
        scope = (target.organization_id, target.project_id, target.region_code)
        # Distinct Raw upload rows may race for the same logical recording ID.
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            ("recording-registration/" + "/".join((*scope, command.recording_id)),),
        )
        cursor.execute(
            """SELECT recording_upload_id FROM ingest.continuous_recordings
            WHERE organization_id=%s
            AND project_id=%s
            AND region_code=%s
            AND recording_id=%s""",
            (*scope, command.recording_id),
        )
        prior = cursor.fetchone()
        if prior and str(prior[0]) != str(uid):
            raise RecordingRegistrationConflict("recording owned by another upload")
        cursor.execute(
            """SELECT manifest_sha256 FROM ingest.recording_uploads
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND upload_id=%s""",
            (*scope, uid),
        )
        existing = cursor.fetchone()
        if existing and existing[0] != recorded.manifest_sha256:
            raise RecordingRegistrationConflict("immutable recording identity conflict")
        cursor.execute(
            """INSERT INTO ingest.recording_uploads
            (organization_id,project_id,region_code,upload_id,recording_id,manifest_sha256,status,
             upload_document,created_by,created_at,updated_at)
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
        for asset in projected:
            cursor.execute(
                "SELECT asset_document FROM ingest.recording_upload_assets "
                "WHERE organization_id=%s AND project_id=%s AND region_code=%s "
                "AND upload_id=%s AND asset_id=%s",
                (*scope, uid, asset.asset_id),
            )
            prior_asset = cursor.fetchone()
            if prior_asset and RecordingAsset.model_validate(prior_asset[0]) != asset:
                raise RecordingRegistrationConflict("immutable recording asset identity conflict")
            cursor.execute(
                """INSERT INTO ingest.recording_upload_assets
                (organization_id,project_id,region_code,upload_id,asset_id,asset_path,role,
                 camera_id,media_type,
                 expected_size,expected_sha256,expected_crc64,object_key,multipart_upload_id,status,
                 object_etag,committed_at,asset_document)
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
            duration_ns=recording_duration_ns(command.capture_started_at, command.capture_ended_at),
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
            """INSERT INTO ingest.continuous_recordings
            (organization_id,project_id,region_code,recording_id,upload_session_id,recording_upload_id,
             rollout_id,data_package_id,collection_task_id,collection_job_id,robot_id,device_id,
             capture_started_at,capture_ended_at,duration_ns,source_sha256,manifest_fingerprint,
             video_asset_count,
             status,current_revision,finalized_revision,resource_version,recording_document,created_at,updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
            %s,%s,%s::jsonb,%s,%s)
            ON CONFLICT (organization_id,project_id,region_code,recording_id) DO NOTHING""",
            _recording_values(recording),
        )
        return recording
