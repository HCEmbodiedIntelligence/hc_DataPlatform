"""Application services for snapshot-bound storage lifecycle governance."""

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
    ValidationError,
    VersionConflictError,
)
from app.core.etag import compute_etag
from app.core.ids import new_id

from .models import (
    LifecyclePolicySetModel,
    LifecyclePolicyVersionModel,
    LifecycleSimulationModel,
    MultipartAbortModel,
    MultipartAbortPlanModel,
    MultipartUploadProjectionModel,
    RestorePreflightModel,
    RestoreTaskModel,
    StorageJobModel,
    StorageObjectModel,
)
from .repository import ScopeTuple, StorageRepository
from .schemas import (
    AbortMultipartRequest,
    CreateLifecyclePolicyRequest,
    CreateSimulationRequest,
    EnableLifecyclePolicyRequest,
    PauseLifecyclePolicyRequest,
    PlanMultipartAbortRequest,
    RestoreCommitRequest,
    RestorePreflightRequest,
    UpdateLifecyclePolicyRequest,
)

POLICY_SET_EMPTY_VERSION = "0"
POLICY_SET_EMPTY_ETAG = compute_etag(POLICY_SET_EMPTY_VERSION)


def utc_now() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def scope_key(scope: ScopeTuple) -> str:
    return ":".join(scope)


def canonical_hash(value: Any) -> str:
    payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def next_version(value: str) -> str:
    try:
        return str(int(value) + 1)
    except ValueError:
        return new_id("event")


def job_projection(row: StorageJobModel) -> dict[str, Any]:
    return {
        "id": row.job_id,
        "status": row.status,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "attempt": row.attempt,
        "progress": row.progress,
        "failure": row.failure,
    }


def object_projection(row: StorageObjectModel) -> dict[str, Any]:
    """Return diagnostics without provider bucket/key/version locators."""

    return {
        "id": row.object_id,
        "display_key": row.display_key,
        "object_version": row.object_version,
        "object_role": row.object_role,
        "classification_status": row.classification_status,
        "physical_bytes": str(row.physical_bytes),
        "storage_class": row.storage_class,
        "media_type": row.media_type,
        "status": row.status,
        "content_sha256": row.content_sha256,
        "created_at": row.created_at,
        "last_accessed_at": row.last_accessed_at,
        "expires_at": row.expires_at,
        "etag": row.etag,
        "allowed_actions": ["VIEW_RESOURCE"],
    }


def policy_projection(row: LifecyclePolicyVersionModel) -> dict[str, Any]:
    payload = dict(row.payload)
    return {
        "id": row.policy_id,
        **payload,
        "version": row.version,
        "etag": row.etag,
        "status": row.status,
        "simulation_input_hash": row.simulation_input_hash,
        "effective_at": row.effective_at,
        "updated_at": row.updated_at,
        "allowed_actions": [
            {"action": action, "allowed": True, "reason_code": None}
            for action in ("UPDATE", "ENABLE", "PAUSE", "SIMULATE")
        ],
        "protected_reasons": [],
    }


