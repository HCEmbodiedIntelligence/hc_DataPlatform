from __future__ import annotations

from collections.abc import Callable
from threading import RLock
from typing import Any, Protocol
from uuid import UUID

from hc_data_platform.core.errors import problem

from .asset_models import RecordingAsset, RecordingUpload
from .models import RecordingScope


class RecordingAssetRepository(Protocol):
    def create_upload(
        self, upload: RecordingUpload, assets: tuple[RecordingAsset, ...]
    ) -> RecordingUpload: ...

    def get_upload(self, scope: RecordingScope, upload_id: UUID) -> RecordingUpload | None: ...

    def get_upload_by_recording(
        self, scope: RecordingScope, recording_id: str
    ) -> RecordingUpload | None: ...

    def save_upload(self, upload: RecordingUpload) -> RecordingUpload: ...

    def get_asset(
        self, scope: RecordingScope, upload_id: UUID, asset_id: UUID
    ) -> RecordingAsset | None: ...

    def list_assets(self, scope: RecordingScope, upload_id: UUID) -> tuple[RecordingAsset, ...]: ...

    def save_asset(self, asset: RecordingAsset) -> RecordingAsset: ...


class InMemoryRecordingAssetRepository:
    def __init__(self) -> None:
        self._uploads: dict[tuple[str, str, str, UUID], RecordingUpload] = {}
        self._by_recording: dict[tuple[str, str, str, str], UUID] = {}
        self._assets: dict[tuple[str, str, str, UUID, UUID], RecordingAsset] = {}
        self._lock = RLock()

    def create_upload(
        self, upload: RecordingUpload, assets: tuple[RecordingAsset, ...]
    ) -> RecordingUpload:
        upload_key = _upload_key(upload.scope, upload.upload_id)
        recording_key = _recording_key(upload.scope, upload.command.recording_id)
        with self._lock:
            existing = self._uploads.get(upload_key)
            owner = self._by_recording.get(recording_key)
            if existing is not None:
                if existing != upload:
                    raise _identity_conflict()
                return existing
            if owner is not None and owner != upload.upload_id:
                raise _identity_conflict()
            self._uploads[upload_key] = upload
            self._by_recording[recording_key] = upload.upload_id
            for asset in assets:
                self._assets[_asset_key(asset.scope, asset.upload_id, asset.asset_id)] = asset
            return upload

    def get_upload(self, scope: RecordingScope, upload_id: UUID) -> RecordingUpload | None:
        with self._lock:
            return self._uploads.get(_upload_key(scope, upload_id))

    def get_upload_by_recording(
        self, scope: RecordingScope, recording_id: str
    ) -> RecordingUpload | None:
        with self._lock:
            upload_id = self._by_recording.get(_recording_key(scope, recording_id))
            return None if upload_id is None else self._uploads[_upload_key(scope, upload_id)]

    def save_upload(self, upload: RecordingUpload) -> RecordingUpload:
        key = _upload_key(upload.scope, upload.upload_id)
        with self._lock:
            if key not in self._uploads:
                raise _not_found()
            self._uploads[key] = upload
            return upload

    def get_asset(
        self, scope: RecordingScope, upload_id: UUID, asset_id: UUID
    ) -> RecordingAsset | None:
        with self._lock:
            return self._assets.get(_asset_key(scope, upload_id, asset_id))

    def list_assets(self, scope: RecordingScope, upload_id: UUID) -> tuple[RecordingAsset, ...]:
        with self._lock:
            values = [
                asset
                for key, asset in self._assets.items()
                if key[:4] == _upload_key(scope, upload_id)
            ]
        return tuple(sorted(values, key=lambda asset: asset.manifest.path))

    def save_asset(self, asset: RecordingAsset) -> RecordingAsset:
        key = _asset_key(asset.scope, asset.upload_id, asset.asset_id)
        with self._lock:
            if key not in self._assets:
                raise _not_found()
            self._assets[key] = asset
            return asset


