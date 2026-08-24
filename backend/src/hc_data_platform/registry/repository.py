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
    RobotModelBinding,
    RobotModelBindingStatus,
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


@dataclass(frozen=True, slots=True)
class RobotModelBindingRecord:
    organization_id: str
    project_id: str
    region_code: str
    binding_id: str
    robot_id: str
    version_id: str
    idempotency_key: str
    request_fingerprint: str
    expected_robot_etag: str
    status: RobotModelBindingStatus
    bound_at: datetime
    unbound_at: datetime | None

    def public(self) -> RobotModelBinding:
        return RobotModelBinding(
            binding_id=self.binding_id,
            robot_id=self.robot_id,
            region_code=self.region_code,
            version_id=self.version_id,
            status=self.status,
            bound_at=self.bound_at,
            unbound_at=self.unbound_at,
        )


class RegistryRepository(Protocol):
    def has_organization_project(self, *, organization_id: str, project_id: str) -> bool: ...

    def list_robot_models(
        self, *, organization_id: str, project_id: str, query: str | None
    ) -> tuple[RobotModelSummary, ...]: ...

    def get_robot_model_version(
        self, *, organization_id: str, project_id: str, version_id: str
    ) -> RobotModelVersion | None: ...

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
    ) -> RobotModelAssetUploadRecord: ...

    def list_robot_model_assets(
        self, *, organization_id: str, project_id: str, version_id: str
    ) -> tuple[RobotModelAsset, ...]: ...

    def get_robot_model_asset(
        self, *, organization_id: str, project_id: str, asset_id: str
    ) -> tuple[str, RobotModelAsset] | None: ...

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

    def list_robot_model_bindings(
        self, *, organization_id: str, project_id: str, version_id: str
    ) -> tuple[RobotModelBinding, ...]: ...

    def bind_robot_model_version(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        version_id: str,
        robot_id: str,
        expected_robot_etag: str,
        idempotency_key: str,
        request_fingerprint: str,
        bound_at: datetime,
    ) -> RobotModelBinding: ...

    def append_audit(self, event: RegistryAuditEvent) -> None: ...


class InMemoryRegistryRepository:
    """Thread-safe reference repository; production composition uses PostgreSQL."""

    def __init__(
        self,
        *,
        models: tuple[tuple[str, RobotModelSummary], ...] = (),
        versions: tuple[tuple[str, RobotModelVersion], ...] = (),
        organization_projects: tuple[tuple[str, str], ...] = (),
        robot_etags: tuple[tuple[str, str, str, str], ...] = (),
    ) -> None:
        self._models = list(models)
        self._versions = {(organization_id, value.id): value for organization_id, value in versions}
        self._organization_projects = frozenset(organization_projects)
        self._asset_uploads: dict[tuple[str, str, str], RobotModelAssetUploadRecord] = {}
        self._assets: dict[tuple[str, str, str], tuple[str, str, RobotModelAsset]] = {}
        self._joint_mappings: dict[tuple[str, str, str], tuple[RobotModelJointMapping, ...]] = {}
        self._command_receipts: dict[
            tuple[str, str, str, str, str], tuple[str, RobotModelVersion]
        ] = {}
        self._publish_preflights: dict[tuple[str, str, str], RobotModelPublishPreflightRecord] = {}
        self._bindings: dict[tuple[str, str, str, str], RobotModelBindingRecord] = {}
        self._robot_etags = {
            (project_id, region_code, robot_id): etag
            for project_id, region_code, robot_id, etag in robot_etags
        }
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
            self._assets.setdefault(
                (organization_id, project_id, asset.asset_id),
                (
                    files[[item.relative_path for item in files].index(relative_path)].object_key,
                    record.version_id,
                    asset,
                ),
            )
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
    ) -> tuple[str, RobotModelAsset] | None:
        with self._lock:
            found = self._assets.get((organization_id, project_id, asset_id))
            return None if found is None else (found[0], found[2])

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
            self._publish_preflights[key] = dataclass_replace(
                preflight, status="CONSUMED", consumed_at=occurred_at
            )
            return updated

    def list_robot_model_bindings(
        self, *, organization_id: str, project_id: str, version_id: str
    ) -> tuple[RobotModelBinding, ...]:
        with self._lock:
            values = [
                item.public()
                for item in self._bindings.values()
                if item.organization_id == organization_id
                and item.project_id == project_id
                and item.version_id == version_id
            ]
        return tuple(
            sorted(values, key=lambda item: (item.bound_at, item.binding_id), reverse=True)
        )

    def bind_robot_model_version(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        version_id: str,
        robot_id: str,
        expected_robot_etag: str,
        idempotency_key: str,
        request_fingerprint: str,
        bound_at: datetime,
    ) -> RobotModelBinding:
        with self._lock:
            existing = next(
                (
                    item
                    for item in self._bindings.values()
                    if item.organization_id == organization_id
                    and item.project_id == project_id
                    and item.region_code == region_code
                    and item.version_id == version_id
                    and item.idempotency_key == idempotency_key
                ),
                None,
            )
            if existing is not None:
                if existing.request_fingerprint != request_fingerprint:
                    raise ValueError("robot model binding idempotency key was reused")
                return existing.public()
            version = self._versions.get((organization_id, version_id))
            if version is None:
                raise KeyError(version_id)
            if version.lifecycle != "PUBLISHED":
                raise ValueError("robot model version is not published")
            robot_key = (project_id, region_code, robot_id)
            current_robot_etag = self._robot_etags.get(robot_key)
            if current_robot_etag is None:
                raise KeyError(robot_id)
            if current_robot_etag != expected_robot_etag:
                raise ValueError("robot etag does not match")
            for key, item in tuple(self._bindings.items()):
                if (
                    item.organization_id == organization_id
                    and item.project_id == project_id
                    and item.region_code == region_code
                    and item.robot_id == robot_id
                    and item.status is RobotModelBindingStatus.ACTIVE
                ):
                    self._bindings[key] = dataclass_replace(
                        item,
                        status=RobotModelBindingStatus.SUPERSEDED,
                        unbound_at=bound_at,
                    )
            binding_id = str(uuid4())
            record = RobotModelBindingRecord(
                organization_id=organization_id,
                project_id=project_id,
                region_code=region_code,
                binding_id=binding_id,
                robot_id=robot_id,
                version_id=version_id,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                expected_robot_etag=expected_robot_etag,
                status=RobotModelBindingStatus.ACTIVE,
                bound_at=bound_at,
                unbound_at=None,
            )
            self._bindings[(organization_id, project_id, region_code, binding_id)] = record
            self._robot_etags[robot_key] = f'"robot:{robot_id}:binding:{binding_id}"'
            return record.public()

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


