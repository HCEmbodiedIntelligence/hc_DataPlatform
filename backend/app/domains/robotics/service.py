"""Shared domain services and safe projections for P14-P17."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    ForbiddenError,
    GoneError,
    NotFoundError,
    PreconditionFailedError,
    VersionConflictError,
)
from app.core.etag import compute_etag
from app.core.ids import new_id

from . import models
from .repository import RoboticsRepository

CONTRACT_VERSION = "robotics-calibration-schema.v1alpha1"


def utc_now() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def canonical_hash(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def increment(value: str) -> str:
    try:
        return str(int(value) + 1)
    except ValueError:
        return new_id("event")


def scope_key(*parts: str) -> str:
    return ":".join(parts)


def robot_model_projection(row: models.RobotModel) -> dict[str, Any]:
    return {
        "id": row.model_id,
        "manufacturer": row.manufacturer,
        "model_code": row.model_code,
        "display_name": row.display_name,
        "current_published_version_id": row.current_published_version_id,
        "status": row.status,
        "etag": row.etag,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "allowed_actions": ["CREATE_VERSION"],
        "blocked_reasons": [],
    }


def model_version_projection(row: models.RobotModelVersion) -> dict[str, Any]:
    return {
        "id": row.version_id,
        "robot_model_id": row.model_id,
        "version_label": row.version_label,
        "lifecycle": row.lifecycle,
        "upload_status": row.upload_status,
        "validation_status": row.validation_status,
        "asset_availability": row.asset_availability,
        "publish_readiness": row.publish_readiness,
        "asset_manifest_hash": row.manifest_hash,
        "validation_input_hash": row.validation_input_hash,
        "etag": row.etag,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "allowed_actions": ["UPLOAD", "CONFIGURE", "MAP", "VALIDATE", "PUBLISH"]
        if row.lifecycle == "DRAFT"
        else (["DISABLE"] if row.lifecycle == "PUBLISHED" else []),
        "blocked_reasons": [],
    }


def upload_projection(row: models.RobotAssetUploadSession) -> dict[str, Any]:
    return {
        "id": row.upload_session_id,
        "robot_model_version_id": row.version_id,
        "status": row.status,
        "objects": [
            {key: value for key, value in item.items() if key != "provider_object_id"}
            for item in row.objects
        ],
        "expires_at": row.expires_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def binding_projection(row: models.RobotModelBinding) -> dict[str, Any]:
    return {
        "id": row.binding_id,
        "project_id": row.project_id,
        "region_code": row.region_code,
        "scope_type": row.scope_type,
        "scope_id": row.scope_id,
        "robot_model_version_id": row.robot_model_version_id,
        "status": row.status,
        "valid_from": row.valid_from,
        "valid_to": row.valid_to,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "reason": row.reason,
        "etag": row.etag,
    }


def robot_projection(row: models.Robot) -> dict[str, Any]:
    return {
        "id": row.robot_id,
        "display_name": row.display_name,
        "serial_no": row.serial_no,
        "robot_model_id": row.robot_model_id,
        "lifecycle": row.lifecycle,
        "connectivity": row.connectivity,
        "topology_revision": row.topology_revision,
        "etag": row.etag,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "allowed_actions": ["UPDATE", "ADD_COMPONENT"],
        "blocked_reasons": [],
    }


def component_projection(row: models.Component) -> dict[str, Any]:
    return {
        "id": row.component_id,
        "robot_id": row.robot_id,
        "parent_component_id": row.parent_component_id,
        "component_type": row.component_type,
        "display_name": row.display_name,
        "serial_no": row.serial_no,
        "lifecycle": row.lifecycle,
        "connectivity": row.connectivity,
        "sort_key": row.sort_key,
        "etag": row.etag,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "allowed_actions": ["UPDATE", "CHANGE_MOUNT", "DISABLE"]
        if row.lifecycle not in {"DISABLED", "RETIRED"}
        else [],
        "blocked_reasons": [],
    }


def calibration_set_projection(row: models.CalibrationSet) -> dict[str, Any]:
    return {
        "id": row.set_id,
        "robot_id": row.robot_id,
        "component_id": row.component_id,
        "display_name": row.display_name,
        "valid_from": row.valid_from,
        "valid_to": row.valid_to,
        "lifecycle": row.lifecycle,
        "etag": row.etag,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "allowed_actions": ["UPDATE", "CREATE_VERSION", "PREVIEW"],
        "blocked_reasons": [],
    }


def calibration_version_projection(row: models.CalibrationVersion) -> dict[str, Any]:
    return {
        "set_id": row.set_id,
        "version": row.version,
        "lifecycle": row.lifecycle,
        "content_hash": row.content_hash,
        "validation_context_hash": row.validation_context_hash,
        "definition": row.definition,
        "availability": row.availability,
        "etag": row.etag,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "allowed_actions": ["UPDATE", "VALIDATE", "PUBLISH"] if row.lifecycle == "DRAFT" else [],
        "blocked_reasons": [],
    }


def schema_projection(row: models.DataSchema) -> dict[str, Any]:
    return {
        "id": row.schema_id,
        "name": row.name,
        "family_id": row.family_id,
        "description": row.description,
        "compatibility_mode": row.compatibility_mode,
        "logical_type": row.logical_type,
        "current_published_version": row.current_published_version,
        "status": row.status,
        "etag": row.etag,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "allowed_actions": ["CREATE_VERSION"],
        "blocked_reasons": [],
    }


def schema_version_projection(row: models.DataSchemaVersion) -> dict[str, Any]:
    return {
        "id": row.row_id,
        "schema_id": row.schema_id,
        "version": row.version,
        "parent_version_id": row.parent_version_id,
        "lifecycle": row.lifecycle,
        "definition_hash": row.definition_hash,
        "definition": row.definition,
        "validation_status": row.validation_status,
        "compatibility": row.compatibility,
        "etag": row.etag,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "allowed_actions": ["UPDATE", "VALIDATE", "PUBLISH"] if row.lifecycle == "DRAFT" else [],
        "blocked_reasons": [],
    }


def job_projection(row: models.RoboticsJob) -> dict[str, Any]:
    return {
        "job_id": row.job_id,
        "job_type": row.job_type,
        "status": row.status,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "progress": {"completed": "1", "total": "1"} if row.status == "SUCCEEDED" else None,
        "result_ref": row.result_ref,
        "error": row.error,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class RoboticsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = RoboticsRepository(session)

    async def create_job(
        self, scope: str, job_type: str, resource_type: str, resource_id: str
    ) -> models.RoboticsJob:
        now = utc_now()
        row = models.RoboticsJob(
            job_id=new_id("job"),
            scope_key=scope,
            job_type=job_type,
            status="SUCCEEDED",
            resource_type=resource_type,
            resource_id=resource_id,
            result_ref={"type": resource_type, "id": resource_id},
            error=None,
            created_at=now,
            updated_at=now,
        )
        self.repo.add(row)
        await self.session.flush()
        return row

    async def create_preflight(
        self,
        operation_id: str,
        scope: str,
        actor_id: str,
        target_id: str,
        expected_etag: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        now = utc_now()
        preflight_id = new_id("event")
        secret = secrets.token_urlsafe(32)
        token = f"{preflight_id}.{secret}"
        row = models.RoboticsPreflight(
            preflight_id=preflight_id,
            operation_id=operation_id,
            scope_key=scope,
            actor_id=actor_id,
            target_id=target_id,
            expected_etag=expected_etag,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            payload=jsonable_encoder(payload),
            expires_at=now + timedelta(minutes=10),
            used_at=None,
        )
        self.repo.add(row)
        await self.session.flush()
        return {
            "preflight_id": preflight_id,
            "preflight_token": token,
            "allowed": True,
            "warnings": [],
            "blocked_reasons": [],
            "resource_revision": expected_etag,
            "expires_at": row.expires_at,
            "server_now": now,
            "impacts": [],
        }

    async def consume_preflight(
        self,
        operation_id: str,
        scope: str,
        actor_id: str,
        target_id: str,
        token: str,
        header_token: str,
        current_etag: str,
    ) -> models.RoboticsPreflight:
        if token != header_token:
            raise ForbiddenError(code="PREFLIGHT_TOKEN_MISMATCH")
        preflight_id, separator, _secret = token.partition(".")
        if not separator:
            raise ForbiddenError(code="PREFLIGHT_TOKEN_INVALID")
        row = await self.repo.preflight(preflight_id)
        if (
            row is None
            or row.operation_id != operation_id
            or row.scope_key != scope
            or row.actor_id != actor_id
            or row.target_id != target_id
        ):
            raise NotFoundError(code="PREFLIGHT_NOT_FOUND")
        if hashlib.sha256(token.encode()).hexdigest() != row.token_hash:
            raise ForbiddenError(code="PREFLIGHT_TOKEN_INVALID")
        if as_utc(row.expires_at) <= utc_now():
            raise GoneError(code="PREFLIGHT_EXPIRED")
        if row.used_at is not None:
            raise VersionConflictError(code="PREFLIGHT_CONSUMED")
        if row.expected_etag != current_etag:
            raise PreconditionFailedError(code="ETAG_MISMATCH")
        row.used_at = utc_now()
        await self.session.flush()
        return row

    @staticmethod
    def check_mutable(lifecycle: str) -> None:
        if lifecycle != "DRAFT":
            raise VersionConflictError(code="PUBLISHED_RESOURCE_IMMUTABLE")

    @staticmethod
    def check_etag(current_etag: str, supplied_etag: str) -> None:
        if current_etag != supplied_etag:
            raise PreconditionFailedError(code="ETAG_MISMATCH")

    @staticmethod
    def bump(row: Any) -> None:
        row.resource_version = increment(row.resource_version)
        row.etag = compute_etag(row.resource_version)
        row.updated_at = utc_now()

    async def require_robot_model(self, organization_id: str, model_id: str):
        row = await self.repo.robot_model(organization_id, model_id)
        if row is None:
            raise NotFoundError(code="ROBOT_MODEL_NOT_FOUND")
        return row

    async def require_model_version(self, organization_id: str, version_id: str):
        row = await self.repo.model_version(organization_id, version_id)
        if row is None:
            raise NotFoundError(code="ROBOT_MODEL_VERSION_NOT_FOUND")
        return row

    async def require_robot(self, project_id: str, region_code: str, robot_id: str):
        row = await self.repo.robot(project_id, region_code, robot_id)
        if row is None:
            raise NotFoundError(code="ROBOT_NOT_FOUND")
        return row

    async def require_component(self, project_id: str, region_code: str, component_id: str):
        row = await self.repo.component(project_id, region_code, component_id)
        if row is None:
            raise NotFoundError(code="COMPONENT_NOT_FOUND")
        return row

    async def require_calibration_set(self, project_id: str, region_code: str, set_id: str):
        row = await self.repo.calibration_set(project_id, region_code, set_id)
        if row is None:
            raise NotFoundError(code="CALIBRATION_SET_NOT_FOUND")
        return row

    async def require_calibration_version(
        self, project_id: str, region_code: str, set_id: str, version: str
    ):
        await self.require_calibration_set(project_id, region_code, set_id)
        row = await self.repo.calibration_version(project_id, region_code, set_id, version)
        if row is None:
            raise NotFoundError(code="CALIBRATION_VERSION_NOT_FOUND")
        return row

    async def require_schema(self, organization_id: str, schema_id: str):
        row = await self.repo.data_schema(organization_id, schema_id)
        if row is None:
            raise NotFoundError(code="DATA_SCHEMA_NOT_FOUND")
        return row

    async def require_schema_version(self, organization_id: str, schema_id: str, version: str):
        await self.require_schema(organization_id, schema_id)
        row = await self.repo.schema_version(organization_id, schema_id, version)
        if row is None:
            raise NotFoundError(code="SCHEMA_VERSION_NOT_FOUND")
        return row