class StorageService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = StorageRepository(session)

    async def require_snapshot(self, scope: ScopeTuple, snapshot_id: str | None = None):
        row = (
            await self.repo.get_snapshot(scope, snapshot_id)
            if snapshot_id
            else await self.repo.current_snapshot(scope)
        )
        if row is None:
            raise VersionConflictError(
                code="INVENTORY_SNAPSHOT_UNAVAILABLE",
                message="No committed inventory snapshot is available for this scope.",
            )
        return row

    async def create_inventory_job(
        self, scope: ScopeTuple, expected_snapshot_id: str | None
    ) -> StorageJobModel:
        snapshot = await self.require_snapshot(scope)
        if expected_snapshot_id and expected_snapshot_id != snapshot.snapshot_id:
            raise VersionConflictError(code="INVENTORY_SNAPSHOT_CHANGED")
        now = utc_now()
        job = StorageJobModel(
            job_id=new_id("job"),
            scope_key=scope_key(scope),
            status="QUEUED",
            resource_type="INVENTORY_SNAPSHOT",
            resource_id=snapshot.snapshot_id,
            attempt=0,
            progress=0,
            failure=None,
            created_at=now,
            updated_at=now,
        )
        self.repo.add(job)
        await self.session.flush()
        return job

    async def get_or_create_policy_set(self, scope: ScopeTuple) -> LifecyclePolicySetModel:
        row = await self.repo.policy_set(scope)
        if row is not None:
            return row
        organization_id, project_id, region_code = scope
        row = LifecyclePolicySetModel(
            policy_set_id=new_id("object"),
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            version=POLICY_SET_EMPTY_VERSION,
            etag=POLICY_SET_EMPTY_ETAG,
            updated_at=utc_now(),
        )
        self.repo.add(row)
        await self.session.flush()
        return row

    @staticmethod
    def assert_policy_set(
        row: LifecyclePolicySetModel, expected_version: str, supplied_etag: str
    ) -> None:
        if supplied_etag != row.etag:
            raise PreconditionFailedError(code="ETAG_MISMATCH")
        if expected_version != row.version:
            raise VersionConflictError(code="POLICY_SET_VERSION_CHANGED")

    async def create_policy(
        self,
        scope: ScopeTuple,
        body: CreateLifecyclePolicyRequest,
        supplied_etag: str,
    ) -> tuple[LifecyclePolicyVersionModel, LifecyclePolicySetModel]:
        policy_set = await self.get_or_create_policy_set(scope)
        self.assert_policy_set(policy_set, body.expected_policy_set_version, supplied_etag)
        now = utc_now()
        policy_id = new_id("object")
        version = "1"
        organization_id, project_id, region_code = scope
        row = LifecyclePolicyVersionModel(
            row_id=f"{policy_id}:{version}",
            policy_id=policy_id,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            version=version,
            current=True,
            status="PAUSED",
            etag=compute_etag(version),
            payload=body.policy.model_dump(mode="json"),
            simulation_input_hash=canonical_hash(body.policy),
            effective_at=None,
            updated_at=now,
        )
        policy_set.version = next_version(policy_set.version)
        policy_set.etag = compute_etag(policy_set.version)
        policy_set.updated_at = now
        self.repo.add(row)
        await self.session.flush()
        return row, policy_set

    async def update_policy(
        self,
        scope: ScopeTuple,
        policy_id: str,
        body: UpdateLifecyclePolicyRequest,
        supplied_etag: str,
    ) -> tuple[LifecyclePolicyVersionModel, LifecyclePolicySetModel]:
        current = await self.repo.current_policy(scope, policy_id)
        if current is None:
            raise NotFoundError(code="LIFECYCLE_POLICY_NOT_FOUND")
        if supplied_etag != current.etag:
            raise PreconditionFailedError(code="ETAG_MISMATCH")
        if body.expected_policy_version != current.version:
            raise VersionConflictError(code="POLICY_VERSION_CHANGED")
        policy_set = await self.get_or_create_policy_set(scope)
        if body.expected_policy_set_version != policy_set.version:
            raise VersionConflictError(code="POLICY_SET_VERSION_CHANGED")
        current.current = False
        version = next_version(current.version)
        now = utc_now()
        row = LifecyclePolicyVersionModel(
            row_id=f"{policy_id}:{version}",
            policy_id=policy_id,
            organization_id=current.organization_id,
            project_id=current.project_id,
            region_code=current.region_code,
            version=version,
            current=True,
            status="PAUSED",
            etag=compute_etag(version),
            payload=body.policy.model_dump(mode="json"),
            simulation_input_hash=canonical_hash(body.policy),
            effective_at=None,
            updated_at=now,
        )
        policy_set.version = next_version(policy_set.version)
        policy_set.etag = compute_etag(policy_set.version)
        policy_set.updated_at = now
        self.repo.add(row)
        await self.session.flush()
        return row, policy_set

    async def create_simulation(
        self, scope: ScopeTuple, body: CreateSimulationRequest
    ) -> tuple[LifecycleSimulationModel, StorageJobModel]:
        snapshot_id = body.snapshot_id
        snapshot = await self.require_snapshot(scope, snapshot_id)
        policy_set = await self.get_or_create_policy_set(scope)
        if body.mode == "SAVED_POLICIES":
            if body.policy_set_version != policy_set.version:
                raise VersionConflictError(code="POLICY_SET_VERSION_CHANGED")
            policy_versions = [item.model_dump(mode="json") for item in body.policy_versions]
            input_hash = body.input_hash
        else:
            if body.baseline_policy_set_version != policy_set.version:
                raise VersionConflictError(code="POLICY_SET_VERSION_CHANGED")
            policy_versions = []
            input_hash = canonical_hash(body.draft_policy)
        now = utc_now()
        simulation_id = new_id("event")
        job = StorageJobModel(
            job_id=new_id("job"),
            scope_key=scope_key(scope),
            status="SUCCEEDED",
            resource_type="LIFECYCLE_SIMULATION",
            resource_id=simulation_id,
            attempt=1,
            progress=1,
            failure=None,
            created_at=now,
            updated_at=now,
        )
        impact_digest = canonical_hash(
            {"scope": scope, "snapshot_id": snapshot_id, "input_hash": input_hash}
        )
        result = {
            "id": simulation_id,
            "status": "READY",
            "mode": body.mode,
            "input_hash": input_hash,
            "impact_digest": impact_digest,
            "policy_set_version": policy_set.version,
            "policy_versions": policy_versions,
            "snapshot_id": snapshot_id,
            "snapshot_at": snapshot.as_of,
            "created_at": now,
            "expires_at": now + timedelta(hours=2),
            "server_now": now,
            "candidates": [],
            "protected": [],
            "unknown_summary": {"object_count": "0", "physical_bytes": "0"},
            "failure": None,
            "job_id": job.job_id,
        }
        simulation = LifecycleSimulationModel(
            simulation_id=simulation_id,
            organization_id=scope[0],
            project_id=scope[1],
            region_code=scope[2],
            status="READY",
            mode=body.mode,
            input_hash=input_hash,
            impact_digest=impact_digest,
            policy_set_version=policy_set.version,
            policy_versions=policy_versions,
            snapshot_id=snapshot_id,
            snapshot_at=snapshot.as_of,
            result_projection=jsonable_encoder(result),
            job_id=job.job_id,
            audit_event_id="pending",
            created_at=now,
            expires_at=now + timedelta(hours=2),
        )
        self.repo.add(job)
        self.repo.add(simulation)
        await self.session.flush()
        return simulation, job

    async def enable_policy(
        self,
        scope: ScopeTuple,
        policy_id: str,
        body: EnableLifecyclePolicyRequest,
        supplied_etag: str,
    ) -> tuple[LifecyclePolicyVersionModel, LifecyclePolicySetModel]:
        policy = await self.repo.current_policy(scope, policy_id)
        if policy is None:
            raise NotFoundError(code="LIFECYCLE_POLICY_NOT_FOUND")
        if supplied_etag != policy.etag:
            raise PreconditionFailedError(code="ETAG_MISMATCH")
        if policy.version != body.policy_version:
            raise VersionConflictError(code="POLICY_VERSION_CHANGED")
        simulation = await self.repo.get_simulation(scope, body.simulation_id)
        if simulation is None:
            raise NotFoundError(code="LIFECYCLE_SIMULATION_NOT_FOUND")
        if as_utc(simulation.expires_at) <= utc_now():
            raise GoneError(code="SIMULATION_EXPIRED")
        expected = (
            simulation.status == "READY"
            and simulation.input_hash == body.input_hash
            and simulation.impact_digest == body.confirmation.impact_digest
            and simulation.snapshot_id == body.snapshot_id
            and simulation.policy_set_version == body.policy_set_version
            and simulation.policy_versions
            == [item.model_dump(mode="json") for item in body.policy_versions]
        )
        if not expected:
            raise VersionConflictError(code="SIMULATION_EVIDENCE_MISMATCH")
        policy_set = await self.get_or_create_policy_set(scope)
        policy.status = "ACTIVE"
        policy.effective_at = utc_now()
        policy.updated_at = utc_now()
        policy_set.version = next_version(policy_set.version)
        policy_set.etag = compute_etag(policy_set.version)
        policy_set.updated_at = utc_now()
        await self.session.flush()
        return policy, policy_set

    async def pause_policy(
        self,
        scope: ScopeTuple,
        policy_id: str,
        body: PauseLifecyclePolicyRequest,
        supplied_etag: str,
    ) -> tuple[LifecyclePolicyVersionModel, LifecyclePolicySetModel]:
        policy = await self.repo.current_policy(scope, policy_id)
        if policy is None:
            raise NotFoundError(code="LIFECYCLE_POLICY_NOT_FOUND")
        if supplied_etag != policy.etag:
            raise PreconditionFailedError(code="ETAG_MISMATCH")
        policy_set = await self.get_or_create_policy_set(scope)
        if policy.version != body.policy_version:
            raise VersionConflictError(code="POLICY_VERSION_CHANGED")
        if policy_set.version != body.expected_policy_set_version:
            raise VersionConflictError(code="POLICY_SET_VERSION_CHANGED")
        policy.status = "PAUSED"
        policy.effective_at = None
        policy.updated_at = utc_now()
        policy_set.version = next_version(policy_set.version)
        policy_set.etag = compute_etag(policy_set.version)
        policy_set.updated_at = utc_now()
        await self.session.flush()
        return policy, policy_set

    async def restore_preflight(
        self, scope: ScopeTuple, actor_id: str, body: RestorePreflightRequest
    ) -> dict[str, Any]:
        if body.target.resource_type != "STORAGE_OBJECT":
            raise NotFoundError(code="RESTORE_TARGET_NOT_FOUND")
        target = await self.repo.get_object(scope, body.target.resource_id)
        if target is None:
            raise NotFoundError(code="RESTORE_TARGET_NOT_FOUND")
        if target.status in {"DELETED", "MISSING", "TOMBSTONED"}:
            raise ValidationError(code="RESTORE_TARGET_DELETED")
        if target.storage_class not in {"IA", "ARCHIVE", "COLD_ARCHIVE"}:
            raise ValidationError(code="RESTORE_NOT_REQUIRED")
        now = utc_now()
        token = secrets.token_urlsafe(32)
        quote_id = new_id("event")
        options = [
            {
                "restore_method": method,
                "min_ready_seconds": "60" if method == "EXPEDITED" else "300",
                "max_ready_seconds": "900" if method == "EXPEDITED" else "14400",
                "temporary_available_seconds": "86400",
                "estimated_cost": {"availability": "COMPUTING", "currency": "CNY"},
                "allowed": True,
                "blocked_reasons": [],
            }
            for method in ("STANDARD", "EXPEDITED")
        ]
        projection = {
            "mode": "PREFLIGHT",
            "quote_id": quote_id,
            "expires_at": now + timedelta(minutes=10),
            "server_now": now,
            "target": {
                "resource_type": "STORAGE_OBJECT",
                "resource_id": target.object_id,
                "display_name": target.display_key,
                "target_version": target.object_version,
                "storage_class": target.storage_class,
                "physical_bytes": str(target.physical_bytes),
            },
            "options": options,
        }
        self.repo.add(
            RestorePreflightModel(
                quote_id=quote_id,
                scope_key=scope_key(scope),
                actor_id=actor_id,
                token_hash=token_digest(token),
                target_type="STORAGE_OBJECT",
                target_id=target.object_id,
                target_version=target.object_version,
                evidence_digest=canonical_hash(projection),
                expires_at=now + timedelta(minutes=10),
                used_at=None,
                projection=jsonable_encoder(projection),
            )
        )
        await self.session.flush()
        return {**projection, "preflight_token": token}

    async def restore_commit(
        self, scope: ScopeTuple, actor_id: str, body: RestoreCommitRequest
    ) -> tuple[RestoreTaskModel, StorageJobModel]:
        quote = await self.repo.get_restore_preflight(body.quote_id)
        if quote is None or quote.scope_key != scope_key(scope) or quote.actor_id != actor_id:
            raise NotFoundError(code="RESTORE_PREFLIGHT_NOT_FOUND")
        if as_utc(quote.expires_at) <= utc_now():
            raise GoneError(code="RESTORE_PREFLIGHT_EXPIRED")
        if quote.used_at is not None:
            raise VersionConflictError(code="RESTORE_PREFLIGHT_CONSUMED")
        if token_digest(body.preflight_token) != quote.token_hash:
            raise ForbiddenError(code="RESTORE_PREFLIGHT_TOKEN_INVALID")
        target = await self.repo.get_object(scope, quote.target_id)
        if target is None:
            raise NotFoundError(code="RESTORE_TARGET_NOT_FOUND")
        if target.object_version != body.expected_target_version:
            raise VersionConflictError(code="RESTORE_TARGET_VERSION_CHANGED")
        methods = {option["restore_method"] for option in quote.projection["options"]}
        if body.restore_method not in methods:
            raise ValidationError(code="RESTORE_METHOD_NOT_OFFERED")
        now = utc_now()
        task_id = new_id("event")
        job = StorageJobModel(
            job_id=new_id("job"),
            scope_key=scope_key(scope),
            status="QUEUED",
            resource_type="RESTORE_TASK",
            resource_id=task_id,
            attempt=0,
            progress=0,
            failure=None,
            created_at=now,
            updated_at=now,
        )
        projection = {
            "id": task_id,
            "target": quote.projection["target"],
            "storage_class": target.storage_class,
            "restore_method": body.restore_method,
            "status": "QUEUED",
            "physical_bytes": str(target.physical_bytes),
            "requested_at": now,
            "estimated_ready_at": now + timedelta(minutes=15),
            "available_at": None,
            "available_until": None,
            "estimated_cost": {"availability": "COMPUTING", "currency": "CNY"},
            "actual_cost": {"availability": "COMPUTING", "currency": "CNY"},
            "failure": None,
            "job_id": job.job_id,
            "allowed_actions": [],
        }
        task = RestoreTaskModel(
            restore_task_id=task_id,
            organization_id=scope[0],
            project_id=scope[1],
            region_code=scope[2],
            status="QUEUED",
            requested_at=now,
            projection=jsonable_encoder(projection),
            job_id=job.job_id,
        )
        quote.used_at = now
        self.repo.add(job)
        self.repo.add(task)
        await self.session.flush()
        return task, job

    @staticmethod
    def _validate_abortable(upload: MultipartUploadProjectionModel) -> None:
        if upload.status not in {"ACTIVE", "PAUSED_RESUMABLE", "INACTIVE", "ABORT_FAILED"}:
            raise VersionConflictError(code="MULTIPART_NOT_ABORTABLE")
        if upload.protection_state != "UNPROTECTED" or upload.protected_reasons:
            raise ValidationError(
                code="MULTIPART_ABORT_PROTECTED", blocked_reasons=upload.protected_reasons
            )

    async def plan_abort(
        self,
        scope: ScopeTuple,
        upload_id: str,
        body: PlanMultipartAbortRequest,
        supplied_etag: str,
    ) -> tuple[dict[str, Any], MultipartAbortPlanModel]:
        upload = await self.repo.get_multipart(scope, upload_id)
        if upload is None:
            raise NotFoundError(code="MULTIPART_UPLOAD_NOT_FOUND")
        if supplied_etag != upload.etag:
            raise PreconditionFailedError(code="ETAG_MISMATCH")
        self._validate_abortable(upload)
        if upload.upload_version != body.upload_version or as_utc(
            upload.last_activity_at
        ) != as_utc(body.observed_last_activity_at):
            raise VersionConflictError(code="MULTIPART_ABORT_PLAN_STALE")
        now = utc_now()
        token = secrets.token_urlsafe(32)
        plan_id = new_id("event")
        projection = {
            "plan_id": plan_id,
            "expires_at": now + timedelta(minutes=10),
            "server_now": now,
            "upload_id": upload.upload_id,
            "upload_version": upload.upload_version,
            "etag": upload.etag,
            "observed_last_activity_at": upload.last_activity_at,
            "uploaded_bytes": str(upload.uploaded_bytes),
            "uploaded_part_count": str(upload.uploaded_part_count),
            "impact_digest": canonical_hash(
                {
                    "upload_id": upload.upload_id,
                    "version": upload.upload_version,
                    "activity": upload.last_activity_at,
                }
            ),
            "allowed": True,
            "protected_reasons": [],
        }
        plan = MultipartAbortPlanModel(
            plan_id=plan_id,
            upload_id=upload.upload_id,
            upload_version=upload.upload_version,
            etag=upload.etag,
            observed_last_activity_at=upload.last_activity_at,
            provider_etag=upload.provider_etag,
            token_hash=token_digest(token),
            evidence_digest=projection["impact_digest"],
            expires_at=now + timedelta(minutes=10),
            consumed_at=None,
        )
        self.repo.add(plan)
        await self.session.flush()
        return {**projection, "confirmation_token": token}, plan

    async def commit_abort(
        self,
        scope: ScopeTuple,
        upload_id: str,
        body: AbortMultipartRequest,
        supplied_etag: str,
    ) -> tuple[MultipartAbortModel, StorageJobModel, MultipartUploadProjectionModel]:
        plan = await self.repo.get_abort_plan(body.plan_id)
        upload = await self.repo.get_multipart(scope, upload_id)
        if plan is None or plan.upload_id != upload_id or upload is None:
            raise NotFoundError(code="MULTIPART_ABORT_PLAN_NOT_FOUND")
        if as_utc(plan.expires_at) <= utc_now():
            raise GoneError(code="MULTIPART_ABORT_PLAN_EXPIRED")
        if plan.consumed_at is not None:
            raise VersionConflictError(code="MULTIPART_ABORT_PLAN_CONSUMED")
        if token_digest(body.confirmation_token) != plan.token_hash:
            raise ForbiddenError(code="MULTIPART_ABORT_TOKEN_INVALID")
        self._validate_abortable(upload)
        if supplied_etag != upload.etag:
            raise PreconditionFailedError(code="ETAG_MISMATCH")
        if (
            plan.upload_version != body.upload_version
            or upload.upload_version != plan.upload_version
            or as_utc(plan.observed_last_activity_at) != as_utc(body.observed_last_activity_at)
            or as_utc(upload.last_activity_at) != as_utc(plan.observed_last_activity_at)
            or upload.provider_etag != plan.provider_etag
        ):
            raise VersionConflictError(code="MULTIPART_ABORT_PLAN_STALE")
        now = utc_now()
        abort_id = new_id("event")
        job = StorageJobModel(
            job_id=new_id("job"),
            scope_key=scope_key(scope),
            status="QUEUED",
            resource_type="MULTIPART_ABORT",
            resource_id=upload_id,
            attempt=0,
            progress=0,
            failure=None,
            created_at=now,
            updated_at=now,
        )
        abort = MultipartAbortModel(
            abort_id=abort_id,
            plan_id=plan.plan_id,
            upload_id=upload_id,
            job_id=job.job_id,
            status="QUEUED",
            evidence_digest=plan.evidence_digest,
            created_at=now,
        )
        plan.consumed_at = now
        upload.status = "ABORTING"
        self.repo.add(job)
        self.repo.add(abort)
        await self.session.flush()
        return abort, job, upload
