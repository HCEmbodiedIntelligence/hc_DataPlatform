from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Callable, Sequence
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5

from hc_data_platform.core.context import select_organization_scope, select_request_scope
from hc_data_platform.core.errors import ProblemException, problem
from hc_data_platform.ingest.models import CompletedPart as StorageCompletedPart
from hc_data_platform.ingest.ports import (
    InMemoryObjectStorage,
    ObjectStoragePort,
    crc64_ecma,
    normalize_etag,
)
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.scope import ScopeGuard

from .adapters import AdapterVerification, RobotFormatAdapterRegistry
from .models import (
    AssetState,
    AttemptOutcome,
    AuthorizePartsCommand,
    CameraVerification,
    CompleteAssetCommand,
    CreateRobotIngestIdentity,
    CredentialState,
    IdentityState,
    IssueCredentialCommand,
    IssuedRobotCredential,
    PartAuthorizationGrant,
    QualityStatus,
    RobotCredentialSummary,
    RobotIdentityEnvelope,
    RobotIdentityList,
    RobotIngestAsset,
    RobotIngestAttempt,
    RobotIngestAttemptList,
    RobotIngestAuthContext,
    RobotIngestEpisodeResult,
    RobotIngestEpisodeResultList,
    RobotIngestIdentity,
    RobotIngestProcessingStatus,
    RobotIngestStatistics,
    RobotIngestUpload,
    RobotIngestUploadEnvelope,
    RobotIngestUploadManifest,
    RobotPartAuthorization,
    UpdateRobotIngestIdentity,
    UploadedPart,
    UploadList,
    UploadState,
    UploadTarget,
)
from .repository import (
    InMemoryRobotIngestRepository,
    RobotIngestConflict,
    RobotIngestRepository,
)

Clock = Callable[[], datetime]
_TOKEN_PATTERN = re.compile(r"^hcri_([0-9a-f]{32})\.([A-Za-z0-9_-]{32,256})$")
_IDENTITY_NAMESPACE = uuid5(NAMESPACE_URL, "hc-data-platform/robot-ingest/identity")
_UPLOAD_NAMESPACE = uuid5(NAMESPACE_URL, "hc-data-platform/robot-ingest/upload")
_JOB_NAMESPACE = uuid5(NAMESPACE_URL, "hc-data-platform/robot-ingest/collection-job")
_RAW_NAMESPACE = uuid5(NAMESPACE_URL, "hc-data-platform/robot-ingest/raw-source")


