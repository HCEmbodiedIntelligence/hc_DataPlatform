"""P14 robot-model application service."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import replace as dataclass_replace
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any, cast
from urllib.parse import quote, unquote
from uuid import uuid4
from xml.etree import ElementTree

from hc_data_platform.core.context import select_request_scope
from hc_data_platform.core.errors import problem
from hc_data_platform.core.pagination import CursorCodec, PageInfo
from hc_data_platform.ingest.models import CompletedPart
from hc_data_platform.ingest.ports import MultipartPart, ObjectStoragePort
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.idempotency import idempotency_conflict, request_fingerprint
from hc_data_platform.security.scope import ScopeGuard

from .models import (
    AuthorizeRobotModelAssetPartsRequest,
    BlockedReason,
    CompleteRobotModelAssetFileRequest,
    CreateRobotModelAssetUploadRequest,
    CreateRobotModelDraftRequest,
    CreateRobotModelRequest,
    PublishRobotModelVersionRequest,
    RegistryScope,
    ReplaceRobotModelJointMappingsRequest,
    RobotAssetPartAuthorization,
    RobotAssetPartAuthorizationPage,
    RobotAssetUploadStatus,
    RobotModelAsset,
    RobotModelAssetDownloadAuthorization,
    RobotModelAssetPage,
    RobotModelAssetUploadEnvelope,
    RobotModelJointMapping,
    RobotModelJointMappingPage,
    RobotModelPage,
    RobotModelPublishCheck,
    RobotModelPublishPreflight,
    RobotModelPublishPreflightEnvelope,
    RobotModelVersionEnvelope,
)
from .repository import (
    RegistryAuditEvent,
    RegistryRepository,
    RobotModelAssetUploadFileRecord,
    RobotModelAssetUploadRecord,
    RobotModelPublishPreflightRecord,
)

_ASSET_PART_SIZE_BYTES = 16 * 1024**2
_ASSET_INITIAL_PART_LIMIT = 32
_ASSET_AUTHORIZATION_TTL = timedelta(minutes=15)
_PUBLISH_PREFLIGHT_TTL = timedelta(minutes=10)
_MAX_URDF_BYTES = 32 * 1024 * 1024
_IN_MEMORY_PREFLIGHT_SECRET = "registry-in-memory-preflight-secret"
_DATABASE_DOWNLOAD_TOKEN_KIND = "robot-model-asset-database-download/v1"


class RegistryService:
    def __init__(
        self,
        repository: RegistryRepository,
        *,
        storage: ObjectStoragePort | None = None,
        cursor_secret: str = _IN_MEMORY_PREFLIGHT_SECRET,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._preflight_codec = CursorCodec(cursor_secret)
        self._clock = clock

    def _authorize_read(
        self, *, auth: AuthContext, organization_id: str, project_id: str
    ) -> RegistryScope:
        if not organization_id or not project_id:
            raise problem(
                status=422,
                code="REGISTRY_SCOPE_REQUIRED",
                title="Registry scope is required",
                detail="An exact organization and project scope are required.",
            )
        ScopeGuard.require(auth, project_id)
        auth.require_capability("robot_model.read", project_id)
        if not self._repository.has_organization_project(
            organization_id=organization_id, project_id=project_id
        ):
            raise problem(
                status=403,
                code="ORGANIZATION_SCOPE_DENIED",
                title="Organization access denied",
                detail="The current project is not a member of the requested organization.",
            )
        return RegistryScope(organization_id=organization_id, project_id=project_id)

    def _authorize_manage(
        self, *, auth: AuthContext, organization_id: str, project_id: str
    ) -> RegistryScope:
        if not organization_id or not project_id:
            raise problem(
                status=422,
                code="REGISTRY_SCOPE_REQUIRED",
                title="Registry scope is required",
                detail="An exact organization and project scope are required.",
            )
        ScopeGuard.require(auth, project_id)
        auth.require_capability("robot_model.manage", project_id)
        if not self._repository.has_organization_project(
            organization_id=organization_id, project_id=project_id
        ):
            raise problem(
                status=403,
                code="ORGANIZATION_SCOPE_DENIED",
                title="Organization access denied",
                detail="The current project is not a member of the requested organization.",
            )
        return RegistryScope(organization_id=organization_id, project_id=project_id)

    def list_robot_models(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        query: str | None,
        request_id: str,
    ) -> RobotModelPage:
        scope = self._authorize_read(
            auth=auth, organization_id=organization_id, project_id=project_id
        )
        items = self._repository.list_robot_models(
            organization_id=organization_id, project_id=project_id, query=query
        )
        self._audit(
            auth=auth,
            scope=scope,
            action="registry.robot_model.listed",
            resource_id=organization_id,
            request_id=request_id,
            outcome="SUCCEEDED",
        )
        return RobotModelPage(
            items=items,
            page_info=PageInfo(
                has_next_page=False,
                has_previous_page=False,
                start_cursor=None,
                end_cursor=None,
            ),
            snapshot_at=self._clock(),
            scope=scope,
            request_id=request_id,
        )

    def get_robot_model_version(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        version_id: str,
        request_id: str,
    ) -> RobotModelVersionEnvelope:
        scope = self._authorize_read(
            auth=auth, organization_id=organization_id, project_id=project_id
        )
        item = self._repository.get_robot_model_version(
            organization_id=organization_id, project_id=project_id, version_id=version_id
        )
        if item is None:
            raise problem(
                status=404,
                code="ROBOT_MODEL_VERSION_NOT_FOUND",
                title="Robot model version not found",
                detail="The requested robot model version does not exist in this organization.",
            )
        self._audit(
            auth=auth,
            scope=scope,
            action="registry.robot_model_version.read",
            resource_id=version_id,
            request_id=request_id,
            outcome="SUCCEEDED",
        )
        return RobotModelVersionEnvelope(data=item, scope=scope, request_id=request_id)

    def discard_robot_model_import(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        version_id: str,
        fallback_version_id: str | None,
        request_id: str,
    ) -> None:
        """Rollback an unbound import and remove every upload-owned server file."""

        scope = self._authorize_manage(
            auth=auth, organization_id=organization_id, project_id=project_id
        )
        try:
            cleanup = self._repository.discard_robot_model_import(
                organization_id=organization_id,
                project_id=project_id,
                version_id=version_id,
                fallback_version_id=fallback_version_id,
            )
        except ValueError as exc:
            raise problem(
                status=409,
                code="ROBOT_MODEL_IMPORT_IN_USE",
                title="Robot model import cannot be discarded",
                detail=("Only an unbound draft or an unbound import publication can be discarded."),
            ) from exc

        if cleanup is not None and cleanup.files:
            storage = self._storage_or_problem()
            delete_object = getattr(storage, "delete_object", None)
            for item in cleanup.files:
                storage.abort_multipart(item.object_key, item.multipart_upload_id)
                if callable(delete_object):
                    delete_object(item.object_key)
        self._audit(
            auth=auth,
            scope=scope,
            action="registry.robot_model_import.discarded",
            resource_id=version_id,
            request_id=request_id,
            outcome="SUCCEEDED",
        )

    def create_robot_model_draft(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        source_version_id: str,
        idempotency_key: str,
        request_id: str,
        command: CreateRobotModelDraftRequest,
    ) -> RobotModelVersionEnvelope:
        scope = self._authorize_manage(
            auth=auth, organization_id=organization_id, project_id=project_id
        )
        fingerprint = request_fingerprint(command.model_dump(mode="json"))
        new_version_id = f"version-{uuid4()}"
        try:
            draft = self._repository.create_robot_model_draft(
                organization_id=organization_id,
                project_id=project_id,
                source_version_id=source_version_id,
                new_version_id=new_version_id,
                version_label=command.version_label,
                update_scope=command.update_scope,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                created_at=self._clock(),
            )
        except KeyError as error:
            raise problem(
                status=404,
                code="ROBOT_MODEL_VERSION_NOT_FOUND",
                title="Robot model version not found",
                detail="The published source version does not exist in this organization.",
            ) from error
        except ValueError as error:
            detail = str(error)
            if "idempotency" in detail:
                raise idempotency_conflict() from error
            raise problem(
                status=409,
                code=(
                    "ROBOT_MODEL_VERSION_LABEL_EXISTS"
                    if "label already exists" in detail
                    else "ROBOT_MODEL_DRAFT_SOURCE_INVALID"
                ),
                title="Robot model draft cannot be created",
                detail=(
                    "Choose a unique version label for this robot model."
                    if "label already exists" in detail
                    else "Only a published fixed version can be used as the draft source."
                ),
            ) from error
        self._audit(
            auth=auth,
            scope=scope,
            action="registry.robot_model_version.draft_created",
            resource_id=draft.id,
            request_id=request_id,
            outcome="SUCCEEDED",
        )
        return RobotModelVersionEnvelope(data=draft, scope=scope, request_id=request_id)

    def create_robot_model(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        idempotency_key: str,
        request_id: str,
        command: CreateRobotModelRequest,
    ) -> RobotModelVersionEnvelope:
        scope = self._authorize_manage(
            auth=auth, organization_id=organization_id, project_id=project_id
        )
        fingerprint = request_fingerprint(command.model_dump(mode="json"))
        model_id = f"model-{uuid4()}"
        version_id = f"version-{uuid4()}"
        try:
            draft = self._repository.create_robot_model(
                organization_id=organization_id,
                project_id=project_id,
                model_id=model_id,
                version_id=version_id,
                manufacturer=command.manufacturer,
                model_code=command.model_code,
                display_name=command.display_name,
                version_label=command.version_label,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                created_at=self._clock(),
            )
        except ValueError as error:
            detail = str(error)
            if "idempotency" in detail:
                raise idempotency_conflict() from error
            raise problem(
                status=409,
                code="ROBOT_MODEL_IDENTITY_EXISTS",
                title="Robot model already exists",
                detail="Use a unique manufacturer and model code, or select the existing model.",
            ) from error
        self._audit(
            auth=auth,
            scope=scope,
            action="registry.robot_model.created",
            resource_id=draft.robot_model_id,
            request_id=request_id,
            outcome="SUCCEEDED",
        )
        return RobotModelVersionEnvelope(data=draft, scope=scope, request_id=request_id)

    def create_asset_upload(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        version_id: str,
        request_id: str,
        idempotency_key: str,
        command: CreateRobotModelAssetUploadRequest,
    ) -> RobotModelAssetUploadEnvelope:
        scope = self._authorize_manage(
            auth=auth, organization_id=organization_id, project_id=project_id
        )
        self._require_draft_version(
            organization_id=organization_id, project_id=project_id, version_id=version_id
        )
        fingerprint = request_fingerprint(command.model_dump(mode="json"))
        existing = self._repository.find_asset_upload_by_idempotency(
            organization_id=organization_id,
            project_id=project_id,
            version_id=version_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise idempotency_conflict()
            return self._upload_envelope(existing, scope=scope, request_id=request_id)

        storage = self._storage_or_problem()
        now = self._clock()
        upload_id = str(uuid4())
        files: list[RobotModelAssetUploadFileRecord] = []
        try:
            for requested_file in command.files:
                object_key = self._asset_object_key(
                    organization_id=organization_id,
                    project_id=project_id,
                    version_id=version_id,
                    upload_id=upload_id,
                    relative_path=requested_file.relative_path,
                )
                files.append(
                    RobotModelAssetUploadFileRecord(
                        relative_path=requested_file.relative_path,
                        role=requested_file.role,
                        media_type=requested_file.media_type,
                        size_bytes=requested_file.size_bytes,
                        sha256=requested_file.sha256,
                        object_key=object_key,
                        multipart_upload_id=storage.create_multipart(object_key),
                        status=RobotAssetUploadStatus.UPLOADING,
                        completed_etag=None,
                        completed_at=None,
                    )
                )
            record = RobotModelAssetUploadRecord(
                organization_id=organization_id,
                project_id=project_id,
                upload_id=upload_id,
                version_id=version_id,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                status=RobotAssetUploadStatus.UPLOADING,
                files=tuple(files),
                created_at=now,
                updated_at=now,
                completed_at=None,
            )
            self._repository.create_asset_upload(record)
        except Exception:
            for upload_file in files:
                storage.abort_multipart(upload_file.object_key, upload_file.multipart_upload_id)
            concurrent = self._repository.find_asset_upload_by_idempotency(
                organization_id=organization_id,
                project_id=project_id,
                version_id=version_id,
                idempotency_key=idempotency_key,
            )
            if concurrent is not None and concurrent.request_fingerprint == fingerprint:
                return self._upload_envelope(concurrent, scope=scope, request_id=request_id)
            raise
        self._audit(
            auth=auth,
            scope=scope,
            action="registry.robot_model_asset_upload.created",
            resource_id=version_id,
            request_id=request_id,
            outcome="SUCCEEDED",
        )
        return self._upload_envelope(record, scope=scope, request_id=request_id)

    def feature_unavailable(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        version_id: str,
        request_id: str,
        action: str,
    ) -> None:
        """Temporary guarded transition used until the durable publish preflight lands.

        It intentionally is a state conflict rather than a product 501, and does
        not report success or mutate the version.
        """

        scope = self._authorize_manage(
            auth=auth, organization_id=organization_id, project_id=project_id
        )
        self._require_version(
            organization_id=organization_id, project_id=project_id, version_id=version_id
        )
        self._audit(
            auth=auth,
            scope=scope,
            action=action,
            resource_id=version_id,
            request_id=request_id,
            outcome="FAILED",
        )
        raise problem(
            status=409,
            code="ROBOT_MODEL_PUBLISH_PREPARATION_REQUIRED",
            title="Robot model publish preparation is required",
            detail="Complete the asset manifest and mapping validation before publishing.",
        )

    def authorize_asset_parts(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        upload_id: str,
        request_id: str,
        command: AuthorizeRobotModelAssetPartsRequest,
    ) -> RobotAssetPartAuthorizationPage:
        scope = self._authorize_manage(
            auth=auth, organization_id=organization_id, project_id=project_id
        )
        record = self._require_upload(
            organization_id=organization_id, project_id=project_id, upload_id=upload_id
        )
        item = self._require_uploading_file(record, command.relative_path)
        total = self._total_parts(item.size_bytes)
        if command.part_numbers[-1] > total:
            raise problem(
                status=422,
                code="ROBOT_MODEL_ASSET_PART_OUT_OF_RANGE",
                title="Asset part number is out of range",
                detail="Requested part numbers exceed the declared asset size.",
            )
        authorizations = self._part_authorizations(item, command.part_numbers)
        self._audit(
            auth=auth,
            scope=scope,
            action="registry.robot_model_asset_upload.parts_authorized",
            resource_id=upload_id,
            request_id=request_id,
            outcome="SUCCEEDED",
        )
        return RobotAssetPartAuthorizationPage(items=authorizations)

    def complete_asset_upload_file(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        upload_id: str,
        request_id: str,
        command: CompleteRobotModelAssetFileRequest,
    ) -> RobotModelAssetUploadEnvelope:
        scope = self._authorize_manage(
            auth=auth, organization_id=organization_id, project_id=project_id
        )
        record = self._require_upload(
            organization_id=organization_id, project_id=project_id, upload_id=upload_id
        )
        self._require_draft_version(
            organization_id=organization_id,
            project_id=project_id,
            version_id=record.version_id,
        )
        item = self._require_uploading_file(record, command.relative_path, allow_completed=True)
        storage = self._storage_or_problem()
        if item.status is RobotAssetUploadStatus.COMPLETED:
            delete_object = getattr(storage, "delete_object", None)
            if callable(delete_object):
                delete_object(item.object_key)
            return self._upload_envelope(record, scope=scope, request_id=request_id)
        metadata = storage.head(item.object_key)
        if metadata is None:
            metadata = storage.complete_multipart(
                item.object_key,
                item.multipart_upload_id,
                tuple(
                    CompletedPart(part_number=part.part_number, etag=part.etag)
                    for part in command.parts
                ),
            )
        if metadata.size != item.size_bytes:
            raise problem(
                status=409,
                code="ROBOT_MODEL_ASSET_SIZE_MISMATCH",
                title="Asset size does not match upload declaration",
                detail="The completed object size differs from the immutable upload request.",
            )
        digest = hashlib.sha256()
        content = bytearray()
        for chunk in storage.read_chunks(item.object_key):
            digest.update(chunk)
            content.extend(chunk)
        if len(content) != item.size_bytes or digest.hexdigest() != item.sha256:
            raise problem(
                status=409,
                code="ROBOT_MODEL_ASSET_HASH_MISMATCH",
                title="Asset integrity check failed",
                detail="The completed object does not match the declared SHA-256 digest.",
            )
        now = self._clock()
        updated = self._repository.complete_asset_upload_file(
            organization_id=organization_id,
            project_id=project_id,
            upload_id=upload_id,
            relative_path=command.relative_path,
            completed_etag=metadata.etag,
            completed_at=now,
            asset=RobotModelAsset(
                asset_id=str(uuid4()),
                relative_path=item.relative_path,
                role=item.role,
                media_type=item.media_type,
                size_bytes=item.size_bytes,
                sha256=item.sha256,
                created_at=now,
            ),
            content=bytes(content),
        )
        delete_object = getattr(storage, "delete_object", None)
        if callable(delete_object):
            delete_object(item.object_key)
        if updated.status is not RobotAssetUploadStatus.COMPLETED and all(
            file.status is RobotAssetUploadStatus.COMPLETED for file in updated.files
        ):
            assets = self._repository.list_robot_model_assets(
                organization_id=organization_id,
                project_id=project_id,
                version_id=updated.version_id,
            )
            updated = self._repository.finalize_asset_upload(
                organization_id=organization_id,
                project_id=project_id,
                upload_id=upload_id,
                asset_manifest_hash=self._asset_manifest_hash(assets),
                updated_at=now,
            )
        self._audit(
            auth=auth,
            scope=scope,
            action="registry.robot_model_asset_upload.file_completed",
            resource_id=upload_id,
            request_id=request_id,
            outcome="SUCCEEDED",
        )
        return self._upload_envelope(updated, scope=scope, request_id=request_id)

    def list_robot_model_assets(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        version_id: str,
        request_id: str,
    ) -> RobotModelAssetPage:
        scope = self._authorize_read(
            auth=auth, organization_id=organization_id, project_id=project_id
        )
        self._require_version(
            organization_id=organization_id, project_id=project_id, version_id=version_id
        )
        items = self._repository.list_robot_model_assets(
            organization_id=organization_id, project_id=project_id, version_id=version_id
        )
        self._audit(
            auth=auth,
            scope=scope,
            action="registry.robot_model_asset.listed",
            resource_id=version_id,
            request_id=request_id,
            outcome="SUCCEEDED",
        )
        return RobotModelAssetPage(items=items, scope=scope, request_id=request_id)

    def authorize_asset_download(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        version_id: str,
        asset_id: str,
        request_id: str,
    ) -> RobotModelAssetDownloadAuthorization:
        scope = self._authorize_read(
            auth=auth, organization_id=organization_id, project_id=project_id
        )
        assets = self._repository.list_robot_model_assets(
            organization_id=organization_id, project_id=project_id, version_id=version_id
        )
        if not any(asset.asset_id == asset_id for asset in assets):
            raise problem(
                status=404,
                code="ROBOT_MODEL_ASSET_NOT_FOUND",
                title="Robot model asset not found",
                detail="The requested asset does not exist in this model version.",
            )
        found = self._repository.get_robot_model_asset(
            organization_id=organization_id, project_id=project_id, asset_id=asset_id
        )
        if found is None:
            raise problem(
                status=404,
                code="ROBOT_MODEL_ASSET_NOT_FOUND",
                title="Robot model asset not found",
                detail="The requested asset does not exist in this model version.",
            )
        object_key, asset, content = found
        self._persisted_asset_content(
            organization_id=organization_id,
            project_id=project_id,
            object_key=object_key,
            asset=asset,
            content=content,
        )
        expires_at = self._clock() + _ASSET_AUTHORIZATION_TTL
        token = self._preflight_codec.encode(
            {
                "kind": _DATABASE_DOWNLOAD_TOKEN_KIND,
                "organization_id": organization_id,
                "project_id": project_id,
                "asset_id": asset.asset_id,
                "expires_at": int(expires_at.timestamp()),
            }
        )
        url = (
            f"/api/v1/organizations/{quote(organization_id, safe='')}"
            f"/robot-model-assets/content?token={quote(token, safe='')}"
        )
        self._audit(
            auth=auth,
            scope=scope,
            action="registry.robot_model_asset.download_authorized",
            resource_id=asset_id,
            request_id=request_id,
            outcome="SUCCEEDED",
        )
        return RobotModelAssetDownloadAuthorization(
            asset_id=asset.asset_id,
            download_url=url,
            expires_at=expires_at,
            sha256=asset.sha256,
            media_type=asset.media_type,
        )

    def receive_authorized_asset_part(
        self,
        *,
        organization_id: str,
        token: str,
        body: bytes,
    ) -> MultipartPart:
        """Accept one short-lived, token-authorized part into server storage."""

        storage = self._storage_or_problem()
        receiver = getattr(storage, "put_authorized_part", None)
        if not callable(receiver):
            raise problem(
                status=503,
                code="ROBOT_MODEL_SERVER_STORAGE_UNAVAILABLE",
                title="Robot model server storage is unavailable",
                detail="The server-local robot model storage adapter is not active.",
            )
        typed_receiver = cast(Callable[..., MultipartPart], receiver)
        return typed_receiver(organization_id=organization_id, token=token, data=body)

    def open_authorized_asset_download(
        self,
        *,
        organization_id: str,
        token: str,
    ) -> tuple[str, Iterable[bytes]]:
        """Resolve a signed PostgreSQL asset download without exposing storage internals."""

        try:
            payload = self._preflight_codec.decode(token)
        except Exception as exc:
            raise self._invalid_asset_download() from exc
        project_id = payload.get("project_id")
        asset_id = payload.get("asset_id")
        expires_at = payload.get("expires_at")
        if (
            payload.get("kind") != _DATABASE_DOWNLOAD_TOKEN_KIND
            or payload.get("organization_id") != organization_id
            or not isinstance(project_id, str)
            or not project_id
            or not isinstance(asset_id, str)
            or not asset_id
            or not isinstance(expires_at, int)
        ):
            raise self._invalid_asset_download()
        if expires_at < int(self._clock().timestamp()):
            raise problem(
                status=403,
                code="ROBOT_MODEL_ASSET_TRANSFER_EXPIRED",
                title="Robot model asset transfer expired",
                detail="Request a fresh robot-model asset transfer URL and retry.",
            )
        select_request_scope(project_id, organization_id=organization_id)
        found = self._repository.get_robot_model_asset(
            organization_id=organization_id,
            project_id=project_id,
            asset_id=asset_id,
        )
        if found is None:
            raise problem(
                status=404,
                code="ROBOT_MODEL_ASSET_NOT_FOUND",
                title="Robot model asset not found",
                detail="The authorized model asset no longer exists.",
            )
        object_key, asset, content = found
        body = self._persisted_asset_content(
            organization_id=organization_id,
            project_id=project_id,
            object_key=object_key,
            asset=asset,
            content=content,
        )
        filename = unquote(PurePosixPath(asset.relative_path).name) or "robot-model-asset"
        return filename, tuple(
            body[offset : offset + 8 * 1024 * 1024]
            for offset in range(0, len(body), 8 * 1024 * 1024)
        )

    def list_robot_model_joint_mappings(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        version_id: str,
        request_id: str,
    ) -> RobotModelJointMappingPage:
        scope = self._authorize_read(
            auth=auth, organization_id=organization_id, project_id=project_id
        )
        self._require_version(
            organization_id=organization_id, project_id=project_id, version_id=version_id
        )
        items = self._repository.list_robot_model_joint_mappings(
            organization_id=organization_id,
            project_id=project_id,
            version_id=version_id,
        )
        self._audit(
            auth=auth,
            scope=scope,
            action="registry.robot_model_joint_mappings.listed",
            resource_id=version_id,
            request_id=request_id,
            outcome="SUCCEEDED",
        )
        return RobotModelJointMappingPage(
            items=items,
            mapping_hash=self._joint_mapping_hash(items),
            scope=scope,
            request_id=request_id,
        )

    def replace_robot_model_joint_mappings(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        version_id: str,
        expected_etag: str,
        idempotency_key: str,
        request_id: str,
        command: ReplaceRobotModelJointMappingsRequest,
    ) -> RobotModelVersionEnvelope:
        scope = self._authorize_manage(
            auth=auth, organization_id=organization_id, project_id=project_id
        )
        self._require_draft_version(
            organization_id=organization_id, project_id=project_id, version_id=version_id
        )
        try:
            updated = self._repository.replace_robot_model_joint_mappings(
                organization_id=organization_id,
                project_id=project_id,
                version_id=version_id,
                expected_etag=expected_etag,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint(command.model_dump(mode="json")),
                mappings=command.mappings,
                mapping_hash=self._joint_mapping_hash(command.mappings),
                updated_at=self._clock(),
            )
        except KeyError as exc:
            raise problem(
                status=404,
                code="ROBOT_MODEL_VERSION_NOT_FOUND",
                title="Robot model version not found",
                detail="The requested robot model version does not exist in this organization.",
            ) from exc
        except ValueError as exc:
            message = str(exc)
            if "idempotency" in message:
                raise idempotency_conflict() from exc
            if "etag" in message:
                raise problem(
                    status=412,
                    code="ROBOT_MODEL_VERSION_ETAG_MISMATCH",
                    title="Robot model version changed",
                    detail="Reload the fixed version before replacing its joint mappings.",
                ) from exc
            raise problem(
                status=409,
                code="ROBOT_MODEL_VERSION_NOT_DRAFT",
                title="Robot model version is immutable",
                detail=(
                    "Joint mappings can only be changed while the robot model version is a draft."
                ),
            ) from exc
        self._audit(
            auth=auth,
            scope=scope,
            action="registry.robot_model_joint_mappings.replaced",
            resource_id=version_id,
            request_id=request_id,
            outcome="SUCCEEDED",
        )
        return RobotModelVersionEnvelope(data=updated, scope=scope, request_id=request_id)

    def preflight_robot_model_publish(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        version_id: str,
        expected_etag: str,
        idempotency_key: str,
        request_id: str,
    ) -> RobotModelPublishPreflightEnvelope:
        """Validate immutable draft inputs and issue a short-lived publish proof.

        A preflight is deliberately persisted before its opaque proof is returned:
        publishing can therefore reject stale assets/mappings atomically rather
        than trusting a browser-supplied validation result.
        """

        scope = self._authorize_manage(
            auth=auth, organization_id=organization_id, project_id=project_id
        )
        fingerprint = request_fingerprint(
            {"operation": "registry.robot_model_version.preflight_publish", "etag": expected_etag}
        )
        existing = self._repository.find_publish_preflight_by_idempotency(
            organization_id=organization_id,
            project_id=project_id,
            version_id=version_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise idempotency_conflict()
            return self._preflight_envelope(existing, scope=scope, request_id=request_id)

        version = self._require_version(
            organization_id=organization_id, project_id=project_id, version_id=version_id
        )
        if version.etag != expected_etag:
            raise problem(
                status=412,
                code="ROBOT_MODEL_VERSION_ETAG_MISMATCH",
                title="Robot model version changed",
                detail="Reload the draft before starting a publish preflight.",
            )

        checks, manifest_hash, mapping_hash = self._evaluate_publish_preflight(
            organization_id=organization_id,
            project_id=project_id,
            version_id=version_id,
            version=version,
        )
        now = self._clock()
        record_without_token = RobotModelPublishPreflightRecord(
            organization_id=organization_id,
            project_id=project_id,
            preflight_id=str(uuid4()),
            version_id=version_id,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            expected_etag=expected_etag,
            asset_manifest_hash=manifest_hash,
            mapping_hash=mapping_hash,
            allowed=all(item.passed for item in checks),
            status="ISSUED",
            token_hash="",
            checks=checks,
            expires_at=now + _PUBLISH_PREFLIGHT_TTL,
            created_at=now,
            consumed_at=None,
        )
        token = self._preflight_token(record_without_token)
        record = dataclass_replace(
            record_without_token,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
        )
        try:
            self._repository.create_publish_preflight(record)
        except Exception:
            concurrent = self._repository.find_publish_preflight_by_idempotency(
                organization_id=organization_id,
                project_id=project_id,
                version_id=version_id,
                idempotency_key=idempotency_key,
            )
            if concurrent is None or concurrent.request_fingerprint != fingerprint:
                raise
            record = concurrent
        self._audit(
            auth=auth,
            scope=scope,
            action="registry.robot_model_version.preflight_publish",
            resource_id=version_id,
            request_id=request_id,
            outcome="SUCCEEDED",
        )
        return self._preflight_envelope(record, scope=scope, request_id=request_id)

    def publish_robot_model_version(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        version_id: str,
        expected_etag: str,
        idempotency_key: str,
        request_id: str,
        command: PublishRobotModelVersionRequest,
    ) -> RobotModelVersionEnvelope:
        scope = self._authorize_manage(
            auth=auth, organization_id=organization_id, project_id=project_id
        )
        preflight_id = self._decode_preflight_token(
            token=command.preflight_token,
            organization_id=organization_id,
            project_id=project_id,
            version_id=version_id,
        )
        try:
            updated = self._repository.publish_robot_model_version(
                organization_id=organization_id,
                project_id=project_id,
                version_id=version_id,
                preflight_id=preflight_id,
                token_hash=hashlib.sha256(command.preflight_token.encode()).hexdigest(),
                idempotency_key=idempotency_key,
                expected_etag=expected_etag,
                occurred_at=self._clock(),
            )
        except KeyError as exc:
            raise problem(
                status=404,
                code="ROBOT_MODEL_PUBLISH_PREFLIGHT_NOT_FOUND",
                title="Publish preflight not found",
                detail="Start a new publish preflight for this robot model version.",
            ) from exc
        except PermissionError as exc:
            raise problem(
                status=422,
                code="ROBOT_MODEL_PUBLISH_PREFLIGHT_TOKEN_INVALID",
                title="Publish preflight token is invalid",
                detail="Start a new publish preflight for this robot model version.",
            ) from exc
        except TimeoutError as exc:
            raise problem(
                status=409,
                code="ROBOT_MODEL_PUBLISH_PREFLIGHT_EXPIRED",
                title="Publish preflight expired",
                detail="Run the publish preflight again before publishing the draft.",
            ) from exc
        except ValueError as exc:
            message = str(exc)
            if "idempotency" in message:
                raise idempotency_conflict() from exc
            if "etag" in message:
                raise problem(
                    status=412,
                    code="ROBOT_MODEL_VERSION_ETAG_MISMATCH",
                    title="Robot model version changed",
                    detail="Reload the draft and run a new publish preflight.",
                ) from exc
            raise problem(
                status=409,
                code="ROBOT_MODEL_PUBLISH_PREFLIGHT_STALE",
                title="Publish preflight is no longer valid",
                detail="The asset manifest, joint mappings, or version state changed.",
            ) from exc
        self._audit(
            auth=auth,
            scope=scope,
            action="registry.robot_model_version.published",
            resource_id=version_id,
            request_id=request_id,
            outcome="SUCCEEDED",
        )
        return RobotModelVersionEnvelope(data=updated, scope=scope, request_id=request_id)

    def _evaluate_publish_preflight(
        self,
        *,
        organization_id: str,
        project_id: str,
        version_id: str,
        version: Any,
    ) -> tuple[tuple[RobotModelPublishCheck, ...], str | None, str | None]:
        """Return deterministic checks, never a client-claimed validation result."""

        assets = self._repository.list_robot_model_assets(
            organization_id=organization_id, project_id=project_id, version_id=version_id
        )
        manifest_hash = self._asset_manifest_hash(assets) if assets else None
        mappings = self._repository.list_robot_model_joint_mappings(
            organization_id=organization_id, project_id=project_id, version_id=version_id
        )
        mapping_hash = self._joint_mapping_hash(mappings)
        checks: list[RobotModelPublishCheck] = [
            RobotModelPublishCheck(
                code="DRAFT_VERSION",
                passed=version.lifecycle == "DRAFT",
                message=(
                    "The version is a mutable draft."
                    if version.lifecycle == "DRAFT"
                    else "Only a draft version can be published."
                ),
            ),
            RobotModelPublishCheck(
                code="ASSET_MANIFEST_CURRENT",
                passed=(
                    bool(assets)
                    and version.asset_availability == "AVAILABLE"
                    and version.asset_manifest_hash == manifest_hash
                ),
                message=(
                    "The immutable asset manifest is available."
                    if bool(assets)
                    and version.asset_availability == "AVAILABLE"
                    and version.asset_manifest_hash == manifest_hash
                    else "Complete the declared asset manifest before publishing."
                ),
            ),
            RobotModelPublishCheck(
                code="MAPPING_REVISION_CURRENT",
                passed=version.validation_input_hash == mapping_hash,
                message=(
                    "The saved joint mappings match the draft revision."
                    if version.validation_input_hash == mapping_hash
                    else "Save joint mappings for the current draft revision before publishing."
                ),
            ),
        ]
        urdfs = tuple(asset for asset in assets if asset.role.value == "URDF")
        checks.append(
            RobotModelPublishCheck(
                code="SINGLE_URDF",
                passed=len(urdfs) == 1,
                message=(
                    "Exactly one URDF asset is present."
                    if len(urdfs) == 1
                    else "A robot model version must contain exactly one URDF asset."
                ),
            )
        )
        if len(urdfs) != 1:
            checks.extend(
                (
                    RobotModelPublishCheck(
                        code="URDF_WELL_FORMED",
                        passed=False,
                        message="A single valid URDF is required before joint validation.",
                    ),
                    RobotModelPublishCheck(
                        code="JOINT_MAPPINGS_COMPLETE",
                        passed=False,
                        message="Joint mappings cannot be checked until the URDF is valid.",
                    ),
                )
            )
            return tuple(checks), manifest_hash, mapping_hash

        try:
            object_key, stored_asset, content = self._repository.get_robot_model_asset(
                organization_id=organization_id,
                project_id=project_id,
                asset_id=urdfs[0].asset_id,
            ) or (None, None, None)
            if object_key is None or stored_asset is None:
                raise KeyError(urdfs[0].asset_id)
            body = self._persisted_asset_content(
                organization_id=organization_id,
                project_id=project_id,
                object_key=object_key,
                asset=stored_asset,
                content=content,
            )
            joints = self._parse_urdf_joint_names((body,))
        except (KeyError, ValueError, ElementTree.ParseError, UnicodeDecodeError):
            checks.extend(
                (
                    RobotModelPublishCheck(
                        code="URDF_WELL_FORMED",
                        passed=False,
                        message=(
                            "The published URDF must be well-formed and within validation limits."
                        ),
                    ),
                    RobotModelPublishCheck(
                        code="JOINT_MAPPINGS_COMPLETE",
                        passed=False,
                        message="Joint mappings cannot be checked until the URDF is valid.",
                    ),
                )
            )
            return tuple(checks), manifest_hash, mapping_hash

        checks.append(
            RobotModelPublishCheck(
                code="URDF_WELL_FORMED",
                passed=True,
                message="The URDF structure and joint declarations are valid.",
            )
        )
        # Runtime samples use source names and the viewer maps them onto URDF joint
        # names, so publish validation must cover the URDF with mapping targets.
        mapped_targets = {item.target_joint_name for item in mappings}
        complete = mapped_targets == joints
        checks.append(
            RobotModelPublishCheck(
                code="JOINT_MAPPINGS_COMPLETE",
                passed=complete,
                message=(
                    "Joint mappings exactly cover every actuated URDF joint."
                    if complete
                    else "Joint mappings must exactly cover every actuated URDF joint."
                ),
            )
        )
        return tuple(checks), manifest_hash, mapping_hash

    @staticmethod
    def _parse_urdf_joint_names(chunks: Any) -> set[str]:
        body = bytearray()
        for chunk in chunks:
            body.extend(chunk)
            if len(body) > _MAX_URDF_BYTES:
                raise ValueError("URDF exceeds the validation size limit")
        lower = bytes(body).lower()
        if b"<!doctype" in lower or b"<!entity" in lower:
            raise ValueError("URDF must not declare external entities")
        root = ElementTree.fromstring(bytes(body))
        if root.tag.rsplit("}", 1)[-1] != "robot":
            raise ValueError("URDF root must be robot")
        known_types = {"revolute", "continuous", "prismatic", "fixed", "floating", "planar"}
        declared_names: set[str] = set()
        actuated_names: set[str] = set()
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] != "joint":
                continue
            name = element.attrib.get("name", "").strip()
            joint_type = element.attrib.get("type", "").strip()
            if not name or name in declared_names or joint_type not in known_types:
                raise ValueError("URDF joint declarations are invalid")
            declared_names.add(name)
            if joint_type != "fixed":
                actuated_names.add(name)
        return actuated_names

    def _preflight_token(self, record: RobotModelPublishPreflightRecord) -> str:
        return self._preflight_codec.encode(
            {
                "kind": "registry.robot_model.publish_preflight.v1",
                "organization_id": record.organization_id,
                "project_id": record.project_id,
                "version_id": record.version_id,
                "preflight_id": record.preflight_id,
                "expected_etag": record.expected_etag,
                "expires_at": record.expires_at.isoformat(),
            }
        )

    def _preflight_envelope(
        self,
        record: RobotModelPublishPreflightRecord,
        *,
        scope: RegistryScope,
        request_id: str,
    ) -> RobotModelPublishPreflightEnvelope:
        expired = record.expires_at <= self._clock() or record.status != "ISSUED"
        blockers = tuple(
            BlockedReason(code=item.code, message=item.message)
            for item in record.checks
            if not item.passed
        )
        if expired:
            blockers = (
                *blockers,
                BlockedReason(
                    code="PUBLISH_PREFLIGHT_EXPIRED",
                    message="Run the publish preflight again to obtain a current validation proof.",
                ),
            )
        return RobotModelPublishPreflightEnvelope(
            data=RobotModelPublishPreflight(
                allowed=record.allowed and not expired,
                preflight_token=(
                    self._preflight_token(record) if record.allowed and not expired else None
                ),
                expires_at=record.expires_at,
                expected_etag=record.expected_etag,
                asset_manifest_hash=record.asset_manifest_hash,
                mapping_hash=record.mapping_hash,
                checks=record.checks,
                blockers=blockers,
            ),
            scope=scope,
            request_id=request_id,
        )

    def _decode_preflight_token(
        self,
        *,
        token: str,
        organization_id: str,
        project_id: str,
        version_id: str,
    ) -> str:
        try:
            payload = self._preflight_codec.decode(token)
        except Exception as exc:
            raise problem(
                status=422,
                code="ROBOT_MODEL_PUBLISH_PREFLIGHT_TOKEN_INVALID",
                title="Publish preflight token is invalid",
                detail="Start a new publish preflight for this robot model version.",
            ) from exc
        if (
            payload.get("kind") != "registry.robot_model.publish_preflight.v1"
            or payload.get("organization_id") != organization_id
            or payload.get("project_id") != project_id
            or payload.get("version_id") != version_id
            or not isinstance(payload.get("preflight_id"), str)
            or not isinstance(payload.get("expected_etag"), str)
            or not isinstance(payload.get("expires_at"), str)
        ):
            raise problem(
                status=422,
                code="ROBOT_MODEL_PUBLISH_PREFLIGHT_TOKEN_INVALID",
                title="Publish preflight token is invalid",
                detail="Start a new publish preflight for this robot model version.",
            )
        return str(payload["preflight_id"])

    def _persisted_asset_content(
        self,
        *,
        organization_id: str,
        project_id: str,
        object_key: str,
        asset: RobotModelAsset,
        content: bytes | None,
    ) -> bytes:
        body = content
        if body is None:
            storage = self._storage_or_problem()
            try:
                body = b"".join(storage.read_chunks(object_key))
            except (FileNotFoundError, KeyError) as exc:
                raise problem(
                    status=409,
                    code="ROBOT_MODEL_ASSET_CONTENT_MISSING",
                    title="Robot model asset content is missing",
                    detail=(
                        "The legacy server file is unavailable and has not been migrated into "
                        "PostgreSQL. Re-import this model version."
                    ),
                ) from exc
            if len(body) != asset.size_bytes or hashlib.sha256(body).hexdigest() != asset.sha256:
                raise problem(
                    status=409,
                    code="ROBOT_MODEL_ASSET_CONTENT_INVALID",
                    title="Robot model asset content is invalid",
                    detail="The legacy server file differs from its registered checksum.",
                )
            self._repository.store_robot_model_asset_content(
                organization_id=organization_id,
                project_id=project_id,
                asset_id=asset.asset_id,
                content=body,
            )
        if len(body) != asset.size_bytes or hashlib.sha256(body).hexdigest() != asset.sha256:
            raise problem(
                status=409,
                code="ROBOT_MODEL_ASSET_CONTENT_INVALID",
                title="Robot model asset content is invalid",
                detail="The PostgreSQL asset content differs from its registered checksum.",
            )
        return body

    @staticmethod
    def _invalid_asset_download() -> Exception:
        return problem(
            status=403,
            code="ROBOT_MODEL_ASSET_TRANSFER_INVALID",
            title="Robot model asset transfer is invalid",
            detail="Request a fresh robot-model asset transfer URL and retry.",
        )

    def _require_version(self, *, organization_id: str, project_id: str, version_id: str) -> Any:
        item = self._repository.get_robot_model_version(
            organization_id=organization_id, project_id=project_id, version_id=version_id
        )
        if item is None:
            raise problem(
                status=404,
                code="ROBOT_MODEL_VERSION_NOT_FOUND",
                title="Robot model version not found",
                detail="The requested robot model version does not exist in this organization.",
            )
        return item

    def _require_draft_version(
        self, *, organization_id: str, project_id: str, version_id: str
    ) -> Any:
        item = self._require_version(
            organization_id=organization_id, project_id=project_id, version_id=version_id
        )
        if item.lifecycle != "DRAFT":
            raise problem(
                status=409,
                code="ROBOT_MODEL_VERSION_NOT_DRAFT",
                title="Robot model version is immutable",
                detail="Assets can only be changed while the robot model version is a draft.",
            )
        return item

    def _require_upload(
        self, *, organization_id: str, project_id: str, upload_id: str
    ) -> RobotModelAssetUploadRecord:
        record = self._repository.get_asset_upload(
            organization_id=organization_id, project_id=project_id, upload_id=upload_id
        )
        if record is None:
            raise problem(
                status=404,
                code="ROBOT_MODEL_ASSET_UPLOAD_NOT_FOUND",
                title="Robot model asset upload not found",
                detail="The requested asset upload does not exist in this project.",
            )
        return record

    @staticmethod
    def _require_uploading_file(
        record: RobotModelAssetUploadRecord,
        relative_path: str,
        *,
        allow_completed: bool = False,
    ) -> RobotModelAssetUploadFileRecord:
        if record.status is not RobotAssetUploadStatus.UPLOADING and not allow_completed:
            raise problem(
                status=409,
                code="ROBOT_MODEL_ASSET_UPLOAD_NOT_ACTIVE",
                title="Robot model asset upload is not active",
                detail="Part authorizations are available only while the upload is active.",
            )
        item = next((file for file in record.files if file.relative_path == relative_path), None)
        if item is None:
            raise problem(
                status=404,
                code="ROBOT_MODEL_ASSET_UPLOAD_FILE_NOT_FOUND",
                title="Robot model asset upload file not found",
                detail="The requested file is not part of this upload session.",
            )
        return item

    def _upload_envelope(
        self,
        record: RobotModelAssetUploadRecord,
        *,
        scope: RegistryScope,
        request_id: str,
    ) -> RobotModelAssetUploadEnvelope:
        public = record.public(part_size_bytes=_ASSET_PART_SIZE_BYTES)
        files = tuple(
            item.model_copy(
                update={
                    "part_authorizations": (
                        ()
                        if item.status is not RobotAssetUploadStatus.UPLOADING
                        else self._part_authorizations(
                            file,
                            tuple(
                                range(
                                    1,
                                    min(
                                        self._total_parts(file.size_bytes),
                                        _ASSET_INITIAL_PART_LIMIT,
                                    )
                                    + 1,
                                )
                            ),
                        )
                    )
                }
            )
            for item, file in zip(public.files, record.files, strict=True)
        )
        return RobotModelAssetUploadEnvelope(
            data=public.model_copy(update={"files": files}), scope=scope, request_id=request_id
        )

    def _part_authorizations(
        self,
        item: RobotModelAssetUploadFileRecord,
        part_numbers: tuple[int, ...],
    ) -> tuple[RobotAssetPartAuthorization, ...]:
        storage = self._storage_or_problem()
        expires_at = self._clock() + _ASSET_AUTHORIZATION_TTL
        return tuple(
            RobotAssetPartAuthorization(
                part_number=number,
                url=storage.presign_part(
                    item.object_key,
                    item.multipart_upload_id,
                    number,
                    int(_ASSET_AUTHORIZATION_TTL.total_seconds()),
                ),
                expires_at=expires_at,
            )
            for number in part_numbers
        )

    @staticmethod
    def _total_parts(size_bytes: int) -> int:
        return (size_bytes + _ASSET_PART_SIZE_BYTES - 1) // _ASSET_PART_SIZE_BYTES

    @staticmethod
    def _asset_object_key(
        *,
        organization_id: str,
        project_id: str,
        version_id: str,
        upload_id: str,
        relative_path: str,
    ) -> str:
        return "/".join(
            (
                "registry-assets",
                quote(organization_id, safe=""),
                quote(project_id, safe=""),
                quote(version_id, safe=""),
                quote(upload_id, safe=""),
                quote(relative_path, safe=""),
            )
        )

    @staticmethod
    def _asset_manifest_hash(assets: tuple[RobotModelAsset, ...]) -> str:
        payload = [item.model_dump(mode="json") for item in assets]
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _joint_mapping_hash(mappings: tuple[RobotModelJointMapping, ...]) -> str:
        payload = [
            item.model_dump(mode="json")
            for item in sorted(mappings, key=lambda candidate: candidate.source_joint_name)
        ]
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def _storage_or_problem(self) -> ObjectStoragePort:
        if self._storage is None:
            raise problem(
                status=503,
                code="ROBOT_MODEL_ASSET_STORAGE_UNAVAILABLE",
                title="Robot model asset storage is unavailable",
                detail=(
                    "The asset object-storage adapter is not configured. "
                    "Retry after it is restored."
                ),
            )
        return self._storage

    def _audit(
        self,
        *,
        auth: AuthContext,
        scope: RegistryScope,
        action: str,
        resource_id: str,
        request_id: str,
        outcome: str,
    ) -> None:
        assert scope.project_id is not None
        self._repository.append_audit(
            RegistryAuditEvent(
                organization_id=scope.organization_id,
                project_id=scope.project_id,
                actor_id=auth.subject_id,
                action=action,
                resource_type="ROBOT_MODEL_VERSION",
                resource_id=resource_id,
                request_id=request_id,
                outcome=outcome,
                occurred_at=self._clock(),
            )
        )