def _binding(row: Mapping[str, object]) -> RobotModelBinding:
    return RobotModelBinding(
        binding_id=str(row["binding_id"]),
        robot_id=str(row["robot_id"]),
        region_code=str(row["region_code"]),
        version_id=str(row["version_id"]),
        status=RobotModelBindingStatus(str(row["status"])),
        bound_at=cast(datetime, row["bound_at"]),
        unbound_at=cast(datetime | None, row["unbound_at"]),
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

    def list_robot_models(
        self, *, organization_id: str, project_id: str, query: str | None
    ) -> tuple[RobotModelSummary, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT model_id, manufacturer, model_code, display_name,
                       current_published_version_id
                FROM registry.robot_models model
                JOIN registry.organization_projects membership
                  ON membership.organization_id = model.organization_id
                 WHERE model.organization_id = %s
                   AND membership.project_id = %s
                   AND (
                       %s::text IS NULL
                       OR display_name ILIKE '%%' || %s || '%%'
                       OR model_code ILIKE '%%' || %s || '%%'
                       OR manufacturer ILIKE '%%' || %s || '%%'
                   )
                 ORDER BY lower(display_name), model_id
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
                        media_type, size_bytes, sha256, object_key, created_at
                    ) VALUES (%s, %s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (organization_id, project_id, version_id, relative_path)
                    DO NOTHING
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
    ) -> tuple[str, RobotModelAsset] | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT asset_id, relative_path, role, media_type, size_bytes, sha256, created_at,
                       object_key
                  FROM registry.robot_model_assets
                 WHERE organization_id = %s AND project_id = %s AND asset_id = %s::uuid
                """,
                (organization_id, project_id, asset_id),
            )
            raw = cursor.fetchone()
            if raw is None:
                return None
            row = _row(cursor, raw)
            return str(row["object_key"]), _asset(row)
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

    def list_robot_model_bindings(
        self, *, organization_id: str, project_id: str, version_id: str
    ) -> tuple[RobotModelBinding, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT binding_id, robot_id, region_code, version_id, status, bound_at, unbound_at
                  FROM registry.robot_model_bindings
                 WHERE organization_id = %s AND project_id = %s AND version_id = %s
                 ORDER BY bound_at DESC, binding_id DESC
                """,
                (organization_id, project_id, version_id),
            )
            return tuple(_binding(_row(cursor, raw)) for raw in cursor.fetchall())
        finally:
            cursor.close()
            connection.close()

    def bind_robot_model_version(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        version_id: str,
        robot_id: str,
        expected_robot_etag: str,
        idempotency_key: str,
        request_fingerprint: str,
        bound_at: datetime,
    ) -> RobotModelBinding:
        """Atomically write binding history and the P15 effective-binding projection."""

        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            # This serializes an idempotency key even if two callers select
            # different robot rows. It avoids letting a unique violation leak as
            # a transient 500 after one of those rows has been locked.
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (
                    "|".join(
                        (
                            organization_id,
                            project_id,
                            region_code,
                            version_id,
                            idempotency_key,
                        )
                    ),
                ),
            )
            cursor.execute(
                """
                SELECT binding_id, robot_id, region_code, version_id, status, bound_at, unbound_at,
                       request_fingerprint
                  FROM registry.robot_model_bindings
                 WHERE organization_id = %s AND project_id = %s AND region_code = %s
                   AND version_id = %s AND idempotency_key = %s
                 FOR UPDATE
                """,
                (organization_id, project_id, region_code, version_id, idempotency_key),
            )
            prior = cursor.fetchone()
            if prior is not None:
                row = _row(cursor, prior)
                if str(row["request_fingerprint"]) != request_fingerprint:
                    raise ValueError("robot model binding idempotency key was reused")
                connection.commit()
                return _binding(row)

            cursor.execute(
                """
                SELECT lifecycle
                  FROM registry.robot_model_versions
                 WHERE organization_id = %s AND version_id = %s
                 FOR SHARE
                """,
                (organization_id, version_id),
            )
            raw_version = cursor.fetchone()
            if raw_version is None:
                raise KeyError(version_id)
            if str(_row(cursor, raw_version)["lifecycle"]) != "PUBLISHED":
                raise ValueError("robot model version is not published")
            cursor.execute(
                """
                SELECT etag
                  FROM robotics.robot_instances
                 WHERE project_id = %s AND region_code = %s AND robot_id = %s
                 FOR UPDATE
                """,
                (project_id, region_code, robot_id),
            )
            raw_robot = cursor.fetchone()
            if raw_robot is None:
                raise KeyError(robot_id)
            if str(_row(cursor, raw_robot)["etag"]) != expected_robot_etag:
                raise ValueError("robot etag does not match")
            binding_id = str(uuid4())
            binding_etag = f'"registry-binding:{binding_id}"'
            robot_etag = f'"robot:{robot_id}:binding:{binding_id}"'
            cursor.execute(
                """
                UPDATE registry.robot_model_bindings
                   SET status = 'SUPERSEDED', unbound_at = %s
                 WHERE organization_id = %s AND project_id = %s AND region_code = %s
                   AND robot_id = %s AND status = 'ACTIVE'
                """,
                (bound_at, organization_id, project_id, region_code, robot_id),
            )
            cursor.execute(
                """
                INSERT INTO registry.robot_model_bindings (
                    organization_id, project_id, region_code, binding_id, robot_id, version_id,
                    idempotency_key, request_fingerprint, expected_robot_etag, status,
                    bound_at, unbound_at
                ) VALUES (%s, %s, %s, %s::uuid, %s, %s, %s, %s, %s, 'ACTIVE', %s, NULL)
                """,
                (
                    organization_id,
                    project_id,
                    region_code,
                    binding_id,
                    robot_id,
                    version_id,
                    idempotency_key,
                    request_fingerprint,
                    expected_robot_etag,
                    bound_at,
                ),
            )
            cursor.execute(
                """
                UPDATE robotics.robot_instances
                   SET binding_id = %s, binding_scope_type = 'ROBOT', binding_scope_id = %s,
                       robot_model_version_id = %s, binding_valid_from = %s,
                       binding_valid_to = NULL, binding_etag = %s, etag = %s,
                       topology_revision = topology_revision || ':binding:' || %s
                 WHERE project_id = %s AND region_code = %s AND robot_id = %s
                """,
                (
                    binding_id,
                    robot_id,
                    version_id,
                    bound_at,
                    binding_etag,
                    robot_etag,
                    binding_id,
                    project_id,
                    region_code,
                    robot_id,
                ),
            )
            connection.commit()
            return RobotModelBinding(
                binding_id=binding_id,
                robot_id=robot_id,
                region_code=region_code,
                version_id=version_id,
                status=RobotModelBindingStatus.ACTIVE,
                bound_at=bound_at,
                unbound_at=None,
            )
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