class RobotIngestService:
    def __init__(
        self,
        repository: RobotIngestRepository,
        storage: ObjectStoragePort,
        *,
        credential_hmac_key: str,
        adapters: RobotFormatAdapterRegistry | None = None,
        clock: Clock = lambda: datetime.now(timezone.utc),
    ) -> None:
        if len(credential_hmac_key) < 16:
            raise ValueError("robot credential HMAC key must contain at least 16 characters")
        self.repository = repository
        self.storage = storage
        self.adapters = adapters or RobotFormatAdapterRegistry()
        self._credential_key = credential_hmac_key.encode("utf-8")
        self._clock = clock

    @classmethod
    def in_memory(
        cls,
        *,
        repository: InMemoryRobotIngestRepository | None = None,
        storage: InMemoryObjectStorage | None = None,
        credential_hmac_key: str = "robot-ingest-local-hmac-key",
        clock: Clock = lambda: datetime.now(timezone.utc),
    ) -> RobotIngestService:
        return cls(
            repository or InMemoryRobotIngestRepository(),
            storage or InMemoryObjectStorage(),
            credential_hmac_key=credential_hmac_key,
            clock=clock,
        )

    def create_identity(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        command: CreateRobotIngestIdentity,
    ) -> RobotIdentityEnvelope:
        self._authorize_admin(auth, organization_id, project_id)
        now = self._clock()
        identity_seed = "\0".join((organization_id, command.robot_id))
        identity_id = f"rii-{uuid5(_IDENTITY_NAMESPACE, identity_seed).hex}"
        existing = self.repository.get_identity(organization_id, identity_id)
        if existing is not None:
            return RobotIdentityEnvelope(data=existing)
        identity = RobotIngestIdentity(
            ingest_identity_id=identity_id,
            organization_id=organization_id,
            robot_id=command.robot_id,
            display_name=command.display_name,
            allowed_transports=tuple(sorted(set(command.allowed_transports))),
            allowed_formats=tuple(sorted(set(command.allowed_formats))),
            upload_policy=command.upload_policy,
            created_at=now,
            updated_at=now,
        )
        try:
            saved = self.repository.create_identity(identity)
        except RobotIngestConflict as exc:
            raise problem(
                status=409,
                code="ROBOT_INGEST_IDENTITY_CONFLICT",
                title="Robot upload identity already exists",
                detail="This robot already has a platform upload identity.",
            ) from exc
        return RobotIdentityEnvelope(data=saved)

    def list_identities(
        self, *, auth: AuthContext, organization_id: str, project_id: str
    ) -> RobotIdentityList:
        self._authorize_admin(auth, organization_id, project_id, manage=False)
        return RobotIdentityList(items=self.repository.list_identities(organization_id))

    def get_identity(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        ingest_identity_id: str,
    ) -> RobotIdentityEnvelope:
        self._authorize_admin(auth, organization_id, project_id, manage=False)
        return RobotIdentityEnvelope(
            data=self._require_identity(organization_id, ingest_identity_id)
        )

    def update_identity(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        ingest_identity_id: str,
        command: UpdateRobotIngestIdentity,
    ) -> RobotIdentityEnvelope:
        self._authorize_admin(auth, organization_id, project_id)
        if not command.allowed_formats or not command.allowed_transports:
            raise problem(
                status=422,
                code="ROBOT_INGEST_PERMISSIONS_EMPTY",
                title="Robot upload permissions are empty",
                detail="Keep at least one approved format and transport.",
            )
        current = self._require_identity(organization_id, ingest_identity_id)
        updated = current.model_copy(
            update={
                "allowed_transports": tuple(sorted(set(command.allowed_transports))),
                "allowed_formats": tuple(sorted(set(command.allowed_formats))),
                "upload_policy": command.upload_policy,
                "updated_at": self._clock(),
            }
        )
        return RobotIdentityEnvelope(data=self.repository.save_identity(updated))

    def set_identity_state(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        ingest_identity_id: str,
        state: IdentityState,
    ) -> RobotIdentityEnvelope:
        self._authorize_admin(auth, organization_id, project_id)
        current = self._require_identity(organization_id, ingest_identity_id)
        if current.state is state:
            return RobotIdentityEnvelope(data=current)
        updated = current.model_copy(update={"state": state, "updated_at": self._clock()})
        return RobotIdentityEnvelope(data=self.repository.save_identity(updated))

    def issue_credential(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        ingest_identity_id: str,
        command: IssueCredentialCommand,
    ) -> RobotIdentityEnvelope:
        self._authorize_admin(auth, organization_id, project_id)
        current = self._require_identity(organization_id, ingest_identity_id)
        now = self._clock()
        if command.expires_at is not None and command.expires_at <= now:
            raise problem(
                status=422,
                code="ROBOT_CREDENTIAL_EXPIRY_INVALID",
                title="Credential expiry is invalid",
                detail="Choose an expiry time in the future.",
            )
        credential_uuid = uuid4().hex
        credential_id = f"ric-{credential_uuid}"
        secret = secrets.token_urlsafe(48)
        token = f"hcri_{credential_uuid}.{secret}"
        token_prefix = f"hcri_{credential_uuid}.{secret[:8]}"
        revision = current.credential_revision + 1
        summary = RobotCredentialSummary(
            credential_id=credential_id,
            credential_version=revision,
            state=CredentialState.ACTIVE,
            token_prefix=token_prefix,
            issued_at=now,
            expires_at=command.expires_at,
        )
        updated = current.model_copy(update={"credential_revision": revision, "updated_at": now})
        self.repository.issue_credential(
            identity=updated,
            summary=summary,
            token_digest=self._token_digest(token),
            revoke_previous=command.revoke_previous,
        )
        return RobotIdentityEnvelope(
            data=updated,
            credential=IssuedRobotCredential(
                credential_id=credential_id,
                credential_version=revision,
                token=token,
                token_prefix=token_prefix,
                issued_at=now,
                expires_at=command.expires_at,
            ),
        )

    def revoke_credential(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        ingest_identity_id: str,
        credential_id: str,
    ) -> RobotCredentialSummary:
        self._authorize_admin(auth, organization_id, project_id)
        self._require_identity(organization_id, ingest_identity_id)
        summary = self.repository.revoke_credential(
            organization_id=organization_id,
            ingest_identity_id=ingest_identity_id,
            credential_id=credential_id,
            now=self._clock(),
        )
        if summary is None:
            raise problem(
                status=404,
                code="ROBOT_CREDENTIAL_NOT_FOUND",
                title="Robot credential not found",
                detail="The requested credential does not exist for this identity.",
            )
        return summary

    def list_credentials(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        ingest_identity_id: str,
    ) -> tuple[RobotCredentialSummary, ...]:
        self._authorize_admin(auth, organization_id, project_id, manage=False)
        self._require_identity(organization_id, ingest_identity_id)
        now = self._clock()
        return tuple(
            summary.model_copy(update={"state": CredentialState.EXPIRED})
            if summary.state is CredentialState.ACTIVE
            and summary.expires_at is not None
            and summary.expires_at <= now
            else summary
            for summary in self.repository.list_credentials(organization_id, ingest_identity_id)
        )

    def authenticate(self, token: str) -> RobotIngestAuthContext:
        parsed = _TOKEN_PATTERN.fullmatch(token)
        if parsed is None:
            raise self._authentication_problem()
        credential_id = f"ric-{parsed.group(1)}"
        result = self.repository.authenticate(
            credential_id=credential_id,
            token_digest=self._token_digest(token),
            now=self._clock(),
        )
        if result.context is None:
            raise self._authentication_problem(result.failure_code)
        return result.context

    def create_upload(
        self, *, token: str, manifest: RobotIngestUploadManifest
    ) -> RobotIngestUploadEnvelope:
        now = self._clock()
        try:
            auth = self.authenticate(token)
        except ProblemException as exc:
            self._safe_attempt(
                RobotIngestAttempt(
                    attempt_id=f"ria-{uuid4().hex}",
                    request_robot_id=manifest.robot_id,
                    collection_task_id=manifest.collection_task_id,
                    source_format=manifest.source_format,
                    outcome=AttemptOutcome.REJECTED,
                    failure_stage="AUTHENTICATION",
                    failure_code=exc.problem.code,
                    occurred_at=now,
                )
            )
            raise
        if manifest.robot_id != auth.authenticated_robot_id:
            # The authorization decision remains identity-first. A best-effort target
            # lookup is used only to scope the audit fact so another Project in the same
            # Organization does not inherit this failure in its statistics.
            attempt_target: UploadTarget | None = None
            with suppress(Exception):
                candidate = self.repository.resolve_upload_target(manifest.collection_task_id)
                if candidate is not None and candidate.organization_id == auth.organization_id:
                    attempt_target = candidate
            self._record_rejection(
                auth,
                manifest,
                "IDENTITY",
                "ROBOT_IDENTITY_MISMATCH",
                target=attempt_target,
            )
            raise problem(
                status=403,
                code="ROBOT_IDENTITY_MISMATCH",
                title="Robot identity mismatch",
                detail="The authenticated robot does not match the upload manifest.",
            )
        target = self.repository.resolve_upload_target(manifest.collection_task_id)
        if target is None:
            self._record_rejection(auth, manifest, "TASK_RESOLUTION", "UPLOAD_TARGET_NOT_FOUND")
            raise problem(
                status=404,
                code="UPLOAD_TARGET_NOT_FOUND",
                title="Upload target not found",
                detail="The collection task cannot be used as an upload target.",
            )
        if target.organization_id != auth.organization_id:
            self._record_rejection(auth, manifest, "TASK_RESOLUTION", "UPLOAD_TARGET_NOT_FOUND")
            raise problem(
                status=404,
                code="UPLOAD_TARGET_NOT_FOUND",
                title="Upload target not found",
                detail="The collection task cannot be used as an upload target.",
            )
        select_request_scope(
            target.project_id,
            target.region_code,
            organization_id=target.organization_id,
        )
        if (
            manifest.organization_id is not None
            and manifest.organization_id != target.organization_id
        ):
            self._record_rejection(
                auth,
                manifest,
                "TASK_RESOLUTION",
                "UPLOAD_SCOPE_MISMATCH",
                target=target,
            )
            raise self._scope_mismatch_problem()
        if manifest.project_id is not None and manifest.project_id != target.project_id:
            self._record_rejection(
                auth,
                manifest,
                "TASK_RESOLUTION",
                "UPLOAD_SCOPE_MISMATCH",
                target=target,
            )
            raise self._scope_mismatch_problem()
        if target.task_status != "ACTIVE":
            self._record_rejection(
                auth,
                manifest,
                "TASK_AUTHORIZATION",
                "COLLECTION_TASK_INACTIVE",
                target=target,
            )
            raise problem(
                status=409,
                code="COLLECTION_TASK_INACTIVE",
                title="Collection task is not accepting data",
                detail="Only ACTIVE collection tasks can receive a new robot upload.",
            )
        allowed_formats = {value.upper() for value in auth.allowed_formats}
        if "HTTPS" not in {value.upper() for value in auth.allowed_transports}:
            self._record_rejection(
                auth,
                manifest,
                "TRANSPORT_AUTHORIZATION",
                "SOURCE_TRANSPORT_NOT_ALLOWED",
                target=target,
            )
            raise problem(
                status=403,
                code="SOURCE_TRANSPORT_NOT_ALLOWED",
                title="Upload transport is not allowed",
                detail="This robot identity is not approved for HTTPS Raw uploads.",
            )
        if manifest.source_format.upper() not in allowed_formats:
            self._record_rejection(
                auth,
                manifest,
                "FORMAT_AUTHORIZATION",
                "SOURCE_FORMAT_NOT_ALLOWED",
                target=target,
            )
            raise problem(
                status=403,
                code="SOURCE_FORMAT_NOT_ALLOWED",
                title="Source format is not allowed",
                detail="This robot identity is not approved for the requested source format.",
            )
        try:
            self._validate_policy(auth, manifest)
        except ProblemException as exc:
            self._record_rejection(
                auth,
                manifest,
                "UPLOAD_POLICY",
                exc.problem.code,
                target=target,
            )
            raise
        try:
            adapter = self.adapters.resolve(manifest)
            adapter.preflight_manifest(manifest)
        except ProblemException as exc:
            self._record_rejection(
                auth,
                manifest,
                "FORMAT_PREFLIGHT",
                exc.problem.code,
                target=target,
            )
            raise
        except Exception as exc:
            failure_code = "ROBOT_INGEST_ADAPTER_PREFLIGHT_FAILED"
            self._record_rejection(
                auth,
                manifest,
                "FORMAT_PREFLIGHT",
                failure_code,
                target=target,
            )
            raise problem(
                status=500,
                code=failure_code,
                title="Source format preflight failed",
                detail="The approved source format Adapter could not preflight this upload.",
            ) from exc
        fingerprint = _manifest_fingerprint(manifest)
        existing = self.repository.find_upload(
            organization_id=auth.organization_id,
            ingest_identity_id=auth.ingest_identity_id,
            collection_task_id=target.collection_task_id,
            client_upload_id=manifest.client_upload_id,
        )
        if existing is not None:
            if existing.manifest_fingerprint != fingerprint:
                self._record_rejection(
                    auth,
                    manifest,
                    "IDEMPOTENCY",
                    "CLIENT_UPLOAD_ID_CONFLICT",
                    target=target,
                )
                raise problem(
                    status=409,
                    code="CLIENT_UPLOAD_ID_CONFLICT",
                    title="Client upload identity conflict",
                    detail="This client_upload_id is already bound to different immutable content.",
                )
            self._reject_expired_upload(auth, existing)
            return RobotIngestUploadEnvelope(data=existing, resumed=True)
        job_seed = "\0".join(
            (
                target.organization_id,
                target.project_id,
                target.collection_task_id,
                auth.authenticated_robot_id,
            )
        )
        generated_job_id = f"robot-job-{uuid5(_JOB_NAMESPACE, job_seed).hex}"
        try:
            collection_job_id = self.repository.resolve_collection_job(
                target=target,
                robot_id=auth.authenticated_robot_id,
                collection_job_id=manifest.collection_job_id,
                generated_collection_job_id=generated_job_id,
                now=now,
            )
        except RobotIngestConflict as exc:
            self._record_rejection(
                auth,
                manifest,
                "COLLECTION_JOB",
                "COLLECTION_JOB_MISMATCH",
                target=target,
            )
            raise problem(
                status=409,
                code="COLLECTION_JOB_MISMATCH",
                title="Collection job does not match",
                detail="The collection job is not assigned to this task and robot.",
            ) from exc
        upload_seed = "\0".join(
            (
                auth.ingest_identity_id,
                target.collection_task_id,
                manifest.client_upload_id,
            )
        )
        upload_id = f"riu-{uuid5(_UPLOAD_NAMESPACE, upload_seed).hex}"
        storage_prefix = _storage_prefix(
            organization_id=target.organization_id,
            project_id=target.project_id,
            collection_task_id=target.collection_task_id,
            robot_id=auth.authenticated_robot_id,
            upload_id=upload_id,
        )
        created_multiparts: list[tuple[str, str]] = []
        assets: list[RobotIngestAsset] = []
        try:
            for declared in manifest.assets:
                object_key = f"{storage_prefix}/{declared.path}"
                multipart_upload_id = self.storage.create_multipart(object_key)
                created_multiparts.append((object_key, multipart_upload_id))
                assets.append(
                    RobotIngestAsset(
                        asset_id=declared.asset_id,
                        path=declared.path,
                        role=declared.role,
                        media_type=declared.media_type,
                        object_key=object_key,
                        multipart_upload_id=multipart_upload_id,
                        expected_size_bytes=declared.size_bytes,
                        expected_sha256=declared.sha256,
                        expected_crc64=declared.crc64,
                    )
                )
            upload = RobotIngestUpload(
                upload_id=upload_id,
                client_upload_id=manifest.client_upload_id,
                ingest_identity_id=auth.ingest_identity_id,
                authenticated_robot_id=auth.authenticated_robot_id,
                request_robot_id=manifest.robot_id,
                credential_id=auth.credential_id,
                credential_version=auth.credential_version,
                target=target,
                collection_job_id=collection_job_id,
                capture_mode=manifest.capture_mode,
                source_format=manifest.source_format,
                source_format_version=manifest.source_format_version,
                capture_started_at=manifest.capture_started_at,
                capture_ended_at=manifest.capture_ended_at,
                declared_episode_count=manifest.declared_episode_count,
                total_bytes=sum(item.size_bytes for item in manifest.assets),
                state=UploadState.UPLOADING,
                manifest_fingerprint=fingerprint,
                manifest=manifest,
                assets=tuple(assets),
                cameras=manifest.cameras,
                created_at=now,
                expires_at=now + timedelta(hours=auth.upload_policy.session_retention_hours),
                updated_at=now,
            )
            saved = self.repository.create_upload(upload)
        except RobotIngestConflict:
            for object_key, multipart_upload_id in created_multiparts:
                with suppress(Exception):
                    self.storage.abort_multipart(object_key, multipart_upload_id)
            existing = self.repository.find_upload(
                organization_id=auth.organization_id,
                ingest_identity_id=auth.ingest_identity_id,
                collection_task_id=target.collection_task_id,
                client_upload_id=manifest.client_upload_id,
            )
            if existing is not None and existing.manifest_fingerprint == fingerprint:
                self._reject_expired_upload(auth, existing)
                return RobotIngestUploadEnvelope(data=existing, resumed=True)
            raise problem(
                status=409,
                code="CLIENT_UPLOAD_ID_CONFLICT",
                title="Client upload identity conflict",
                detail="This client_upload_id is already bound to different immutable content.",
            ) from None
        except Exception:
            for object_key, multipart_upload_id in created_multiparts:
                with suppress(Exception):
                    self.storage.abort_multipart(object_key, multipart_upload_id)
            raise
        self._safe_attempt(
            RobotIngestAttempt(
                attempt_id=f"ria-{uuid4().hex}",
                organization_id=target.organization_id,
                project_id=target.project_id,
                region_code=target.region_code,
                authenticated_robot_id=auth.authenticated_robot_id,
                request_robot_id=manifest.robot_id,
                collection_task_id=target.collection_task_id,
                source_format=manifest.source_format,
                outcome=AttemptOutcome.ACCEPTED,
                failure_stage="CREATE_UPLOAD",
                upload_id=saved.upload_id,
                occurred_at=now,
            )
        )
        return RobotIngestUploadEnvelope(data=saved)

    def get_upload(self, *, token: str, upload_id: str) -> RobotIngestUploadEnvelope:
        auth = self.authenticate(token)
        upload = self._owned_upload(auth, upload_id)
        return RobotIngestUploadEnvelope(data=upload)

    def authorize_parts(
        self,
        *,
        token: str,
        upload_id: str,
        asset_id: str,
        command: AuthorizePartsCommand,
    ) -> PartAuthorizationGrant:
        auth = self.authenticate(token)
        upload = self._owned_upload(auth, upload_id)
        if upload.state is not UploadState.UPLOADING:
            raise self._upload_state_problem(upload)
        asset = self._require_asset(upload, asset_id)
        if asset.state is not AssetState.UPLOADING:
            raise problem(
                status=409,
                code="ROBOT_INGEST_ASSET_ALREADY_COMPLETED",
                title="Upload asset is already complete",
                detail="Completed immutable assets do not need new part authorizations.",
            )
        uploaded = self.storage.list_parts(asset.object_key, asset.multipart_upload_id)
        uploaded_numbers = {part.part_number for part in uploaded}
        expires_at = self._clock().timestamp() + auth.upload_policy.part_authorization_ttl_seconds
        expiration = datetime.fromtimestamp(expires_at, tz=timezone.utc)
        grants = tuple(
            RobotPartAuthorization(
                part_number=number,
                url=self.storage.presign_part(
                    asset.object_key,
                    asset.multipart_upload_id,
                    number,
                    auth.upload_policy.part_authorization_ttl_seconds,
                ),
                expires_at=expiration,
            )
            for number in command.part_numbers
            if number not in uploaded_numbers
        )
        return PartAuthorizationGrant(
            upload_id=upload.upload_id,
            asset_id=asset.asset_id,
            uploaded_parts=tuple(
                UploadedPart(
                    part_number=part.part_number,
                    etag=normalize_etag(part.etag),
                    size_bytes=part.size,
                    crc64=part.crc64,
                )
                for part in uploaded
            ),
            authorizations=grants,
        )

    def complete_asset(
        self,
        *,
        token: str,
        upload_id: str,
        asset_id: str,
        command: CompleteAssetCommand,
    ) -> RobotIngestUploadEnvelope:
        auth = self.authenticate(token)
        upload = self._owned_upload(auth, upload_id)
        asset = self._require_asset(upload, asset_id)
        if asset.state is AssetState.COMPLETED:
            return RobotIngestUploadEnvelope(data=upload, resumed=True)
        if upload.state not in {UploadState.UPLOADING, UploadState.PAUSED}:
            raise self._upload_state_problem(upload)
        metadata = self.storage.head(asset.object_key)
        if metadata is None:
            metadata = self.storage.complete_multipart(
                asset.object_key,
                asset.multipart_upload_id,
                tuple(
                    StorageCompletedPart(part_number=part.part_number, etag=part.etag)
                    for part in command.parts
                ),
            )
        actual_sha256, computed_crc64 = self._object_checksums(asset.object_key)
        actual_crc64 = metadata.crc64 if metadata.crc64 is not None else computed_crc64
        failure_code = None
        if metadata.size != asset.expected_size_bytes:
            failure_code = "ASSET_SIZE_MISMATCH"
        elif auth.upload_policy.require_sha256 and actual_sha256 != asset.expected_sha256:
            failure_code = "ASSET_SHA256_MISMATCH"
        elif auth.upload_policy.require_crc64 and actual_crc64 != asset.expected_crc64:
            failure_code = "ASSET_CRC64_MISMATCH"
        now = self._clock()
        if failure_code is not None:
            failed_asset = asset.model_copy(
                update={
                    "state": AssetState.FAILED,
                    "actual_size_bytes": metadata.size,
                    "actual_sha256": actual_sha256,
                    "actual_crc64": actual_crc64,
                    "etag": metadata.etag,
                }
            )
            failed = upload.model_copy(
                update={
                    "assets": _replace_asset(upload.assets, failed_asset),
                    "state": UploadState.FAILED,
                    "updated_at": now,
                }
            )
            self.repository.save_upload(failed)
            self._safe_attempt(
                RobotIngestAttempt(
                    attempt_id=f"ria-{uuid4().hex}",
                    organization_id=upload.target.organization_id,
                    project_id=upload.target.project_id,
                    region_code=upload.target.region_code,
                    authenticated_robot_id=auth.authenticated_robot_id,
                    request_robot_id=upload.request_robot_id,
                    collection_task_id=upload.target.collection_task_id,
                    source_format=upload.source_format,
                    outcome=AttemptOutcome.FAILED,
                    failure_stage="INTEGRITY_VERIFICATION",
                    failure_code=failure_code,
                    upload_id=upload.upload_id,
                    occurred_at=now,
                )
            )
            raise problem(
                status=422,
                code=failure_code,
                title="Raw asset integrity verification failed",
                detail="The uploaded object does not match its immutable manifest.",
            )
        completed_asset = asset.model_copy(
            update={
                "state": AssetState.COMPLETED,
                "actual_size_bytes": metadata.size,
                "actual_sha256": actual_sha256,
                "actual_crc64": actual_crc64,
                "etag": metadata.etag,
                "completed_at": now,
            }
        )
        assets = _replace_asset(upload.assets, completed_asset)
        updated = upload.model_copy(
            update={
                "assets": assets,
                "state": (
                    UploadState.READY_TO_COMMIT
                    if all(item.state is AssetState.COMPLETED for item in assets)
                    else UploadState.UPLOADING
                ),
                "updated_at": now,
            }
        )
        return RobotIngestUploadEnvelope(data=self.repository.save_upload(updated))

    def commit_upload(self, *, token: str, upload_id: str) -> RobotIngestUploadEnvelope:
        auth = self.authenticate(token)
        upload = self._owned_upload(auth, upload_id)
        if upload.state is UploadState.COMMITTED:
            return RobotIngestUploadEnvelope(data=upload, resumed=True)
        if upload.state is not UploadState.READY_TO_COMMIT:
            raise self._upload_state_problem(upload)
        try:
            adapter = self.adapters.resolve(upload.manifest)
            verification = AdapterVerification.model_validate(
                adapter.verify_assets(upload.manifest, upload.assets)
            )
            quality_status = QualityStatus(verification.quality_status)
        except ProblemException as exc:
            self._record_rejection(
                auth,
                upload.manifest,
                "ADAPTER_VERIFICATION",
                exc.problem.code,
                target=upload.target,
                upload_id=upload.upload_id,
            )
            raise
        except Exception as exc:
            failure_code = "ROBOT_INGEST_ADAPTER_VERIFICATION_FAILED"
            self._record_rejection(
                auth,
                upload.manifest,
                "ADAPTER_VERIFICATION",
                failure_code,
                target=upload.target,
                upload_id=upload.upload_id,
            )
            raise problem(
                status=500,
                code=failure_code,
                title="Source format verification failed",
                detail="The approved source format Adapter could not verify this upload.",
            ) from exc
        verified = upload.model_copy(
            update={
                "verified_episode_count": verification.verified_episode_count,
                "verified_sample_count": verification.verified_sample_count,
                "camera_verification": verification.camera_verification,
                "quality_status": quality_status,
                "processing_status": verification.processing_status,
                "updated_at": self._clock(),
            }
        )
        storage_prefix = _storage_prefix(
            organization_id=upload.target.organization_id,
            project_id=upload.target.project_id,
            collection_task_id=upload.target.collection_task_id,
            robot_id=upload.authenticated_robot_id,
            upload_id=upload.upload_id,
        )
        manifest_key = f"{storage_prefix}/robot-ingest-manifest.json"
        try:
            normalized_raw = adapter.normalize_raw_source(upload.manifest)
        except ProblemException as exc:
            self._record_rejection(
                auth,
                upload.manifest,
                "RAW_NORMALIZATION",
                exc.problem.code,
                target=upload.target,
                upload_id=upload.upload_id,
            )
            raise
        except Exception as exc:
            failure_code = "ROBOT_INGEST_ADAPTER_NORMALIZATION_FAILED"
            self._record_rejection(
                auth,
                upload.manifest,
                "RAW_NORMALIZATION",
                failure_code,
                target=upload.target,
                upload_id=upload.upload_id,
            )
            raise problem(
                status=500,
                code=failure_code,
                title="Raw source normalization failed",
                detail="The approved source format Adapter could not normalize this upload.",
            ) from exc
        if self.storage.head(manifest_key) is None:
            try:
                self.storage.put_json(
                    manifest_key,
                    {
                        "schema_version": "robot-ingest-committed/v1",
                        "upload": verified.model_dump(mode="json"),
                        "adapter": normalized_raw,
                    },
                    if_none_match=True,
                )
            except ProblemException as exc:
                if exc.problem.code != "OBJECT_ALREADY_EXISTS":
                    raise
                # Another commit won the conditional write. Validate its immutable
                # lineage below; per-request verification timestamps may differ.
        stored_manifest = json.loads(b"".join(self.storage.read_chunks(manifest_key)))
        stored_upload = RobotIngestUpload.model_validate(stored_manifest.get("upload"))
        lineage_fields = (
            "upload_id",
            "ingest_identity_id",
            "authenticated_robot_id",
            "target",
            "manifest_fingerprint",
            "manifest",
            "assets",
        )
        if (
            stored_manifest.get("schema_version") != "robot-ingest-committed/v1"
            or stored_manifest.get("adapter") != normalized_raw
            or any(
                getattr(stored_upload, field) != getattr(verified, field)
                for field in lineage_fields
            )
        ):
            raise problem(
                status=409,
                code="ROBOT_COMMIT_MANIFEST_CONFLICT",
                title="Committed manifest conflicts with upload",
                detail="The stored commit manifest does not match immutable upload lineage.",
            )
        content_hash = hashlib.sha256(
            "\n".join(
                f"{asset.asset_id}\0{asset.expected_sha256}\0{asset.expected_size_bytes}"
                for asset in verified.assets
            ).encode("utf-8")
        ).hexdigest()
        raw_source_id = f"raw-{uuid5(_RAW_NAMESPACE, upload.upload_id).hex}"
        now = self._clock()
        committed = self.repository.commit_raw_source(
            upload=verified,
            raw_source_id=raw_source_id,
            manifest_key=manifest_key,
            storage_prefix=storage_prefix,
            content_hash=content_hash,
            adapter_name=adapter.name,
            processing_status=verification.processing_status,
            now=now,
        )
        try:
            adapter.start_processing(raw_source_id)
        except Exception:
            # Raw and its durable ingest job were committed atomically above. A
            # synchronous adapter notification must not turn that successful immutable
            # commit into an ambiguous client failure; workers can retry the PENDING job.
            self._safe_attempt(
                RobotIngestAttempt(
                    attempt_id=f"ria-{uuid4().hex}",
                    organization_id=committed.target.organization_id,
                    project_id=committed.target.project_id,
                    region_code=committed.target.region_code,
                    authenticated_robot_id=auth.authenticated_robot_id,
                    request_robot_id=committed.request_robot_id,
                    collection_task_id=committed.target.collection_task_id,
                    source_format=committed.source_format,
                    outcome=AttemptOutcome.FAILED,
                    failure_stage="PROCESSING_START",
                    failure_code="ROBOT_INGEST_PROCESSING_START_FAILED",
                    upload_id=committed.upload_id,
                    raw_source_id=committed.raw_source_id,
                    occurred_at=now,
                )
            )
        self._safe_attempt(
            RobotIngestAttempt(
                attempt_id=f"ria-{uuid4().hex}",
                organization_id=committed.target.organization_id,
                project_id=committed.target.project_id,
                region_code=committed.target.region_code,
                authenticated_robot_id=auth.authenticated_robot_id,
                request_robot_id=committed.request_robot_id,
                collection_task_id=committed.target.collection_task_id,
                source_format=committed.source_format,
                outcome=AttemptOutcome.COMMITTED,
                failure_stage="COMMIT",
                upload_id=committed.upload_id,
                raw_source_id=committed.raw_source_id,
                occurred_at=now,
            )
        )
        return RobotIngestUploadEnvelope(data=committed)

    def pause_upload(self, *, token: str, upload_id: str) -> RobotIngestUploadEnvelope:
        auth = self.authenticate(token)
        upload = self._owned_upload(auth, upload_id)
        if upload.state is UploadState.PAUSED:
            return RobotIngestUploadEnvelope(data=upload, resumed=True)
        if upload.state is not UploadState.UPLOADING:
            raise self._upload_state_problem(upload)
        updated = upload.model_copy(
            update={"state": UploadState.PAUSED, "updated_at": self._clock()}
        )
        return RobotIngestUploadEnvelope(data=self.repository.save_upload(updated))

    def resume_upload(self, *, token: str, upload_id: str) -> RobotIngestUploadEnvelope:
        auth = self.authenticate(token)
        upload = self._owned_upload(auth, upload_id)
        if upload.state is UploadState.UPLOADING:
            return RobotIngestUploadEnvelope(data=upload, resumed=True)
        if upload.state is not UploadState.PAUSED:
            raise self._upload_state_problem(upload)
        updated = upload.model_copy(
            update={"state": UploadState.UPLOADING, "updated_at": self._clock()}
        )
        return RobotIngestUploadEnvelope(data=self.repository.save_upload(updated))

    def cancel_upload(self, *, token: str, upload_id: str) -> RobotIngestUploadEnvelope:
        auth = self.authenticate(token)
        upload = self._owned_upload(auth, upload_id)
        if upload.state is UploadState.CANCELLED:
            return RobotIngestUploadEnvelope(data=upload, resumed=True)
        if upload.state is UploadState.COMMITTED:
            raise self._upload_state_problem(upload)
        assets: list[RobotIngestAsset] = []
        for asset in upload.assets:
            if asset.state is AssetState.UPLOADING:
                self.storage.abort_multipart(asset.object_key, asset.multipart_upload_id)
                assets.append(asset.model_copy(update={"state": AssetState.CANCELLED}))
            else:
                assets.append(asset)
        updated = upload.model_copy(
            update={
                "assets": tuple(assets),
                "state": UploadState.CANCELLED,
                "updated_at": self._clock(),
            }
        )
        return RobotIngestUploadEnvelope(data=self.repository.save_upload(updated))

    def list_robot_uploads(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        robot_id: str | None = None,
        collection_task_id: str | None = None,
        source_format: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> UploadList:
        self._authorize_project_admin(auth, organization_id, project_id, region_code, manage=False)
        _validate_time_filter(created_from, created_to)
        return UploadList(
            items=self.repository.list_uploads(
                organization_id=organization_id,
                project_id=project_id,
                region_code=region_code,
                robot_id=robot_id,
                collection_task_id=collection_task_id,
                source_format=source_format,
                created_from=created_from,
                created_to=created_to,
            )
        )

    def list_attempts(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        robot_id: str | None = None,
    ) -> RobotIngestAttemptList:
        self._authorize_project_admin(auth, organization_id, project_id, region_code, manage=False)
        return RobotIngestAttemptList(
            items=self.repository.list_attempts(
                organization_id=organization_id,
                project_id=project_id,
                region_code=region_code,
                robot_id=robot_id,
            )
        )

    def list_upload_episode_results(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        upload_id: str,
    ) -> RobotIngestEpisodeResultList:
        self._authorize_project_admin(auth, organization_id, project_id, region_code, manage=False)
        upload = next(
            (
                item
                for item in self.repository.list_uploads(
                    organization_id=organization_id,
                    project_id=project_id,
                    region_code=region_code,
                )
                if item.upload_id == upload_id
            ),
            None,
        )
        if upload is None or upload.raw_source_id is None:
            raise problem(
                status=404,
                code="ROBOT_INGEST_RAW_LINEAGE_NOT_FOUND",
                title="Robot Raw lineage not found",
                detail="The requested committed robot upload is not visible in this project.",
            )
        return RobotIngestEpisodeResultList(
            upload_id=upload.upload_id,
            raw_source_id=upload.raw_source_id,
            items=self.repository.list_episode_results(
                organization_id=organization_id,
                project_id=project_id,
                region_code=region_code,
                upload_id=upload.upload_id,
            ),
        )

    def statistics(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        robot_id: str,
        collection_task_id: str | None = None,
        source_format: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> RobotIngestStatistics:
        self._authorize_project_admin(auth, organization_id, project_id, region_code, manage=False)
        _validate_time_filter(created_from, created_to)
        return self.repository.statistics(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            robot_id=robot_id,
            collection_task_id=collection_task_id,
            source_format=source_format,
            created_from=created_from,
            created_to=created_to,
        )

    def apply_processing_result(
        self,
        *,
        organization_id: str,
        upload_id: str,
        ingest_identity_id: str,
        verified_episode_count: int | None = None,
        derived_episode_count: int | None = None,
        verified_frame_count: int | None = None,
        verified_sample_count: int | None = None,
        qc_pass_episode_count: int | None = None,
        qc_risk_episode_count: int | None = None,
        qc_reject_episode_count: int | None = None,
        camera_verification: tuple[CameraVerification, ...] | None = None,
        quality_status: QualityStatus | None = None,
        processing_status: RobotIngestProcessingStatus | None = None,
        episode_results: tuple[RobotIngestEpisodeResult, ...] | None = None,
    ) -> RobotIngestUpload:
        """Adapter/worker callback; declarations and verified metrics remain separate."""

        upload = self.repository.get_upload(organization_id, upload_id, ingest_identity_id)
        if upload is None:
            raise KeyError(upload_id)
        if upload.state is not UploadState.COMMITTED or upload.raw_source_id is None:
            raise ValueError("processing results require a committed immutable Raw source")
        select_request_scope(
            upload.target.project_id,
            upload.target.region_code,
            organization_id=upload.target.organization_id,
        )
        changes: dict[str, object] = {"updated_at": self._clock()}
        if verified_episode_count is not None:
            changes["verified_episode_count"] = verified_episode_count
        if derived_episode_count is not None:
            changes["derived_episode_count"] = derived_episode_count
        if verified_frame_count is not None:
            changes["verified_frame_count"] = verified_frame_count
        if verified_sample_count is not None:
            changes["verified_sample_count"] = verified_sample_count
        if qc_pass_episode_count is not None:
            changes["qc_pass_episode_count"] = qc_pass_episode_count
        if qc_risk_episode_count is not None:
            changes["qc_risk_episode_count"] = qc_risk_episode_count
        if qc_reject_episode_count is not None:
            changes["qc_reject_episode_count"] = qc_reject_episode_count
        if camera_verification is not None:
            declared_camera_ids = {item.camera_id for item in upload.cameras}
            verified_camera_ids = [item.camera_id for item in camera_verification]
            if (
                len(verified_camera_ids) != len(set(verified_camera_ids))
                or set(verified_camera_ids) != declared_camera_ids
            ):
                raise ValueError(
                    "camera verification must contain every declared camera exactly once"
                )
            changes["camera_verification"] = camera_verification
        if quality_status is not None:
            changes["quality_status"] = quality_status
        if processing_status is not None:
            changes["processing_status"] = processing_status
        if episode_results is not None:
            episode_ids = [item.episode_id for item in episode_results]
            source_indexes = [item.source_episode_index for item in episode_results]
            if len(episode_ids) != len(set(episode_ids)) or len(source_indexes) != len(
                set(source_indexes)
            ):
                raise ValueError("Episode result identities and source indexes must be unique")
            if upload.capture_mode.value == "CONTINUOUS":
                changes["derived_episode_count"] = len(episode_results)
            else:
                changes["verified_episode_count"] = len(episode_results)
            changes["verified_frame_count"] = sum(item.frame_count or 0 for item in episode_results)
            changes["verified_sample_count"] = sum(
                item.sample_count or 0 for item in episode_results
            )
            changes["qc_pass_episode_count"] = sum(
                item.quality_status is QualityStatus.PASS for item in episode_results
            )
            changes["qc_risk_episode_count"] = sum(
                item.quality_status is QualityStatus.RISK for item in episode_results
            )
            changes["qc_reject_episode_count"] = sum(
                item.quality_status is QualityStatus.REJECT for item in episode_results
            )
            if changes["qc_reject_episode_count"]:
                changes["quality_status"] = QualityStatus.REJECT
            elif changes["qc_risk_episode_count"]:
                changes["quality_status"] = QualityStatus.RISK
            elif changes["qc_pass_episode_count"] == len(episode_results) and episode_results:
                changes["quality_status"] = QualityStatus.PASS
            if episode_results:
                result_statuses = {item.status for item in episode_results}
                if result_statuses == {"READY"}:
                    derived_processing_status = RobotIngestProcessingStatus.READY
                elif result_statuses == {"FAILED"}:
                    derived_processing_status = RobotIngestProcessingStatus.FAILED
                elif "FAILED" in result_statuses:
                    derived_processing_status = RobotIngestProcessingStatus.PARTIALLY_FAILED
                elif "PROCESSING" in result_statuses or "READY" in result_statuses:
                    derived_processing_status = RobotIngestProcessingStatus.PROCESSING
                else:
                    derived_processing_status = RobotIngestProcessingStatus.PENDING
                if processing_status is not None and processing_status != derived_processing_status:
                    raise ValueError(
                        "processing status must agree with authoritative Episode results"
                    )
                changes["processing_status"] = derived_processing_status
        resulting_verified_count = changes.get(
            "verified_episode_count", upload.verified_episode_count
        )
        if (
            upload.capture_mode.value == "PRESEGMENTED"
            and upload.declared_episode_count is not None
            and resulting_verified_count is not None
            and resulting_verified_count != upload.declared_episode_count
            and changes.get("quality_status", upload.quality_status) is not QualityStatus.REJECT
        ):
            # A robot declaration can never override the count discovered from Raw.
            # Count drift is at least a quality risk even when all discovered Episodes
            # individually passed their QC checks.
            changes["quality_status"] = QualityStatus.RISK
        processed = RobotIngestUpload.model_validate({**upload.model_dump(), **changes})
        evaluated = (
            processed.verified_episode_count
            if processed.verified_episode_count is not None
            else processed.derived_episode_count
        )
        if (
            processed.qc_pass_episode_count
            + processed.qc_risk_episode_count
            + processed.qc_reject_episode_count
            > evaluated
        ):
            raise ValueError("QC Episode counts cannot exceed verified or derived Episodes")
        if episode_results is not None:
            existing_results = self.repository.list_episode_results(
                organization_id=upload.target.organization_id,
                project_id=upload.target.project_id,
                region_code=upload.target.region_code,
                upload_id=upload.upload_id,
            )
            existing_identity = {
                item.episode_id: item.source_episode_index for item in existing_results
            }
            incoming_identity = {
                item.episode_id: item.source_episode_index for item in episode_results
            }
            if existing_identity and existing_identity != incoming_identity:
                raise ValueError("the discovered Episode identity set is immutable")
        saved = self.repository.save_upload(processed)
        if episode_results is not None:
            self.repository.save_episode_results(upload=saved, items=episode_results)
        return saved

    def _owned_upload(self, auth: RobotIngestAuthContext, upload_id: str) -> RobotIngestUpload:
        upload = self.repository.get_upload(
            auth.organization_id,
            upload_id,
            auth.ingest_identity_id,
        )
        if upload is None:
            raise problem(
                status=404,
                code="ROBOT_INGEST_UPLOAD_NOT_FOUND",
                title="Robot upload not found",
                detail="The requested upload does not exist for this robot identity.",
            )
        select_request_scope(
            upload.target.project_id,
            upload.target.region_code,
            organization_id=upload.target.organization_id,
        )
        self._reject_expired_upload(auth, upload)
        return upload

    def _reject_expired_upload(
        self, auth: RobotIngestAuthContext, upload: RobotIngestUpload
    ) -> None:
        if (
            upload.state
            not in {
                UploadState.UPLOADING,
                UploadState.PAUSED,
                UploadState.READY_TO_COMMIT,
            }
            or self._clock() < upload.expires_at
        ):
            return
        assets: list[RobotIngestAsset] = []
        for asset in upload.assets:
            if asset.state is AssetState.UPLOADING:
                with suppress(Exception):
                    self.storage.abort_multipart(asset.object_key, asset.multipart_upload_id)
                assets.append(asset.model_copy(update={"state": AssetState.FAILED}))
            else:
                assets.append(asset)
        now = self._clock()
        failed = upload.model_copy(
            update={
                "assets": tuple(assets),
                "state": UploadState.FAILED,
                "updated_at": now,
            }
        )
        self.repository.save_upload(failed)
        self._safe_attempt(
            RobotIngestAttempt(
                attempt_id=f"ria-{uuid4().hex}",
                organization_id=upload.target.organization_id,
                project_id=upload.target.project_id,
                region_code=upload.target.region_code,
                authenticated_robot_id=auth.authenticated_robot_id,
                request_robot_id=upload.request_robot_id,
                collection_task_id=upload.target.collection_task_id,
                source_format=upload.source_format,
                outcome=AttemptOutcome.FAILED,
                failure_stage="SESSION_RETENTION",
                failure_code="UPLOAD_SESSION_EXPIRED",
                upload_id=upload.upload_id,
                occurred_at=now,
            )
        )
        raise problem(
            status=410,
            code="UPLOAD_SESSION_EXPIRED",
            title="Robot upload session expired",
            detail="The resumable upload exceeded its configured retention period.",
        )

    def _authorize_admin(
        self,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        *,
        manage: bool = True,
    ) -> None:
        if auth.is_platform_admin:
            ScopeGuard.require(auth, project_id, organization_id=organization_id)
        elif not any(
            scoped_org == organization_id and scoped_project == project_id
            for scoped_org, scoped_project, _ in auth.organization_scope_triples
        ):
            raise problem(
                status=403,
                code="ORGANIZATION_SCOPE_DENIED",
                title="Organization access denied",
                detail="The selected project is outside the authenticated organization scope.",
            )
        auth.require_organization_capability(
            organization_id,
            "ingest_source.manage" if manage else "ingest_source.read",
        )
        select_organization_scope(organization_id)

    @staticmethod
    def _authorize_project_admin(
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        *,
        manage: bool,
    ) -> None:
        ScopeGuard.require(
            auth,
            project_id,
            region_code,
            organization_id=organization_id,
        )
        auth.require_capability(
            "ingest_source.manage" if manage else "ingest_source.read",
            project_id,
            organization_id,
        )
        select_request_scope(project_id, region_code, organization_id=organization_id)

    def _require_identity(
        self, organization_id: str, ingest_identity_id: str
    ) -> RobotIngestIdentity:
        identity = self.repository.get_identity(organization_id, ingest_identity_id)
        if identity is None:
            raise problem(
                status=404,
                code="ROBOT_INGEST_IDENTITY_NOT_FOUND",
                title="Robot upload identity not found",
                detail="The requested robot upload identity does not exist.",
            )
        return identity

    @staticmethod
    def _require_asset(upload: RobotIngestUpload, asset_id: str) -> RobotIngestAsset:
        asset = next((item for item in upload.assets if item.asset_id == asset_id), None)
        if asset is None:
            raise problem(
                status=404,
                code="ROBOT_INGEST_ASSET_NOT_FOUND",
                title="Robot upload asset not found",
                detail="The requested asset is not declared by this upload.",
            )
        return asset

    def _token_digest(self, token: str) -> str:
        return hmac.new(self._credential_key, token.encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def _authentication_problem(failure_code: str | None = None) -> ProblemException:
        safe_code = (
            failure_code
            if failure_code
            in {
                "ROBOT_CREDENTIAL_INVALID",
                "ROBOT_CREDENTIAL_REVOKED",
                "ROBOT_CREDENTIAL_EXPIRED",
                "ROBOT_IDENTITY_DISABLED",
            }
            else "ROBOT_CREDENTIAL_INVALID"
        )
        return problem(
            status=401,
            code=safe_code,
            title="Robot authentication failed",
            detail="The robot upload credential is not valid for inbound uploads.",
        )

    @staticmethod
    def _scope_mismatch_problem() -> ProblemException:
        return problem(
            status=403,
            code="UPLOAD_SCOPE_MISMATCH",
            title="Upload scope does not match the task",
            detail="Compatibility scope fields must match the authoritative collection task.",
        )

    @staticmethod
    def _upload_state_problem(upload: RobotIngestUpload) -> ProblemException:
        return problem(
            status=409,
            code="ROBOT_INGEST_UPLOAD_STATE_INVALID",
            title="Robot upload state is invalid",
            detail=(
                "The requested operation is not available while the upload is "
                f"{upload.state.value}."
            ),
        )

    @staticmethod
    def _validate_policy(auth: RobotIngestAuthContext, manifest: RobotIngestUploadManifest) -> None:
        policy = auth.upload_policy
        if len(manifest.assets) > policy.max_assets:
            raise problem(
                status=422,
                code="ROBOT_INGEST_ASSET_LIMIT_EXCEEDED",
                title="Upload asset limit exceeded",
                detail="The manifest declares more assets than this identity permits.",
            )
        if any(asset.size_bytes > policy.max_asset_size_bytes for asset in manifest.assets):
            raise problem(
                status=422,
                code="ROBOT_INGEST_ASSET_SIZE_EXCEEDED",
                title="Upload asset is too large",
                detail="At least one Raw asset exceeds this identity's upload policy.",
            )
        if sum(asset.size_bytes for asset in manifest.assets) > policy.max_batch_size_bytes:
            raise problem(
                status=422,
                code="ROBOT_INGEST_BATCH_SIZE_EXCEEDED",
                title="Upload batch is too large",
                detail="The Raw asset batch exceeds this identity's upload policy.",
            )

    def _object_checksums(self, object_key: str) -> tuple[str, int]:
        sha256 = hashlib.sha256()
        crc64 = 0
        for chunk in self.storage.read_chunks(object_key):
            sha256.update(chunk)
            crc64 = crc64_ecma(chunk, crc64)
        return sha256.hexdigest(), crc64

    def _record_rejection(
        self,
        auth: RobotIngestAuthContext,
        manifest: RobotIngestUploadManifest,
        stage: str,
        code: str,
        *,
        target: UploadTarget | None = None,
        upload_id: str | None = None,
    ) -> None:
        self._safe_attempt(
            RobotIngestAttempt(
                attempt_id=f"ria-{uuid4().hex}",
                organization_id=auth.organization_id,
                project_id=None if target is None else target.project_id,
                region_code=None if target is None else target.region_code,
                authenticated_robot_id=auth.authenticated_robot_id,
                request_robot_id=manifest.robot_id,
                collection_task_id=manifest.collection_task_id,
                source_format=manifest.source_format,
                outcome=AttemptOutcome.REJECTED,
                failure_stage=stage,
                failure_code=code,
                upload_id=upload_id,
                occurred_at=self._clock(),
            )
        )

    def _safe_attempt(self, attempt: RobotIngestAttempt) -> None:
        # Audit persistence must not turn the safe authentication response into a 500.
        try:
            self.repository.append_attempt(attempt)
        except Exception:
            return


def _manifest_fingerprint(manifest: RobotIngestUploadManifest) -> str:
    import json

    payload = json.dumps(
        # These legacy scope hints are checked against the authoritative task target,
        # but are not immutable upload content. Adding or omitting a matching hint on
        # retry must still recover the same client upload session.
        manifest.model_dump(mode="json", exclude={"organization_id", "project_id"}),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _storage_prefix(
    *,
    organization_id: str,
    project_id: str,
    collection_task_id: str,
    robot_id: str,
    upload_id: str,
) -> str:
    return f"raw/{organization_id}/{project_id}/{collection_task_id}/{robot_id}/{upload_id}"


def _validate_time_filter(created_from: datetime | None, created_to: datetime | None) -> None:
    if (created_from is not None and created_from.tzinfo is None) or (
        created_to is not None and created_to.tzinfo is None
    ):
        raise problem(
            status=422,
            code="ROBOT_INGEST_TIME_FILTER_INVALID",
            title="Robot upload time filter is invalid",
            detail="Time filters must include a timezone.",
        )
    if created_from is not None and created_to is not None and created_from >= created_to:
        raise problem(
            status=422,
            code="ROBOT_INGEST_TIME_FILTER_INVALID",
            title="Robot upload time filter is invalid",
            detail="created_from must be earlier than created_to.",
        )


def _replace_asset(
    assets: Sequence[RobotIngestAsset], replacement: RobotIngestAsset
) -> tuple[RobotIngestAsset, ...]:
    return tuple(
        replacement if asset.asset_id == replacement.asset_id else asset for asset in assets
    )
