"""Registry persistence ports and implementations for P14 robot-model reads."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from datetime import datetime
from threading import RLock
from typing import Any, Protocol, cast
from uuid import uuid4

from .models import (
    BlockedReason,
    RobotAssetRole,
    RobotAssetUploadStatus,
    RobotModelAsset,
    RobotModelAssetUploadFile,
    RobotModelAssetUploadSession,
    RobotModelDraftScope,
    RobotModelJointMapping,
    RobotModelPublishCheck,
    RobotModelSummary,
    RobotModelVersion,
)


@dataclass(frozen=True, slots=True)
class RegistryAuditEvent:
    organization_id: str
    project_id: str
    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    request_id: str
    outcome: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class RobotModelAssetUploadFileRecord:
    relative_path: str
    role: RobotAssetRole
    media_type: str
    size_bytes: int
    sha256: str
    object_key: str
    multipart_upload_id: str
    status: RobotAssetUploadStatus
    completed_etag: str | None
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class RobotModelAssetUploadRecord:
    organization_id: str
    project_id: str
    upload_id: str
    version_id: str
    idempotency_key: str
    request_fingerprint: str
    status: RobotAssetUploadStatus
    files: tuple[RobotModelAssetUploadFileRecord, ...]
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    def public(self, *, part_size_bytes: int) -> RobotModelAssetUploadSession:
        return RobotModelAssetUploadSession(
            upload_id=self.upload_id,
            version_id=self.version_id,
            status=self.status,
            files=tuple(
                RobotModelAssetUploadFile(
                    relative_path=item.relative_path,
                    role=item.role,
                    media_type=item.media_type,
                    size_bytes=item.size_bytes,
                    sha256=item.sha256,
                    status=item.status,
                    part_size_bytes=part_size_bytes,
                    total_parts=(item.size_bytes + part_size_bytes - 1) // part_size_bytes,
                )
                for item in self.files
            ),
            created_at=self.created_at,
            updated_at=self.updated_at,
            completed_at=self.completed_at,
        )


@dataclass(frozen=True, slots=True)
class RobotModelImportCleanup:
    """Storage objects owned exclusively by a discarded import attempt."""

    files: tuple[RobotModelAssetUploadFileRecord, ...]


@dataclass(frozen=True, slots=True)
class RobotModelPublishPreflightRecord:
    organization_id: str
    project_id: str
    preflight_id: str
    version_id: str
    idempotency_key: str
    request_fingerprint: str
    expected_etag: str
    asset_manifest_hash: str | None
    mapping_hash: str | None
    allowed: bool
    status: str
    token_hash: str
    checks: tuple[RobotModelPublishCheck, ...]
    expires_at: datetime
    created_at: datetime
    consumed_at: datetime | None


class RegistryRepository(Protocol):
    def has_organization_project(self, *, organization_id: str, project_id: str) -> bool: ...

    def list_robot_models(
        self, *, organization_id: str, project_id: str, query: str | None
    ) -> tuple[RobotModelSummary, ...]: ...

    def get_robot_model_version(
        self, *, organization_id: str, project_id: str, version_id: str
    ) -> RobotModelVersion | None: ...

    def create_robot_model(
        self,
        *,
        organization_id: str,
        project_id: str,
        model_id: str,
        version_id: str,
        manufacturer: str,
        model_code: str,
        display_name: str,
        version_label: str,
        idempotency_key: str,
        request_fingerprint: str,
        created_at: datetime,
    ) -> RobotModelVersion: ...

    def create_robot_model_draft(
        self,
        *,
        organization_id: str,
        project_id: str,
        source_version_id: str,
        new_version_id: str,
        version_label: str,
        update_scope: RobotModelDraftScope,
        idempotency_key: str,
        request_fingerprint: str,
        created_at: datetime,
    ) -> RobotModelVersion: ...

    def find_asset_upload_by_idempotency(
        self,
        *,
        organization_id: str,
        project_id: str,
        version_id: str,
        idempotency_key: str,
    ) -> RobotModelAssetUploadRecord | None: ...

    def create_asset_upload(self, record: RobotModelAssetUploadRecord) -> None: ...

    def get_asset_upload(
        self, *, organization_id: str, project_id: str, upload_id: str
    ) -> RobotModelAssetUploadRecord | None: ...

    def complete_asset_upload_file(
        self,
        *,
        organization_id: str,
        project_id: str,
        upload_id: str,
        relative_path: str,
        completed_etag: str,
        completed_at: datetime,
        asset: RobotModelAsset,
        content: bytes,
    ) -> RobotModelAssetUploadRecord: ...

    def list_robot_model_assets(
        self, *, organization_id: str, project_id: str, version_id: str
    ) -> tuple[RobotModelAsset, ...]: ...

    def get_robot_model_asset(
        self, *, organization_id: str, project_id: str, asset_id: str
    ) -> tuple[str, RobotModelAsset, bytes | None] | None: ...

    def store_robot_model_asset_content(
        self,
        *,
        organization_id: str,
        project_id: str,
        asset_id: str,
        content: bytes,
    ) -> None: ...

    def finalize_asset_upload(
        self,
        *,
        organization_id: str,
        project_id: str,
        upload_id: str,
        asset_manifest_hash: str,
        updated_at: datetime,
    ) -> RobotModelAssetUploadRecord: ...

    def list_robot_model_joint_mappings(
        self, *, organization_id: str, project_id: str, version_id: str
    ) -> tuple[RobotModelJointMapping, ...]: ...

    def replace_robot_model_joint_mappings(
        self,
        *,
        organization_id: str,
        project_id: str,
        version_id: str,
        expected_etag: str,
        idempotency_key: str,
        request_fingerprint: str,
        mappings: tuple[RobotModelJointMapping, ...],
        mapping_hash: str,
        updated_at: datetime,
    ) -> RobotModelVersion: ...

    def find_publish_preflight_by_idempotency(
        self,
        *,
        organization_id: str,
        project_id: str,
        version_id: str,
        idempotency_key: str,
    ) -> RobotModelPublishPreflightRecord | None: ...

    def create_publish_preflight(self, record: RobotModelPublishPreflightRecord) -> None: ...

    def publish_robot_model_version(
        self,
        *,
        organization_id: str,
        project_id: str,
        version_id: str,
        preflight_id: str,
        token_hash: str,
        idempotency_key: str,
        expected_etag: str,
        occurred_at: datetime,
    ) -> RobotModelVersion: ...

    def discard_robot_model_import(
        self,
        *,
        organization_id: str,
        project_id: str,
        version_id: str,
        fallback_version_id: str | None,
    ) -> RobotModelImportCleanup | None: ...

    def append_audit(self, event: RegistryAuditEvent) -> None: ...


class InMemoryRegistryRepository:
    """Thread-safe reference repository; production composition uses PostgreSQL."""

    def __init__(
        self,
        *,
        models: tuple[tuple[str, RobotModelSummary], ...] = (),
        versions: tuple[tuple[str, RobotModelVersion], ...] = (),
        organization_projects: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self._models = list(models)
        self._versions = {(organization_id, value.id): value for organization_id, value in versions}
        self._organization_projects = frozenset(organization_projects)
        self._asset_uploads: dict[tuple[str, str, str], RobotModelAssetUploadRecord] = {}
        self._assets: dict[tuple[str, str, str], tuple[str, str, RobotModelAsset]] = {}
        self._asset_contents: dict[tuple[str, str, str], bytes] = {}
        self._joint_mappings: dict[tuple[str, str, str], tuple[RobotModelJointMapping, ...]] = {}
        self._command_receipts: dict[
            tuple[str, str, str, str, str], tuple[str, RobotModelVersion]
        ] = {}
        self._publish_preflights: dict[tuple[str, str, str], RobotModelPublishPreflightRecord] = {}
        self.audit_events: list[RegistryAuditEvent] = []
        self._lock = RLock()

    def list_robot_models(
        self, *, organization_id: str, project_id: str, query: str | None
    ) -> tuple[RobotModelSummary, ...]:
        if (organization_id, project_id) not in self._organization_projects:
            return ()
        needle = query.casefold().strip() if query else ""
        with self._lock:
            values = [
                value
                for candidate_organization, value in self._models
                if candidate_organization == organization_id
                and (
                    not needle
                    or needle in value.display_name.casefold()
                    or needle in value.model_code.casefold()
                    or needle in value.manufacturer.casefold()
                )
            ]
        return tuple(sorted(values, key=lambda value: (value.display_name.casefold(), value.id)))

    def has_organization_project(self, *, organization_id: str, project_id: str) -> bool:
        return (organization_id, project_id) in self._organization_projects

    def get_robot_model_version(
        self, *, organization_id: str, project_id: str, version_id: str
    ) -> RobotModelVersion | None:
        if (organization_id, project_id) not in self._organization_projects:
            return None
        with self._lock:
            candidate = self._versions.get((organization_id, version_id))
            return candidate

    def create_robot_model(
        self,
        *,
        organization_id: str,
        project_id: str,
        model_id: str,
        version_id: str,
        manufacturer: str,
        model_code: str,
        display_name: str,
        version_label: str,
        idempotency_key: str,
        request_fingerprint: str,
        created_at: datetime,
    ) -> RobotModelVersion:
        with self._lock:
            prior = next(
                (
                    receipt
                    for key, receipt in self._command_receipts.items()
                    if key[0] == organization_id
                    and key[1] == project_id
                    and key[3] == "CREATE_MODEL_DRAFT"
                    and key[4] == idempotency_key
                ),
                None,
            )
            if prior is not None:
                if prior[0] != request_fingerprint:
                    raise ValueError("robot model idempotency key was reused")
                return prior[1]
            if any(
                candidate_organization == organization_id
                and candidate.manufacturer.casefold() == manufacturer.casefold()
                and candidate.model_code.casefold() == model_code.casefold()
                for candidate_organization, candidate in self._models
            ):
                raise ValueError("robot model identity already exists")
            model = RobotModelSummary(
                id=model_id,
                manufacturer=manufacturer,
                model_code=model_code,
                display_name=display_name,
                current_published_version_id=None,
            )
            draft = RobotModelVersion(
                id=version_id,
                robot_model_id=model_id,
                version_label=version_label,
                lifecycle="DRAFT",
                asset_availability="MISSING",
                publish_readiness="CONFIGURATION_REQUIRED",
                asset_manifest_hash=None,
                validation_input_hash=None,
                etag=f'"registry:{version_id}:1"',
                allowed_actions=("VIEW", "EDIT_ASSETS", "EDIT_MAPPING", "PREFLIGHT_PUBLISH"),
                blocked_reasons=(),
            )
            self._models.append((organization_id, model))
            self._versions[(organization_id, version_id)] = draft
            self._joint_mappings[(organization_id, project_id, version_id)] = ()
            self._command_receipts[
                (organization_id, project_id, version_id, "CREATE_MODEL_DRAFT", idempotency_key)
            ] = (request_fingerprint, draft)
            return draft

    def create_robot_model_draft(
        self,
        *,
        organization_id: str,
        project_id: str,
        source_version_id: str,
        new_version_id: str,
        version_label: str,
        update_scope: RobotModelDraftScope,
        idempotency_key: str,
        request_fingerprint: str,
        created_at: datetime,
    ) -> RobotModelVersion:
        receipt_key = (
            organization_id,
            project_id,
            source_version_id,
            "CREATE_DRAFT",
            idempotency_key,
        )
        with self._lock:
            prior = self._command_receipts.get(receipt_key)
            if prior is not None:
                if prior[0] != request_fingerprint:
                    raise ValueError("robot model draft idempotency key was reused")
                return prior[1]
            source = self._versions.get((organization_id, source_version_id))
            if source is None:
                raise KeyError(source_version_id)
            if source.lifecycle != "PUBLISHED":
                raise ValueError("robot model draft source is not published")
            if any(
                candidate.robot_model_id == source.robot_model_id
                and candidate.version_label == version_label
                for (candidate_organization, _), candidate in self._versions.items()
                if candidate_organization == organization_id
            ):
                raise ValueError("robot model version label already exists")
            copied_assets = [
                (object_key, asset, self._asset_contents.get(asset_key))
                for asset_key, (
                    object_key,
                    candidate_version_id,
                    asset,
                ) in self._assets.items()
                if asset_key[0] == organization_id
                and asset_key[1] == project_id
                and candidate_version_id == source_version_id
                and not (
                    update_scope is RobotModelDraftScope.ASSETS
                    and asset.role in {RobotAssetRole.URDF, RobotAssetRole.CONFIG}
                )
            ]
            draft = RobotModelVersion(
                id=new_version_id,
                robot_model_id=source.robot_model_id,
                version_label=version_label,
                lifecycle="DRAFT",
                asset_availability=(
                    source.asset_availability
                    if update_scope is RobotModelDraftScope.MAPPINGS
                    else "MISSING"
                ),
                publish_readiness=(
                    "MAPPING_REQUIRED"
                    if update_scope is RobotModelDraftScope.MAPPINGS
                    else "CONFIGURATION_REQUIRED"
                ),
                asset_manifest_hash=(
                    source.asset_manifest_hash
                    if update_scope is RobotModelDraftScope.MAPPINGS
                    else None
                ),
                validation_input_hash=source.validation_input_hash,
                etag=f'"registry:{new_version_id}:1"',
                allowed_actions=("VIEW", "EDIT_ASSETS", "EDIT_MAPPING", "PREFLIGHT_PUBLISH"),
                blocked_reasons=(),
            )
            self._versions[(organization_id, new_version_id)] = draft
            for object_key, asset, content in copied_assets:
                cloned = asset.model_copy(
                    update={"asset_id": str(uuid4()), "created_at": created_at}
                )
                self._assets[(organization_id, project_id, cloned.asset_id)] = (
                    object_key,
                    new_version_id,
                    cloned,
                )
                if content is not None:
                    self._asset_contents[(organization_id, project_id, cloned.asset_id)] = content
            self._joint_mappings[(organization_id, project_id, new_version_id)] = (
                self._joint_mappings.get((organization_id, project_id, source_version_id), ())
            )
            self._command_receipts[receipt_key] = (request_fingerprint, draft)
            return draft

    def find_asset_upload_by_idempotency(
        self,
        *,
        organization_id: str,
        project_id: str,
        version_id: str,
        idempotency_key: str,
    ) -> RobotModelAssetUploadRecord | None:
        with self._lock:
            return next(
                (
                    item
                    for item in self._asset_uploads.values()
                    if item.organization_id == organization_id
                    and item.project_id == project_id
                    and item.version_id == version_id
                    and item.idempotency_key == idempotency_key
                ),
                None,
            )

    def create_asset_upload(self, record: RobotModelAssetUploadRecord) -> None:
        with self._lock:
            existing = self.find_asset_upload_by_idempotency(
                organization_id=record.organization_id,
                project_id=record.project_id,
                version_id=record.version_id,
                idempotency_key=record.idempotency_key,
            )
            if existing is not None:
                raise ValueError("asset upload idempotency key already exists")
            self._asset_uploads[(record.organization_id, record.project_id, record.upload_id)] = (
                record
            )

    def get_asset_upload(
        self, *, organization_id: str, project_id: str, upload_id: str
    ) -> RobotModelAssetUploadRecord | None:
        with self._lock:
            return self._asset_uploads.get((organization_id, project_id, upload_id))

    def complete_asset_upload_file(
        self,
        *,
        organization_id: str,
        project_id: str,
        upload_id: str,
        relative_path: str,
        completed_etag: str,
        completed_at: datetime,
        asset: RobotModelAsset,
        content: bytes,
    ) -> RobotModelAssetUploadRecord:
        key = (organization_id, project_id, upload_id)
        with self._lock:
            record = self._asset_uploads[key]
            files = tuple(
                item
                if item.relative_path != relative_path
                or item.status is RobotAssetUploadStatus.COMPLETED
                else RobotModelAssetUploadFileRecord(
                    relative_path=item.relative_path,
                    role=item.role,
                    media_type=item.media_type,
                    size_bytes=item.size_bytes,
                    sha256=item.sha256,
                    object_key=item.object_key,
                    multipart_upload_id=item.multipart_upload_id,
                    status=RobotAssetUploadStatus.COMPLETED,
                    completed_etag=completed_etag,
                    completed_at=completed_at,
                )
                for item in record.files
            )
            if not any(item.relative_path == relative_path for item in record.files):
                raise KeyError(relative_path)
            updated = RobotModelAssetUploadRecord(
                organization_id=record.organization_id,
                project_id=record.project_id,
                upload_id=record.upload_id,
                version_id=record.version_id,
                idempotency_key=record.idempotency_key,
                request_fingerprint=record.request_fingerprint,
                status=record.status,
                files=files,
                created_at=record.created_at,
                updated_at=completed_at,
                completed_at=record.completed_at,
            )
            self._asset_uploads[key] = updated
            for asset_key, (_, asset_version_id, existing_asset) in tuple(self._assets.items()):
                if (
                    asset_key[0] == organization_id
                    and asset_key[1] == project_id
                    and asset_version_id == record.version_id
                    and existing_asset.relative_path == relative_path
                ):
                    del self._assets[asset_key]
                    self._asset_contents.pop(asset_key, None)
            self._assets[(organization_id, project_id, asset.asset_id)] = (
                files[[item.relative_path for item in files].index(relative_path)].object_key,
                record.version_id,
                asset,
            )
            self._asset_contents[(organization_id, project_id, asset.asset_id)] = content
            return updated

    def list_robot_model_assets(
        self, *, organization_id: str, project_id: str, version_id: str
    ) -> tuple[RobotModelAsset, ...]:
        with self._lock:
            values = [
                asset
                for (organization, project, _), (
                    _key,
                    asset_version_id,
                    asset,
                ) in self._assets.items()
                if organization == organization_id
                and project == project_id
                and asset_version_id == version_id
            ]
        return tuple(sorted(values, key=lambda item: (item.relative_path, item.asset_id)))

    def get_robot_model_asset(
        self, *, organization_id: str, project_id: str, asset_id: str
    ) -> tuple[str, RobotModelAsset, bytes | None] | None:
        with self._lock:
            key = (organization_id, project_id, asset_id)
            found = self._assets.get(key)
            return None if found is None else (found[0], found[2], self._asset_contents.get(key))

    def store_robot_model_asset_content(
        self,
        *,
        organization_id: str,
        project_id: str,
        asset_id: str,
        content: bytes,
    ) -> None:
        with self._lock:
            key = (organization_id, project_id, asset_id)
            if key not in self._assets:
                raise KeyError(asset_id)
            self._asset_contents[key] = content

    def list_robot_model_joint_mappings(
        self, *, organization_id: str, project_id: str, version_id: str
    ) -> tuple[RobotModelJointMapping, ...]:
        with self._lock:
            return self._joint_mappings.get((organization_id, project_id, version_id), ())

    def replace_robot_model_joint_mappings(
        self,
        *,
        organization_id: str,
        project_id: str,
        version_id: str,
        expected_etag: str,
        idempotency_key: str,
        request_fingerprint: str,
        mappings: tuple[RobotModelJointMapping, ...],
        mapping_hash: str,
        updated_at: datetime,
    ) -> RobotModelVersion:
        with self._lock:
            receipt_key = (
                organization_id,
                project_id,
                version_id,
                "REPLACE_JOINT_MAPPINGS",
                idempotency_key,
            )
            prior = self._command_receipts.get(receipt_key)
            if prior is not None:
                if prior[0] != request_fingerprint:
                    raise ValueError("joint mapping idempotency key was reused")
                return prior[1]
            version_key = (organization_id, version_id)
            version = self._versions.get(version_key)
            if version is None:
                raise KeyError(version_id)
            if version.etag != expected_etag:
                raise ValueError("robot model version etag does not match")
            self._joint_mappings[(organization_id, project_id, version_id)] = tuple(
                sorted(mappings, key=lambda item: item.source_joint_name)
            )
            readiness = (
                "SAMPLE_VALIDATION_REQUIRED"
                if version.asset_availability == "AVAILABLE"
                else "CONFIGURATION_REQUIRED"
            )
            updated = version.model_copy(
                update={
                    "publish_readiness": readiness,
                    "validation_input_hash": mapping_hash,
                    "etag": f'"registry:{version_id}:{updated_at.timestamp():.6f}"',
                }
            )
            self._versions[version_key] = updated
            self._command_receipts[receipt_key] = (request_fingerprint, updated)
            return updated

    def find_publish_preflight_by_idempotency(
        self,
        *,
        organization_id: str,
        project_id: str,
        version_id: str,
        idempotency_key: str,
    ) -> RobotModelPublishPreflightRecord | None:
        with self._lock:
            return next(
                (
                    item
                    for item in self._publish_preflights.values()
                    if item.organization_id == organization_id
                    and item.project_id == project_id
                    and item.version_id == version_id
                    and item.idempotency_key == idempotency_key
                ),
                None,
            )

    def create_publish_preflight(self, record: RobotModelPublishPreflightRecord) -> None:
        with self._lock:
            existing = self.find_publish_preflight_by_idempotency(
                organization_id=record.organization_id,
                project_id=record.project_id,
                version_id=record.version_id,
                idempotency_key=record.idempotency_key,
            )
            if existing is not None:
                raise ValueError("publish preflight idempotency key already exists")
            self._publish_preflights[
                (record.organization_id, record.project_id, record.preflight_id)
            ] = record

    def publish_robot_model_version(
        self,
        *,
        organization_id: str,
        project_id: str,
        version_id: str,
        preflight_id: str,
        token_hash: str,
        idempotency_key: str,
        expected_etag: str,
        occurred_at: datetime,
    ) -> RobotModelVersion:
        key = (organization_id, project_id, preflight_id)
        with self._lock:
            preflight = self._publish_preflights.get(key)
            if preflight is None:
                raise KeyError(preflight_id)
            if preflight.token_hash != token_hash:
                raise PermissionError("publish preflight token does not match")
            if preflight.idempotency_key != idempotency_key:
                raise ValueError("publish idempotency key does not match preflight")
            version = self._versions.get((organization_id, version_id))
            if version is None:
                raise KeyError(version_id)
            if preflight.status == "CONSUMED":
                return version
            if preflight.status != "ISSUED" or preflight.expires_at <= occurred_at:
                raise TimeoutError("publish preflight has expired")
            if version.etag != expected_etag or preflight.expected_etag != expected_etag:
                raise ValueError("robot model version etag does not match")
            if not preflight.allowed or version.lifecycle != "DRAFT":
                raise ValueError("robot model version cannot be published")
            if version.asset_manifest_hash != preflight.asset_manifest_hash:
                raise ValueError("robot model asset manifest changed")
            mappings = self._joint_mappings.get((organization_id, project_id, version_id), ())
            if _joint_mapping_hash(mappings) != preflight.mapping_hash:
                raise ValueError("robot model joint mappings changed")
            updated = version.model_copy(
                update={
                    "lifecycle": "PUBLISHED",
                    "publish_readiness": "READY",
                    "etag": f'"registry:{version_id}:{occurred_at.timestamp():.6f}"',
                }
            )
            self._versions[(organization_id, version_id)] = updated
            self._models = [
                (
                    candidate_organization,
                    model.model_copy(update={"current_published_version_id": version_id}),
                )
                if candidate_organization == organization_id and model.id == version.robot_model_id
                else (candidate_organization, model)
                for candidate_organization, model in self._models
            ]
            self._publish_preflights[key] = dataclass_replace(
                preflight, status="CONSUMED", consumed_at=occurred_at
            )
            return updated

    def discard_robot_model_import(
        self,
        *,
        organization_id: str,
        project_id: str,
        version_id: str,
        fallback_version_id: str | None,
    ) -> RobotModelImportCleanup | None:
        with self._lock:
            version = self._versions.get((organization_id, version_id))
            if version is None:
                return None
            if version.lifecycle not in {"DRAFT", "PUBLISHED"}:
                raise ValueError("robot model import is in use")

            fallback = (
                None
                if fallback_version_id is None
                else self._versions.get((organization_id, fallback_version_id))
            )
            if fallback_version_id is not None and (
                fallback_version_id == version_id
                or fallback is None
                or fallback.robot_model_id != version.robot_model_id
                or fallback.lifecycle != "PUBLISHED"
            ):
                raise ValueError("robot model fallback version is invalid")

            uploads = tuple(
                upload
                for (
                    candidate_organization,
                    _candidate_project,
                    _,
                ), upload in self._asset_uploads.items()
                if candidate_organization == organization_id and upload.version_id == version_id
            )
            cleanup = RobotModelImportCleanup(
                files=tuple(item for upload in uploads for item in upload.files)
            )
            upload_keys = {
                key
                for key, upload in self._asset_uploads.items()
                if key[0] == organization_id and upload.version_id == version_id
            }
            for key in upload_keys:
                del self._asset_uploads[key]
            for key, (_object_key, candidate_version_id, _asset) in tuple(self._assets.items()):
                if key[0] == organization_id and candidate_version_id == version_id:
                    del self._assets[key]
                    self._asset_contents.pop(key, None)
            for key in tuple(self._joint_mappings):
                if key[0] == organization_id and key[2] == version_id:
                    del self._joint_mappings[key]
            for key, item in tuple(self._publish_preflights.items()):
                if key[0] == organization_id and item.version_id == version_id:
                    del self._publish_preflights[key]
            for key, (_fingerprint, response) in tuple(self._command_receipts.items()):
                if key[0] == organization_id and (
                    key[2] == version_id or response.id == version_id
                ):
                    del self._command_receipts[key]

            del self._versions[(organization_id, version_id)]
            remaining_versions = tuple(
                candidate
                for (candidate_organization, _), candidate in self._versions.items()
                if candidate_organization == organization_id
                and candidate.robot_model_id == version.robot_model_id
            )
            if not remaining_versions:
                self._models = [
                    (candidate_organization, model)
                    for candidate_organization, model in self._models
                    if not (
                        candidate_organization == organization_id
                        and model.id == version.robot_model_id
                    )
                ]
            else:
                self._models = [
                    (
                        candidate_organization,
                        model.model_copy(
                            update={
                                "current_published_version_id": (
                                    fallback_version_id
                                    if model.current_published_version_id == version_id
                                    else model.current_published_version_id
                                )
                            }
                        ),
                    )
                    if candidate_organization == organization_id
                    and model.id == version.robot_model_id
                    else (candidate_organization, model)
                    for candidate_organization, model in self._models
                ]
            return cleanup

    def finalize_asset_upload(
        self,
        *,
        organization_id: str,
        project_id: str,
        upload_id: str,
        asset_manifest_hash: str,
        updated_at: datetime,
    ) -> RobotModelAssetUploadRecord:
        key = (organization_id, project_id, upload_id)
        with self._lock:
            record = self._asset_uploads[key]
            if any(item.status is not RobotAssetUploadStatus.COMPLETED for item in record.files):
                raise ValueError("asset upload is not complete")
            updated = RobotModelAssetUploadRecord(
                organization_id=record.organization_id,
                project_id=record.project_id,
                upload_id=record.upload_id,
                version_id=record.version_id,
                idempotency_key=record.idempotency_key,
                request_fingerprint=record.request_fingerprint,
                status=RobotAssetUploadStatus.COMPLETED,
                files=record.files,
                created_at=record.created_at,
                updated_at=updated_at,
                completed_at=updated_at,
            )
            self._asset_uploads[key] = updated
            version_key = (organization_id, record.version_id)
            version = self._versions.get(version_key)
            if version is not None:
                self._versions[version_key] = version.model_copy(
                    update={
                        "asset_availability": "AVAILABLE",
                        "asset_manifest_hash": asset_manifest_hash,
                        "etag": f'"registry:{record.version_id}:{updated_at.timestamp():.6f}"',
                    }
                )
            return updated

    def append_audit(self, event: RegistryAuditEvent) -> None:
        with self._lock:
            self.audit_events.append(event)


class DbApiCursor(Protocol):
    description: Sequence[Sequence[Any]] | None

    def execute(self, query: str, params: Sequence[object] = ()) -> object: ...

    def fetchone(self) -> object | None: ...

    def fetchall(self) -> Sequence[object]: ...

    def close(self) -> None: ...


class DbApiConnection(Protocol):
    def cursor(self) -> DbApiCursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


ConnectionFactory = Callable[[], DbApiConnection]


def _row(cursor: DbApiCursor, raw: object) -> dict[str, object]:
    if isinstance(raw, Mapping):
        return {str(key): value for key, value in raw.items()}
    if cursor.description is None:
        raise RuntimeError("database cursor did not describe its result columns")
    names = (str(column[0]) for column in cursor.description)
    values = cast(Sequence[object], raw)
    return dict(zip(names, values, strict=True))


def _blocked_reasons(value: object) -> tuple[BlockedReason, ...]:
    decoded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(decoded, list):
        return ()
    return tuple(BlockedReason.model_validate(item) for item in decoded)


def _version(row: Mapping[str, object]) -> RobotModelVersion:
    allowed_actions = row.get("allowed_actions")
    actions = tuple(allowed_actions) if isinstance(allowed_actions, list | tuple) else ()
    return RobotModelVersion(
        id=str(row["version_id"]),
        robot_model_id=str(row["robot_model_id"]),
        version_label=str(row["version_label"]),
        lifecycle=str(row["lifecycle"]),
        asset_availability=str(row["asset_availability"]),
        publish_readiness=str(row["publish_readiness"]),
        asset_manifest_hash=(
            None if row.get("asset_manifest_hash") is None else str(row["asset_manifest_hash"])
        ),
        validation_input_hash=(
            None if row.get("validation_input_hash") is None else str(row["validation_input_hash"])
        ),
        etag=str(row["etag"]),
        allowed_actions=tuple(str(item) for item in actions),
        blocked_reasons=_blocked_reasons(row.get("blocked_reasons", [])),
    )


def _asset_upload_file(row: Mapping[str, object]) -> RobotModelAssetUploadFileRecord:
    return RobotModelAssetUploadFileRecord(
        relative_path=str(row["relative_path"]),
        role=RobotAssetRole(str(row["role"])),
        media_type=str(row["media_type"]),
        size_bytes=int(cast(int | str, row["expected_size"])),
        sha256=str(row["expected_sha256"]),
        object_key=str(row["object_key"]),
        multipart_upload_id=str(row["multipart_upload_id"]),
        status=RobotAssetUploadStatus(str(row["status"])),
        completed_etag=(None if row.get("completed_etag") is None else str(row["completed_etag"])),
        completed_at=cast(datetime | None, row.get("completed_at")),
    )


def _asset(row: Mapping[str, object]) -> RobotModelAsset:
    return RobotModelAsset(
        asset_id=str(row["asset_id"]),
        relative_path=str(row["relative_path"]),
        role=RobotAssetRole(str(row["role"])),
        media_type=str(row["media_type"]),
        size_bytes=int(cast(int | str, row["size_bytes"])),
        sha256=str(row["sha256"]),
        created_at=cast(datetime, row["created_at"]),
    )


def _joint_mapping_hash(mappings: tuple[RobotModelJointMapping, ...]) -> str:
    encoded = json.dumps(
        [
            item.model_dump(mode="json")
            for item in sorted(mappings, key=lambda candidate: candidate.source_joint_name)
        ],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _publish_preflight(row: Mapping[str, object]) -> RobotModelPublishPreflightRecord:
    raw_checks = row["checks"]
    checks = json.loads(raw_checks) if isinstance(raw_checks, str) else raw_checks
    if not isinstance(checks, list):
        raise RuntimeError("publish preflight checks are malformed")
    return RobotModelPublishPreflightRecord(
        organization_id=str(row["organization_id"]),
        project_id=str(row["project_id"]),
        preflight_id=str(row["preflight_id"]),
        version_id=str(row["version_id"]),
        idempotency_key=str(row["idempotency_key"]),
        request_fingerprint=str(row["request_fingerprint"]),
        expected_etag=str(row["expected_etag"]),
        asset_manifest_hash=(
            None if row["asset_manifest_hash"] is None else str(row["asset_manifest_hash"])
        ),
        mapping_hash=None if row["mapping_hash"] is None else str(row["mapping_hash"]),
        allowed=bool(row["allowed"]),
        status=str(row["status"]),
        token_hash=str(row["token_hash"]),
        checks=tuple(RobotModelPublishCheck.model_validate(item) for item in checks),
        expires_at=cast(datetime, row["expires_at"]),
        created_at=cast(datetime, row["created_at"]),
        consumed_at=cast(datetime | None, row["consumed_at"]),
    )


class PostgresRegistryRepository:
    """RLS-compatible PostgreSQL repository for P14 registry reads and audit facts."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def has_organization_project(self, *, organization_id: str, project_id: str) -> bool:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT 1
                  FROM registry.organization_projects
                 WHERE organization_id = %s AND project_id = %s
                """,
                (organization_id, project_id),
            )
            return cursor.fetchone() is not None
        finally:
            cursor.close()
            connection.close()

    def create_robot_model(
        self,
        *,
        organization_id: str,
        project_id: str,
        model_id: str,
        version_id: str,
        manufacturer: str,
        model_code: str,
        display_name: str,
        version_label: str,
        idempotency_key: str,
        request_fingerprint: str,
        created_at: datetime,
    ) -> RobotModelVersion:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT request_fingerprint, response
                  FROM registry.robot_model_command_receipts
                 WHERE organization_id = %s AND project_id = %s
                   AND operation = 'CREATE_MODEL_DRAFT' AND idempotency_key = %s
                """,
                (organization_id, project_id, idempotency_key),
            )
            receipt_raw = cursor.fetchone()
            if receipt_raw is not None:
                receipt = _row(cursor, receipt_raw)
                if str(receipt["request_fingerprint"]) != request_fingerprint:
                    raise ValueError("robot model idempotency key was reused")
                response = receipt["response"]
                decoded = json.loads(response) if isinstance(response, str) else response
                if not isinstance(decoded, Mapping):
                    raise RuntimeError("robot model command receipt is malformed")
                connection.commit()
                return RobotModelVersion.model_validate(decoded)

            cursor.execute(
                """
                SELECT 1
                  FROM registry.robot_models
                 WHERE organization_id = %s
                   AND lower(manufacturer) = lower(%s)
                   AND lower(model_code) = lower(%s)
                """,
                (organization_id, manufacturer, model_code),
            )
            if cursor.fetchone() is not None:
                raise ValueError("robot model identity already exists")

            draft = RobotModelVersion(
                id=version_id,
                robot_model_id=model_id,
                version_label=version_label,
                lifecycle="DRAFT",
                asset_availability="MISSING",
                publish_readiness="CONFIGURATION_REQUIRED",
                asset_manifest_hash=None,
                validation_input_hash=None,
                etag=f'"registry:{version_id}:1"',
                allowed_actions=("VIEW", "EDIT_ASSETS", "EDIT_MAPPING", "PREFLIGHT_PUBLISH"),
                blocked_reasons=(),
            )
            cursor.execute(
                """
                INSERT INTO registry.robot_models (
                    organization_id, model_id, manufacturer, model_code,
                    display_name, current_published_version_id, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, NULL, %s, %s)
                """,
                (
                    organization_id,
                    model_id,
                    manufacturer,
                    model_code,
                    display_name,
                    created_at,
                    created_at,
                ),
            )
            cursor.execute(
                """
                INSERT INTO registry.robot_model_versions (
                    organization_id, version_id, robot_model_id, version_label,
                    lifecycle, asset_availability, publish_readiness,
                    asset_manifest_hash, validation_input_hash, etag,
                    allowed_actions, blocked_reasons, revision, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, 'DRAFT', 'MISSING',
                          'CONFIGURATION_REQUIRED', NULL, NULL, %s,
                          %s::jsonb, '[]'::jsonb, 1, %s, %s)
                """,
                (
                    organization_id,
                    version_id,
                    model_id,
                    version_label,
                    draft.etag,
                    json.dumps(list(draft.allowed_actions)),
                    created_at,
                    created_at,
                ),
            )
            cursor.execute(
                """
                INSERT INTO registry.robot_model_command_receipts (
                    organization_id, project_id, version_id, operation, idempotency_key,
                    request_fingerprint, response, created_at
                ) VALUES (%s, %s, %s, 'CREATE_MODEL_DRAFT', %s, %s, %s::jsonb, %s)
                """,
                (
                    organization_id,
                    project_id,
                    version_id,
                    idempotency_key,
                    request_fingerprint,
                    json.dumps(draft.model_dump(mode="json")),
                    created_at,
                ),
            )
            connection.commit()
            return draft
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def create_robot_model_draft(
        self,
        *,
        organization_id: str,
        project_id: str,
        source_version_id: str,
        new_version_id: str,
        version_label: str,
        update_scope: RobotModelDraftScope,
        idempotency_key: str,
        request_fingerprint: str,
        created_at: datetime,
    ) -> RobotModelVersion:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT request_fingerprint, response
                  FROM registry.robot_model_command_receipts
                 WHERE organization_id = %s AND project_id = %s
                   AND version_id = %s AND operation = 'CREATE_DRAFT'
                   AND idempotency_key = %s
                """,
                (organization_id, project_id, source_version_id, idempotency_key),
            )
            receipt_raw = cursor.fetchone()
            if receipt_raw is not None:
                receipt = _row(cursor, receipt_raw)
                if str(receipt["request_fingerprint"]) != request_fingerprint:
                    raise ValueError("robot model draft idempotency key was reused")
                response = receipt["response"]
                decoded = json.loads(response) if isinstance(response, str) else response
                if not isinstance(decoded, Mapping):
                    raise RuntimeError("robot model draft command receipt is malformed")
                connection.commit()
                return RobotModelVersion.model_validate(decoded)

            cursor.execute(
                """
                SELECT version_id, robot_model_id, version_label, lifecycle, asset_availability,
                       publish_readiness, asset_manifest_hash, validation_input_hash, etag,
                       allowed_actions, blocked_reasons
                  FROM registry.robot_model_versions
                 WHERE organization_id = %s AND version_id = %s
                 FOR UPDATE
                """,
                (organization_id, source_version_id),
            )
            source_raw = cursor.fetchone()
            if source_raw is None:
                raise KeyError(source_version_id)
            source = _version(_row(cursor, source_raw))
            if source.lifecycle != "PUBLISHED":
                raise ValueError("robot model draft source is not published")
            cursor.execute(
                """
                SELECT 1
                  FROM registry.robot_model_versions
                 WHERE organization_id = %s AND robot_model_id = %s AND version_label = %s
                """,
                (organization_id, source.robot_model_id, version_label),
            )
            if cursor.fetchone() is not None:
                raise ValueError("robot model version label already exists")

            copy_assets = update_scope is RobotModelDraftScope.MAPPINGS
            draft = RobotModelVersion(
                id=new_version_id,
                robot_model_id=source.robot_model_id,
                version_label=version_label,
                lifecycle="DRAFT",
                asset_availability=source.asset_availability if copy_assets else "MISSING",
                publish_readiness=("MAPPING_REQUIRED" if copy_assets else "CONFIGURATION_REQUIRED"),
                asset_manifest_hash=source.asset_manifest_hash if copy_assets else None,
                validation_input_hash=source.validation_input_hash,
                etag=f'"registry:{new_version_id}:1"',
                allowed_actions=("VIEW", "EDIT_ASSETS", "EDIT_MAPPING", "PREFLIGHT_PUBLISH"),
                blocked_reasons=(),
            )
            cursor.execute(
                """
                INSERT INTO registry.robot_model_versions (
                    organization_id, version_id, robot_model_id, version_label,
                    lifecycle, asset_availability, publish_readiness,
                    asset_manifest_hash, validation_input_hash, etag,
                    allowed_actions, blocked_reasons, revision, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, 'DRAFT', %s, %s, %s, %s, %s,
                          %s::jsonb, '[]'::jsonb, 1, %s, %s)
                """,
                (
                    organization_id,
                    new_version_id,
                    source.robot_model_id,
                    version_label,
                    draft.asset_availability,
                    draft.publish_readiness,
                    draft.asset_manifest_hash,
                    draft.validation_input_hash,
                    draft.etag,
                    json.dumps(list(draft.allowed_actions)),
                    created_at,
                    created_at,
                ),
            )
            cursor.execute(
                """
                SELECT relative_path, role, media_type, size_bytes, sha256, object_key,
                       content
                  FROM registry.robot_model_assets
                 WHERE organization_id = %s AND project_id = %s AND version_id = %s
                   AND (%s OR role NOT IN ('URDF', 'CONFIG'))
                 ORDER BY relative_path
                """,
                (organization_id, project_id, source_version_id, copy_assets),
            )
            copied_assets = [_row(cursor, asset_raw) for asset_raw in cursor.fetchall()]
            for asset in copied_assets:
                cursor.execute(
                    """
                    INSERT INTO registry.robot_model_assets (
                        organization_id, project_id, asset_id, version_id, relative_path,
                        role, media_type, size_bytes, sha256, object_key, content, created_at
                    ) VALUES (%s, %s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        organization_id,
                        project_id,
                        str(uuid4()),
                        new_version_id,
                        str(asset["relative_path"]),
                        str(asset["role"]),
                        str(asset["media_type"]),
                        int(cast(int | str, asset["size_bytes"])),
                        str(asset["sha256"]),
                        str(asset["object_key"]),
                        asset["content"],
                        created_at,
                    ),
                )
            cursor.execute(
                """
                INSERT INTO registry.robot_model_joint_mappings (
                    organization_id, project_id, version_id, source_joint_name,
                    target_joint_name, direction, created_at
                )
                SELECT organization_id, project_id, %s, source_joint_name,
                       target_joint_name, direction, %s
                  FROM registry.robot_model_joint_mappings
                 WHERE organization_id = %s AND project_id = %s AND version_id = %s
                """,
                (
                    new_version_id,
                    created_at,
                    organization_id,
                    project_id,
                    source_version_id,
                ),
            )
            cursor.execute(
                """
                INSERT INTO registry.robot_model_command_receipts (
                    organization_id, project_id, version_id, operation, idempotency_key,
                    request_fingerprint, response, created_at
                ) VALUES (%s, %s, %s, 'CREATE_DRAFT', %s, %s, %s::jsonb, %s)
                """,
                (
                    organization_id,
                    project_id,
                    source_version_id,
                    idempotency_key,
                    request_fingerprint,
                    json.dumps(draft.model_dump(mode="json")),
                    created_at,
                ),
            )
            connection.commit()
            return draft
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def list_robot_models(
        self, *, organization_id: str, project_id: str, query: str | None
    ) -> tuple[RobotModelSummary, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT model.model_id, model.manufacturer, model.model_code,
                       model.display_name, model.current_published_version_id
                FROM registry.robot_models model
                JOIN registry.organization_projects membership
                  ON membership.organization_id = model.organization_id
                 WHERE model.organization_id = %s
                   AND membership.project_id = %s
                   AND (
                       %s::text IS NULL
                       OR model.display_name ILIKE '%%' || %s || '%%'
                       OR model.model_code ILIKE '%%' || %s || '%%'
                       OR model.manufacturer ILIKE '%%' || %s || '%%'
                   )
                 ORDER BY lower(model.display_name), model.model_id
                """,
                (organization_id, project_id, query, query, query, query),
            )
            return tuple(
                RobotModelSummary(
                    id=str(row["model_id"]),
                    manufacturer=str(row["manufacturer"]),
                    model_code=str(row["model_code"]),
                    display_name=str(row["display_name"]),
                    current_published_version_id=(
                        None
                        if row["current_published_version_id"] is None
                        else str(row["current_published_version_id"])
                    ),
                )
                for raw in cursor.fetchall()
                for row in (_row(cursor, raw),)
            )
        finally:
            cursor.close()
            connection.close()

    def get_robot_model_version(
        self, *, organization_id: str, project_id: str, version_id: str
    ) -> RobotModelVersion | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT version_id, robot_model_id, version_label, lifecycle, asset_availability,
                       publish_readiness, asset_manifest_hash, validation_input_hash, etag,
                       allowed_actions, blocked_reasons
                  FROM registry.robot_model_versions version
                  JOIN registry.organization_projects membership
                    ON membership.organization_id = version.organization_id
                 WHERE version.organization_id = %s
                   AND membership.project_id = %s
                   AND version.version_id = %s
                """,
                (organization_id, project_id, version_id),
            )
            raw = cursor.fetchone()
            return None if raw is None else _version(_row(cursor, raw))
        finally:
            cursor.close()
            connection.close()

    def find_asset_upload_by_idempotency(
        self,
        *,
        organization_id: str,
        project_id: str,
        version_id: str,
        idempotency_key: str,
    ) -> RobotModelAssetUploadRecord | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT upload_id
                  FROM registry.robot_model_asset_uploads
                 WHERE organization_id = %s AND project_id = %s AND version_id = %s
                   AND idempotency_key = %s
                """,
                (organization_id, project_id, version_id, idempotency_key),
            )
            raw = cursor.fetchone()
            return (
                None
                if raw is None
                else self._get_asset_upload(
                    cursor,
                    organization_id,
                    project_id,
                    str(cast(Sequence[object], raw)[0]),
                )
            )
        finally:
            cursor.close()
            connection.close()

    def create_asset_upload(self, record: RobotModelAssetUploadRecord) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO registry.robot_model_asset_uploads (
                    organization_id, project_id, upload_id, version_id, idempotency_key,
                    request_fingerprint, status, created_at, updated_at, completed_at
                ) VALUES (%s, %s, %s::uuid, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record.organization_id,
                    record.project_id,
                    record.upload_id,
                    record.version_id,
                    record.idempotency_key,
                    record.request_fingerprint,
                    record.status.value,
                    record.created_at,
                    record.updated_at,
                    record.completed_at,
                ),
            )
            for item in record.files:
                cursor.execute(
                    """
                    INSERT INTO registry.robot_model_asset_upload_files (
                        organization_id, project_id, upload_id, relative_path, role, media_type,
                        expected_size, expected_sha256, object_key, multipart_upload_id, status,
                        completed_etag, completed_at
                    ) VALUES (%s, %s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        record.organization_id,
                        record.project_id,
                        record.upload_id,
                        item.relative_path,
                        item.role.value,
                        item.media_type,
                        item.size_bytes,
                        item.sha256,
                        item.object_key,
                        item.multipart_upload_id,
                        item.status.value,
                        item.completed_etag,
                        item.completed_at,
                    ),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def get_asset_upload(
        self, *, organization_id: str, project_id: str, upload_id: str
    ) -> RobotModelAssetUploadRecord | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            return self._get_asset_upload(cursor, organization_id, project_id, upload_id)
        finally:
            cursor.close()
            connection.close()

    def complete_asset_upload_file(
        self,
        *,
        organization_id: str,
        project_id: str,
        upload_id: str,
        relative_path: str,
        completed_etag: str,
        completed_at: datetime,
        asset: RobotModelAsset,
        content: bytes,
    ) -> RobotModelAssetUploadRecord:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT status, role, media_type, expected_size, expected_sha256, object_key
                  FROM registry.robot_model_asset_upload_files
                 WHERE organization_id = %s AND project_id = %s AND upload_id = %s::uuid
                   AND relative_path = %s
                 FOR UPDATE
                """,
                (organization_id, project_id, upload_id, relative_path),
            )
            raw = cursor.fetchone()
            if raw is None:
                raise KeyError(relative_path)
            file_row = _row(cursor, raw)
            if str(file_row["status"]) != RobotAssetUploadStatus.COMPLETED.value:
                cursor.execute(
                    """
                    SELECT version_id
                      FROM registry.robot_model_asset_uploads
                     WHERE organization_id = %s AND project_id = %s AND upload_id = %s::uuid
                     FOR UPDATE
                    """,
                    (organization_id, project_id, upload_id),
                )
                parent = cursor.fetchone()
                if parent is None:
                    raise KeyError(upload_id)
                version_id = str(cast(Sequence[object], parent)[0])
                cursor.execute(
                    """
                    INSERT INTO registry.robot_model_assets (
                        organization_id, project_id, asset_id, version_id, relative_path, role,
                        media_type, size_bytes, sha256, object_key, content, created_at
                    ) VALUES (%s, %s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (organization_id, version_id, relative_path)
                    DO UPDATE SET
                        project_id = EXCLUDED.project_id,
                        asset_id = EXCLUDED.asset_id,
                        role = EXCLUDED.role,
                        media_type = EXCLUDED.media_type,
                        size_bytes = EXCLUDED.size_bytes,
                        sha256 = EXCLUDED.sha256,
                        object_key = EXCLUDED.object_key,
                        content = EXCLUDED.content,
                        created_at = EXCLUDED.created_at
                    """,
                    (
                        organization_id,
                        project_id,
                        asset.asset_id,
                        version_id,
                        relative_path,
                        asset.role.value,
                        asset.media_type,
                        asset.size_bytes,
                        asset.sha256,
                        str(file_row["object_key"]),
                        content,
                        asset.created_at,
                    ),
                )
                cursor.execute(
                    """
                    UPDATE registry.robot_model_asset_upload_files
                       SET status = 'COMPLETED', completed_etag = %s, completed_at = %s
                     WHERE organization_id = %s AND project_id = %s AND upload_id = %s::uuid
                       AND relative_path = %s
                    """,
                    (
                        completed_etag,
                        completed_at,
                        organization_id,
                        project_id,
                        upload_id,
                        relative_path,
                    ),
                )
                cursor.execute(
                    """
                    UPDATE registry.robot_model_asset_uploads
                       SET updated_at = %s
                     WHERE organization_id = %s AND project_id = %s AND upload_id = %s::uuid
                    """,
                    (completed_at, organization_id, project_id, upload_id),
                )
            result = self._get_asset_upload(cursor, organization_id, project_id, upload_id)
            if result is None:
                raise KeyError(upload_id)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def list_robot_model_assets(
        self, *, organization_id: str, project_id: str, version_id: str
    ) -> tuple[RobotModelAsset, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT asset_id, relative_path, role, media_type, size_bytes, sha256, created_at
                  FROM registry.robot_model_assets
                 WHERE organization_id = %s AND project_id = %s AND version_id = %s
                 ORDER BY relative_path, asset_id
                """,
                (organization_id, project_id, version_id),
            )
            return tuple(_asset(_row(cursor, raw)) for raw in cursor.fetchall())
        finally:
            cursor.close()
            connection.close()

    def get_robot_model_asset(
        self, *, organization_id: str, project_id: str, asset_id: str
    ) -> tuple[str, RobotModelAsset, bytes | None] | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT asset_id, relative_path, role, media_type, size_bytes, sha256, created_at,
                       object_key, content
                  FROM registry.robot_model_assets
                 WHERE organization_id = %s AND project_id = %s AND asset_id = %s::uuid
                """,
                (organization_id, project_id, asset_id),
            )
            raw = cursor.fetchone()
            if raw is None:
                return None
            row = _row(cursor, raw)
            raw_content = row["content"]
            content = None if raw_content is None else bytes(cast(bytes | memoryview, raw_content))
            return str(row["object_key"]), _asset(row), content
        finally:
            cursor.close()
            connection.close()

    def store_robot_model_asset_content(
        self,
        *,
        organization_id: str,
        project_id: str,
        asset_id: str,
        content: bytes,
    ) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE registry.robot_model_assets
                   SET content = %s
                 WHERE organization_id = %s AND project_id = %s AND asset_id = %s::uuid
                   AND size_bytes = octet_length(%s)
                 RETURNING asset_id
                """,
                (content, organization_id, project_id, asset_id, content),
            )
            if cursor.fetchone() is None:
                raise KeyError(asset_id)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def list_robot_model_joint_mappings(
        self, *, organization_id: str, project_id: str, version_id: str
    ) -> tuple[RobotModelJointMapping, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT source_joint_name, target_joint_name, direction
                  FROM registry.robot_model_joint_mappings
                 WHERE organization_id = %s AND project_id = %s AND version_id = %s
                 ORDER BY source_joint_name
                """,
                (organization_id, project_id, version_id),
            )
            return tuple(
                RobotModelJointMapping(
                    source_joint_name=str(row["source_joint_name"]),
                    target_joint_name=str(row["target_joint_name"]),
                    direction=str(row["direction"]),
                )
                for raw in cursor.fetchall()
                for row in (_row(cursor, raw),)
            )
        finally:
            cursor.close()
            connection.close()

    def replace_robot_model_joint_mappings(
        self,
        *,
        organization_id: str,
        project_id: str,
        version_id: str,
        expected_etag: str,
        idempotency_key: str,
        request_fingerprint: str,
        mappings: tuple[RobotModelJointMapping, ...],
        mapping_hash: str,
        updated_at: datetime,
    ) -> RobotModelVersion:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT lifecycle, asset_availability, etag
                  FROM registry.robot_model_versions
                 WHERE organization_id = %s AND version_id = %s
                 FOR UPDATE
                """,
                (organization_id, version_id),
            )
            raw = cursor.fetchone()
            if raw is None:
                raise KeyError(version_id)
            version_row = _row(cursor, raw)
            cursor.execute(
                """
                SELECT request_fingerprint, response
                  FROM registry.robot_model_command_receipts
                 WHERE organization_id = %s AND project_id = %s AND version_id = %s
                   AND operation = 'REPLACE_JOINT_MAPPINGS' AND idempotency_key = %s
                 FOR UPDATE
                """,
                (organization_id, project_id, version_id, idempotency_key),
            )
            raw_receipt = cursor.fetchone()
            if raw_receipt is not None:
                receipt = _row(cursor, raw_receipt)
                if str(receipt["request_fingerprint"]) != request_fingerprint:
                    raise ValueError("joint mapping idempotency key was reused")
                response = receipt["response"]
                decoded = json.loads(response) if isinstance(response, str) else response
                if not isinstance(decoded, Mapping):
                    raise RuntimeError("joint mapping command receipt is malformed")
                connection.commit()
                return RobotModelVersion.model_validate(decoded)
            if str(version_row["etag"]) != expected_etag:
                raise ValueError("robot model version etag does not match")
            if str(version_row["lifecycle"]) != "DRAFT":
                raise ValueError("robot model version is immutable")
            cursor.execute(
                """
                DELETE FROM registry.robot_model_joint_mappings
                 WHERE organization_id = %s AND project_id = %s AND version_id = %s
                """,
                (organization_id, project_id, version_id),
            )
            for mapping in mappings:
                cursor.execute(
                    """
                    INSERT INTO registry.robot_model_joint_mappings (
                        organization_id, project_id, version_id, source_joint_name,
                        target_joint_name, direction, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        organization_id,
                        project_id,
                        version_id,
                        mapping.source_joint_name,
                        mapping.target_joint_name,
                        mapping.direction.value,
                        updated_at,
                    ),
                )
            readiness = (
                "SAMPLE_VALIDATION_REQUIRED"
                if str(version_row["asset_availability"]) == "AVAILABLE"
                else "CONFIGURATION_REQUIRED"
            )
            cursor.execute(
                """
                UPDATE registry.robot_model_versions
                   SET validation_input_hash = %s, publish_readiness = %s,
                       revision = revision + 1,
                       etag = '"registry:' || version_id || ':' || (revision + 1)::text || '"',
                       updated_at = %s
                 WHERE organization_id = %s AND version_id = %s
                """,
                (mapping_hash, readiness, updated_at, organization_id, version_id),
            )
            cursor.execute(
                """
                SELECT version_id, robot_model_id, version_label, lifecycle, asset_availability,
                       publish_readiness, asset_manifest_hash, validation_input_hash, etag,
                       allowed_actions, blocked_reasons
                  FROM registry.robot_model_versions
                 WHERE organization_id = %s AND version_id = %s
                """,
                (organization_id, version_id),
            )
            result = cursor.fetchone()
            if result is None:
                raise KeyError(version_id)
            updated = _version(_row(cursor, result))
            cursor.execute(
                """
                INSERT INTO registry.robot_model_command_receipts (
                    organization_id, project_id, version_id, operation, idempotency_key,
                    request_fingerprint, response, created_at
                ) VALUES (%s, %s, %s, 'REPLACE_JOINT_MAPPINGS', %s, %s, %s::jsonb, %s)
                """,
                (
                    organization_id,
                    project_id,
                    version_id,
                    idempotency_key,
                    request_fingerprint,
                    json.dumps(updated.model_dump(mode="json")),
                    updated_at,
                ),
            )
            connection.commit()
            return updated
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def find_publish_preflight_by_idempotency(
        self,
        *,
        organization_id: str,
        project_id: str,
        version_id: str,
        idempotency_key: str,
    ) -> RobotModelPublishPreflightRecord | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT organization_id, project_id, preflight_id, version_id, idempotency_key,
                       request_fingerprint, expected_etag, asset_manifest_hash, mapping_hash,
                       allowed, status, token_hash, checks, expires_at, created_at, consumed_at
                  FROM registry.robot_model_publish_preflights
                 WHERE organization_id = %s AND project_id = %s AND version_id = %s
                   AND idempotency_key = %s
                """,
                (organization_id, project_id, version_id, idempotency_key),
            )
            raw = cursor.fetchone()
            return None if raw is None else _publish_preflight(_row(cursor, raw))
        finally:
            cursor.close()
            connection.close()

    def create_publish_preflight(self, record: RobotModelPublishPreflightRecord) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO registry.robot_model_publish_preflights (
                    organization_id, project_id, preflight_id, version_id, idempotency_key,
                    request_fingerprint, expected_etag, asset_manifest_hash, mapping_hash,
                    allowed, status, token_hash, checks, expires_at, created_at, consumed_at
                ) VALUES (
                    %s, %s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s
                )
                """,
                (
                    record.organization_id,
                    record.project_id,
                    record.preflight_id,
                    record.version_id,
                    record.idempotency_key,
                    record.request_fingerprint,
                    record.expected_etag,
                    record.asset_manifest_hash,
                    record.mapping_hash,
                    record.allowed,
                    record.status,
                    record.token_hash,
                    json.dumps([item.model_dump(mode="json") for item in record.checks]),
                    record.expires_at,
                    record.created_at,
                    record.consumed_at,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def publish_robot_model_version(
        self,
        *,
        organization_id: str,
        project_id: str,
        version_id: str,
        preflight_id: str,
        token_hash: str,
        idempotency_key: str,
        expected_etag: str,
        occurred_at: datetime,
    ) -> RobotModelVersion:
        """Consume one validated preflight and atomically publish its draft.

        The version, preflight, mapping rows, and model projection all share the
        same transaction.  A consumed token deliberately returns the published
        version for a retry with the original command identity.
        """

        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT organization_id, project_id, preflight_id, version_id, idempotency_key,
                       request_fingerprint, expected_etag, asset_manifest_hash, mapping_hash,
                       allowed, status, token_hash, checks, expires_at, created_at, consumed_at
                  FROM registry.robot_model_publish_preflights
                 WHERE organization_id = %s AND project_id = %s AND preflight_id = %s::uuid
                 FOR UPDATE
                """,
                (organization_id, project_id, preflight_id),
            )
            raw_preflight = cursor.fetchone()
            if raw_preflight is None:
                raise KeyError(preflight_id)
            preflight = _publish_preflight(_row(cursor, raw_preflight))
            if preflight.version_id != version_id:
                raise PermissionError("publish preflight version does not match")
            if preflight.token_hash != token_hash:
                raise PermissionError("publish preflight token does not match")
            if preflight.idempotency_key != idempotency_key:
                raise ValueError("publish idempotency key does not match preflight")

            cursor.execute(
                """
                SELECT version_id, robot_model_id, version_label, lifecycle, asset_availability,
                       publish_readiness, asset_manifest_hash, validation_input_hash, etag,
                       allowed_actions, blocked_reasons
                  FROM registry.robot_model_versions
                 WHERE organization_id = %s AND version_id = %s
                 FOR UPDATE
                """,
                (organization_id, version_id),
            )
            raw_version = cursor.fetchone()
            if raw_version is None:
                raise KeyError(version_id)
            version = _version(_row(cursor, raw_version))
            if preflight.status == "CONSUMED":
                connection.commit()
                return version
            if preflight.status != "ISSUED" or preflight.expires_at <= occurred_at:
                raise TimeoutError("publish preflight has expired")
            if preflight.expected_etag != expected_etag or version.etag != expected_etag:
                raise ValueError("robot model version etag does not match")
            if not preflight.allowed or version.lifecycle != "DRAFT":
                raise ValueError("robot model version cannot be published")
            if version.asset_manifest_hash != preflight.asset_manifest_hash:
                raise ValueError("robot model asset manifest changed")
            cursor.execute(
                """
                SELECT source_joint_name, target_joint_name, direction
                  FROM registry.robot_model_joint_mappings
                 WHERE organization_id = %s AND project_id = %s AND version_id = %s
                 ORDER BY source_joint_name
                """,
                (organization_id, project_id, version_id),
            )
            mappings = tuple(
                RobotModelJointMapping(
                    source_joint_name=str(row["source_joint_name"]),
                    target_joint_name=str(row["target_joint_name"]),
                    direction=str(row["direction"]),
                )
                for raw in cursor.fetchall()
                for row in (_row(cursor, raw),)
            )
            if _joint_mapping_hash(mappings) != preflight.mapping_hash:
                raise ValueError("robot model joint mappings changed")
            cursor.execute(
                """
                UPDATE registry.robot_model_versions
                   SET lifecycle = 'PUBLISHED', publish_readiness = 'READY',
                       revision = revision + 1,
                       etag = '"registry:' || version_id || ':' || (revision + 1)::text || '"',
                       updated_at = %s
                 WHERE organization_id = %s AND version_id = %s
                """,
                (occurred_at, organization_id, version_id),
            )
            cursor.execute(
                """
                UPDATE registry.robot_models
                   SET current_published_version_id = %s
                 WHERE organization_id = %s AND model_id = %s
                """,
                (version_id, organization_id, version.robot_model_id),
            )
            cursor.execute(
                """
                UPDATE registry.robot_model_publish_preflights
                   SET status = 'CONSUMED', consumed_at = %s
                 WHERE organization_id = %s AND project_id = %s AND preflight_id = %s::uuid
                """,
                (occurred_at, organization_id, project_id, preflight_id),
            )
            cursor.execute(
                """
                SELECT version_id, robot_model_id, version_label, lifecycle, asset_availability,
                       publish_readiness, asset_manifest_hash, validation_input_hash, etag,
                       allowed_actions, blocked_reasons
                  FROM registry.robot_model_versions
                 WHERE organization_id = %s AND version_id = %s
                """,
                (organization_id, version_id),
            )
            updated = cursor.fetchone()
            if updated is None:
                raise KeyError(version_id)
            connection.commit()
            return _version(_row(cursor, updated))
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def finalize_asset_upload(
        self,
        *,
        organization_id: str,
        project_id: str,
        upload_id: str,
        asset_manifest_hash: str,
        updated_at: datetime,
    ) -> RobotModelAssetUploadRecord:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT version_id
                  FROM registry.robot_model_asset_uploads
                 WHERE organization_id = %s AND project_id = %s AND upload_id = %s::uuid
                 FOR UPDATE
                """,
                (organization_id, project_id, upload_id),
            )
            raw = cursor.fetchone()
            if raw is None:
                raise KeyError(upload_id)
            version_id = str(cast(Sequence[object], raw)[0])
            cursor.execute(
                """
                SELECT count(*)
                  FROM registry.robot_model_asset_upload_files
                 WHERE organization_id = %s AND project_id = %s AND upload_id = %s::uuid
                   AND status <> 'COMPLETED'
                """,
                (organization_id, project_id, upload_id),
            )
            incomplete = cursor.fetchone()
            incomplete_count = (
                None
                if incomplete is None
                else int(cast(int | str, cast(Sequence[object], incomplete)[0]))
            )
            if incomplete_count != 0:
                raise ValueError("asset upload is not complete")
            cursor.execute(
                """
                UPDATE registry.robot_model_asset_uploads
                   SET status = 'COMPLETED', updated_at = %s, completed_at = %s
                 WHERE organization_id = %s AND project_id = %s AND upload_id = %s::uuid
                """,
                (updated_at, updated_at, organization_id, project_id, upload_id),
            )
            cursor.execute(
                """
                UPDATE registry.robot_model_versions
                   SET asset_availability = 'AVAILABLE', asset_manifest_hash = %s,
                       revision = revision + 1,
                       etag = '"registry:' || version_id || ':' || (revision + 1)::text || '"',
                       updated_at = %s
                 WHERE organization_id = %s AND version_id = %s AND lifecycle = 'DRAFT'
                 RETURNING version_id
                """,
                (asset_manifest_hash, updated_at, organization_id, version_id),
            )
            if cursor.fetchone() is None:
                raise ValueError("robot model version cannot accept asset changes")
            result = self._get_asset_upload(cursor, organization_id, project_id, upload_id)
            if result is None:
                raise KeyError(upload_id)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def discard_robot_model_import(
        self,
        *,
        organization_id: str,
        project_id: str,
        version_id: str,
        fallback_version_id: str | None,
    ) -> RobotModelImportCleanup | None:
        """Delete an unbound import version and return its owned storage objects."""

        del project_id
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT version.robot_model_id, version.lifecycle,
                       model.current_published_version_id
                  FROM registry.robot_model_versions AS version
                  JOIN registry.robot_models AS model
                    ON model.organization_id = version.organization_id
                   AND model.model_id = version.robot_model_id
                 WHERE version.organization_id = %s AND version.version_id = %s
                 FOR UPDATE OF version, model
                """,
                (organization_id, version_id),
            )
            raw_version = cursor.fetchone()
            if raw_version is None:
                connection.commit()
                return None
            version_row = _row(cursor, raw_version)
            model_id = str(version_row["robot_model_id"])
            lifecycle = str(version_row["lifecycle"])
            current_published_version_id = (
                None
                if version_row["current_published_version_id"] is None
                else str(version_row["current_published_version_id"])
            )
            if lifecycle not in {"DRAFT", "PUBLISHED"}:
                raise ValueError("robot model import is in use")

            if fallback_version_id is not None:
                if fallback_version_id == version_id:
                    raise ValueError("robot model fallback version is invalid")
                cursor.execute(
                    """
                    SELECT 1
                      FROM registry.robot_model_versions
                     WHERE organization_id = %s AND version_id = %s
                       AND robot_model_id = %s AND lifecycle = 'PUBLISHED'
                    """,
                    (organization_id, fallback_version_id, model_id),
                )
                if cursor.fetchone() is None:
                    raise ValueError("robot model fallback version is invalid")

            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                      FROM registry.organization_robot_model_bindings
                     WHERE organization_id = %s AND version_id = %s
                    UNION ALL
                    SELECT 1
                      FROM robotics.robot_assets
                     WHERE organization_id = %s AND robot_model_version_id = %s
                ) AS in_use
                """,
                (organization_id, version_id, organization_id, version_id),
            )
            raw_usage = cursor.fetchone()
            if raw_usage is None or bool(_row(cursor, raw_usage)["in_use"]):
                raise ValueError("robot model import is in use")

            cursor.execute(
                """
                SELECT file.relative_path, file.role, file.media_type,
                       file.expected_size, file.expected_sha256, file.object_key,
                       file.multipart_upload_id, file.status,
                       file.completed_etag, file.completed_at
                  FROM registry.robot_model_asset_upload_files AS file
                  JOIN registry.robot_model_asset_uploads AS upload
                    ON upload.organization_id = file.organization_id
                   AND upload.upload_id = file.upload_id
                 WHERE upload.organization_id = %s AND upload.version_id = %s
                 ORDER BY file.upload_id, file.relative_path
                """,
                (organization_id, version_id),
            )
            cleanup = RobotModelImportCleanup(
                files=tuple(_asset_upload_file(_row(cursor, raw)) for raw in cursor.fetchall())
            )

            if current_published_version_id == version_id:
                cursor.execute(
                    """
                    UPDATE registry.robot_models
                       SET current_published_version_id = %s, updated_at = now()
                     WHERE organization_id = %s AND model_id = %s
                    """,
                    (fallback_version_id, organization_id, model_id),
                )
            cursor.execute(
                """
                DELETE FROM registry.robot_model_publish_preflights
                 WHERE organization_id = %s AND version_id = %s
                """,
                (organization_id, version_id),
            )
            cursor.execute(
                """
                DELETE FROM registry.robot_model_command_receipts
                 WHERE organization_id = %s
                   AND (version_id = %s OR response ->> 'id' = %s)
                """,
                (organization_id, version_id, version_id),
            )
            cursor.execute(
                """
                DELETE FROM registry.robot_model_joint_mappings
                 WHERE organization_id = %s AND version_id = %s
                """,
                (organization_id, version_id),
            )
            cursor.execute(
                """
                DELETE FROM registry.robot_model_assets
                 WHERE organization_id = %s AND version_id = %s
                """,
                (organization_id, version_id),
            )
            cursor.execute(
                """
                DELETE FROM registry.robot_model_asset_uploads
                 WHERE organization_id = %s AND version_id = %s
                """,
                (organization_id, version_id),
            )
            cursor.execute(
                """
                DELETE FROM registry.robot_model_versions
                 WHERE organization_id = %s AND version_id = %s
                """,
                (organization_id, version_id),
            )
            cursor.execute(
                """
                DELETE FROM registry.robot_models AS model
                 WHERE model.organization_id = %s AND model.model_id = %s
                   AND NOT EXISTS (
                       SELECT 1
                         FROM registry.robot_model_versions AS version
                        WHERE version.organization_id = model.organization_id
                          AND version.robot_model_id = model.model_id
                   )
                """,
                (organization_id, model_id),
            )
            connection.commit()
            return cleanup
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    @staticmethod
    def _get_asset_upload(
        cursor: DbApiCursor,
        organization_id: str,
        project_id: str,
        upload_id: str,
    ) -> RobotModelAssetUploadRecord | None:
        cursor.execute(
            """
            SELECT upload_id, version_id, idempotency_key, request_fingerprint, status,
                   created_at, updated_at, completed_at
              FROM registry.robot_model_asset_uploads
             WHERE organization_id = %s AND project_id = %s AND upload_id = %s::uuid
            """,
            (organization_id, project_id, upload_id),
        )
        raw = cursor.fetchone()
        if raw is None:
            return None
        parent = _row(cursor, raw)
        cursor.execute(
            """
            SELECT relative_path, role, media_type, expected_size, expected_sha256, object_key,
                   multipart_upload_id, status, completed_etag, completed_at
              FROM registry.robot_model_asset_upload_files
             WHERE organization_id = %s AND project_id = %s AND upload_id = %s::uuid
             ORDER BY relative_path
            """,
            (organization_id, project_id, upload_id),
        )
        return RobotModelAssetUploadRecord(
            organization_id=organization_id,
            project_id=project_id,
            upload_id=str(parent["upload_id"]),
            version_id=str(parent["version_id"]),
            idempotency_key=str(parent["idempotency_key"]),
            request_fingerprint=str(parent["request_fingerprint"]),
            status=RobotAssetUploadStatus(str(parent["status"])),
            files=tuple(_asset_upload_file(_row(cursor, item)) for item in cursor.fetchall()),
            created_at=cast(datetime, parent["created_at"]),
            updated_at=cast(datetime, parent["updated_at"]),
            completed_at=cast(datetime | None, parent["completed_at"]),
        )

    def append_audit(self, event: RegistryAuditEvent) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO registry.audit_events (
                    organization_id, project_id, actor_id, action, resource_type, resource_id,
                    request_id, outcome, occurred_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    event.organization_id,
                    event.project_id,
                    event.actor_id,
                    event.action,
                    event.resource_type,
                    event.resource_id,
                    event.request_id,
                    event.outcome,
                    event.occurred_at,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()