class PostgresRecordingAssetRepository:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def create_upload(
        self, upload: RecordingUpload, assets: tuple[RecordingAsset, ...]
    ) -> RecordingUpload:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO ingest.recording_uploads (
                        organization_id, project_id, region_code, upload_id, recording_id,
                        manifest_sha256, status, upload_document, created_by, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                    ON CONFLICT (organization_id, project_id, region_code, upload_id)
                    DO NOTHING
                    """,
                    (
                        *_scope_values(upload.scope),
                        upload.upload_id,
                        upload.command.recording_id,
                        upload.manifest_sha256,
                        upload.status.value,
                        upload.model_dump_json(),
                        upload.created_by,
                        upload.created_at,
                        upload.updated_at,
                    ),
                )
                for asset in assets:
                    cursor.execute(
                        """
                        INSERT INTO ingest.recording_upload_assets (
                            organization_id, project_id, region_code, upload_id, asset_id,
                            asset_path, role, camera_id, media_type, expected_size,
                            expected_sha256, expected_crc64, object_key, multipart_upload_id,
                            status, asset_document
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s::jsonb
                        )
                        ON CONFLICT (organization_id, project_id, region_code, upload_id, asset_id)
                        DO NOTHING
                        """,
                        _asset_values(asset),
                    )
                existing = self._get_upload(cursor, upload.scope, upload.upload_id)
                if existing is None or existing.manifest_sha256 != upload.manifest_sha256:
                    raise _identity_conflict()
            connection.commit()
            return existing
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_upload(self, scope: RecordingScope, upload_id: UUID) -> RecordingUpload | None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                return self._get_upload(cursor, scope, upload_id)
        finally:
            connection.close()

    def get_upload_by_recording(
        self, scope: RecordingScope, recording_id: str
    ) -> RecordingUpload | None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT upload_document FROM ingest.recording_uploads
                    WHERE organization_id = %s AND project_id = %s AND region_code = %s
                      AND recording_id = %s
                    """,
                    (*_scope_values(scope), recording_id),
                )
                row = cursor.fetchone()
                return None if row is None else RecordingUpload.model_validate(row[0])
        finally:
            connection.close()

    def save_upload(self, upload: RecordingUpload) -> RecordingUpload:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE ingest.recording_uploads
                    SET status = %s, upload_document = %s::jsonb, updated_at = %s
                    WHERE organization_id = %s AND project_id = %s AND region_code = %s
                      AND upload_id = %s
                    """,
                    (
                        upload.status.value,
                        upload.model_dump_json(),
                        upload.updated_at,
                        *_scope_values(upload.scope),
                        upload.upload_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise _not_found()
            connection.commit()
            return upload
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_asset(
        self, scope: RecordingScope, upload_id: UUID, asset_id: UUID
    ) -> RecordingAsset | None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT asset_document FROM ingest.recording_upload_assets
                    WHERE organization_id = %s AND project_id = %s AND region_code = %s
                      AND upload_id = %s AND asset_id = %s
                    """,
                    (*_scope_values(scope), upload_id, asset_id),
                )
                row = cursor.fetchone()
                return None if row is None else RecordingAsset.model_validate(row[0])
        finally:
            connection.close()

    def list_assets(self, scope: RecordingScope, upload_id: UUID) -> tuple[RecordingAsset, ...]:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT asset_document FROM ingest.recording_upload_assets
                    WHERE organization_id = %s AND project_id = %s AND region_code = %s
                      AND upload_id = %s
                    ORDER BY asset_path
                    """,
                    (*_scope_values(scope), upload_id),
                )
                return tuple(RecordingAsset.model_validate(row[0]) for row in cursor.fetchall())
        finally:
            connection.close()

    def save_asset(self, asset: RecordingAsset) -> RecordingAsset:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE ingest.recording_upload_assets
                    SET status = %s, object_etag = %s, committed_at = %s,
                        failure_code = %s, asset_document = %s::jsonb
                    WHERE organization_id = %s AND project_id = %s AND region_code = %s
                      AND upload_id = %s AND asset_id = %s
                    """,
                    (
                        asset.status.value,
                        asset.object_etag,
                        asset.committed_at,
                        asset.failure_code,
                        asset.model_dump_json(),
                        *_scope_values(asset.scope),
                        asset.upload_id,
                        asset.asset_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise _not_found()
            connection.commit()
            return asset
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _get_upload(cursor: Any, scope: RecordingScope, upload_id: UUID) -> RecordingUpload | None:
        cursor.execute(
            """
            SELECT upload_document FROM ingest.recording_uploads
            WHERE organization_id = %s AND project_id = %s AND region_code = %s
              AND upload_id = %s
            """,
            (*_scope_values(scope), upload_id),
        )
        row = cursor.fetchone()
        return None if row is None else RecordingUpload.model_validate(row[0])


def _scope_values(scope: RecordingScope) -> tuple[str, str, str]:
    return scope.organization_id, scope.project_id, scope.region_code


def _upload_key(scope: RecordingScope, upload_id: UUID) -> tuple[str, str, str, UUID]:
    return (*_scope_values(scope), upload_id)


def _recording_key(scope: RecordingScope, recording_id: str) -> tuple[str, str, str, str]:
    return (*_scope_values(scope), recording_id)


def _asset_key(
    scope: RecordingScope, upload_id: UUID, asset_id: UUID
) -> tuple[str, str, str, UUID, UUID]:
    return (*_scope_values(scope), upload_id, asset_id)


def _asset_values(asset: RecordingAsset) -> tuple[object, ...]:
    manifest = asset.manifest
    return (
        *_scope_values(asset.scope),
        asset.upload_id,
        asset.asset_id,
        manifest.path,
        manifest.role.value,
        manifest.camera_id,
        manifest.media_type,
        manifest.size,
        manifest.sha256,
        manifest.crc64,
        asset.object_key,
        asset.multipart_upload_id,
        asset.status.value,
        asset.model_dump_json(),
    )


def _identity_conflict() -> Exception:
    return problem(
        status=409,
        code="RECORDING_UPLOAD_IDENTITY_CONFLICT",
        title="Recording upload identity conflict",
        detail="This recording id already identifies a different immutable upload manifest.",
    )


def _not_found() -> Exception:
    return problem(
        status=404,
        code="RECORDING_UPLOAD_NOT_FOUND",
        title="Recording upload not found",
        detail="The recording upload or asset does not exist in this scope.",
    )
