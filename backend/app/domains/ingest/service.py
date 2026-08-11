from __future__ import annotations

import hashlib
import json
import os
import secrets
from collections.abc import Callable, Coroutine
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.context import RequestContext
from app.core.errors import NotFoundError, ServerError, ValidationError, VersionConflictError
from app.core.etag import check_if_match
from app.core.idempotency import with_idempotency
from app.core.ids import new_id
from app.core.outbox import emit_event
from app.core.pagination import CursorParams, build_page
from app.domains.ingest import models, schemas
from app.domains.ingest.repository import (
    IngestRepository,
    apply_keyset,
    restore_keyset_order,
    scope_predicate,
)
from app.platform.ports import ObjectStoragePort
from app.platform.storage import get_object_storage

CONTRACT_VERSION = "ingest.v1alpha1"
ACTIVE_UPLOAD_STATES = {
    "AUTHORIZING",
    "UPLOADING",
    "PAUSED",
    "FINALIZING",
    "PENDING_VERIFY",
    "VERIFYING",
    "CANCELLING",
}
VERIFICATION_STAGES = (
    "MANIFEST_SCHEMA",
    "OBJECT_EXISTENCE_SIZE",
    "OBJECT_SHA256",
    "ADAPTER_PARSE",
    "DATASET_SCHEMA_SEMANTIC",
    "ATOMIC_AVAILABILITY_COMMIT",
)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(UTC).isoformat().replace("+00:00", "Z")


def _wire(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return jsonable_encoder(value)


def _int(value: Any) -> int:
    while hasattr(value, "root"):
        value = value.root
    return int(value)


def _scope(ctx: RequestContext) -> dict[str, str]:
    return {
        "organization_id": ctx.organization_id,
        "project_id": ctx.project_id,
        "region_code": ctx.region_code,
    }


def envelope(data: Any, ctx: RequestContext) -> dict[str, Any]:
    return {
        "data": data,
        "scope": _scope(ctx),
        "request_id": ctx.request_id,
        "contract_version": CONTRACT_VERSION,
    }


def _source_etag(version: int) -> str:
    return f'"source-rv-{version}"'


def _upload_etag(version: int) -> str:
    return f'"upload-rv-{version}"'


def _job_etag(version: int = 1) -> str:
    return f'"job-rv-{version}"'


def _safe_binding(command_value: Any) -> dict[str, Any]:
    value = dict(_wire(command_value))
    value.setdefault("display_name", None)
    return value


def _safe_configuration(command_value: Any) -> dict[str, Any]:
    value = dict(_wire(command_value))
    if value.get("kind") == "ROBOT":
        value.setdefault("safe_endpoint_hint", None)
    return value


def _source_ref(source: models.DataSource) -> dict[str, Any]:
    projection = source.projection
    return {
        "id": source.source_id,
        "name": projection["name"],
        "source_type": source.source_type,
        "source_format": source.source_format,
        "configuration_version": str(source.config_version),
        "credential_version": str(source.credential_version),
        "upload_policy_version": str(source.upload_policy_version),
    }


def _source_summary(source: models.DataSource) -> dict[str, Any]:
    projection = dict(source.projection)
    projection.pop("configuration", None)
    return projection


def _platform_job(job: models.UploadJob, ctx: RequestContext) -> dict[str, Any]:
    internal = job.projection
    result_ref = internal.get("result_ref")
    return {
        "job_id": job.job_id,
        "kind": job.job_type,
        "status": job.status,
        "stage": internal.get("stage", "QUEUED"),
        "progress": {
            "completed_units": internal.get("progress", {}).get("completed", "0"),
            "total_units": internal.get("progress", {}).get("total"),
            "unit": internal.get("progress", {}).get("unit"),
            "percent": None,
            "message": None,
        },
        "result_ref": result_ref,
        "error": None,
        "scope": _scope(ctx),
        "resource_ref": {
            "resource_type": job.resource_type.lower(),
            "resource_id": job.resource_id,
            "version": None,
            "etag": None,
        },
        "created_at": _iso(job.created_at),
        "started_at": None,
        "finished_at": None,
        "updated_at": _iso(job.updated_at),
        "expires_at": None,
        "etag": internal["etag"],
        "cancellable": job.status in {"QUEUED", "RUNNING"},
        "retry_of_job_id": None,
    }


class IngestService:
    def __init__(
        self, session: AsyncSession, object_storage: ObjectStoragePort | None = None
    ) -> None:
        self.session = session
        self.repo = IngestRepository(session)
        self.object_storage = object_storage or get_object_storage()

    def _idempotency_scope(self, ctx: RequestContext, operation_id: str) -> dict[str, str]:
        return {**_scope(ctx), "actor_id": ctx.actor_id, "operation_id": operation_id}

    async def _write(
        self,
        *,
        ctx: RequestContext,
        key: str,
        operation_id: str,
        audit_event: str,
        event_type: str,
        target_type: str,
        target_id: str | Callable[[], str],
        action: Callable[[], Coroutine[Any, Any, Any]],
        rehydrate: Callable[[Any], Coroutine[Any, Any, Any]] | None = None,
    ) -> Any:
        ephemeral: list[Any] = []

        async def unit() -> Any:
            result = await action()
            ephemeral.append(result)
            resolved_target_id = target_id() if callable(target_id) else target_id
            await write_audit(
                self.session,
                audit_event,
                target_type,
                resolved_target_id,
                "SUCCEEDED",
                ctx,
                {"operation_id": operation_id},
            )
            await emit_event(
                self.session,
                event_type,
                target_type,
                resolved_target_id,
                {"operation_id": operation_id, "request_id": ctx.request_id},
                ctx,
            )
            stable = deepcopy(result)
            if isinstance(stable, dict) and isinstance(stable.get("data"), dict):
                data = stable["data"]
                if "secret" in data:
                    data["secret"] = {"redacted": True}
            return stable

        persisted = await with_idempotency(
            self.session, key, self._idempotency_scope(ctx, operation_id), unit
        )
        if ephemeral:
            return ephemeral[0]
        if rehydrate is not None:
            return await rehydrate(persisted)
        return persisted

    async def get_source(self, ctx: RequestContext, source_id: str) -> models.DataSource:
        return await self.repo.source(ctx, source_id)

    async def list_sources(self, ctx: RequestContext, params: CursorParams) -> dict[str, Any]:
        stmt = select(models.DataSource).where(scope_predicate(models.DataSource, ctx))
        stmt = apply_keyset(stmt, params, models.DataSource.updated_at, models.DataSource.source_id)
        rows = list((await self.session.scalars(stmt)).all())
        rows = restore_keyset_order(rows, params)
        page = build_page(rows, params, ("updated_at", "source_id"))
        page["items"] = [_source_summary(row) for row in page["items"]]
        page.update(scope=_scope(ctx), request_id=ctx.request_id, contract_version=CONTRACT_VERSION)
        return page

    async def source_page(self, ctx: RequestContext, params: CursorParams) -> dict[str, Any]:
        page = await self.list_sources(ctx, params)
        total = await self.session.scalar(
            select(func.count())
            .select_from(models.DataSource)
            .where(scope_predicate(models.DataSource, ctx))
        )
        online = sum(
            1 for item in page["items"] if item.get("connectivity", {}).get("state") == "ONLINE"
        )
        facets: dict[str, Any] = {}
        for response_name, field in (
            ("source_types", "source_type"),
            ("source_formats", "source_format"),
            ("administrative_states", "administrative_state"),
        ):
            counts: dict[str, int] = {}
            for item in page["items"]:
                value = item.get(field)
                if value:
                    counts[value] = counts.get(value, 0) + 1
            facets[response_name] = [
                {"value": value, "label": value, "count": str(count)}
                for value, count in sorted(counts.items())
            ]
        for name in (
            "robots",
            "upload_policies",
            "connectivity_states",
            "credential_states",
            "heartbeat_states",
        ):
            facets.setdefault(name, [])
        return {
            "summary": {
                "total_count": str(total or 0),
                "online_count": str(online),
                "verified_bytes_today": "0",
                "abnormal_count": "0",
                "as_of": page["snapshot_at"],
                "timezone": "UTC",
                "definition_version": "1",
            },
            "facets": facets,
            "items": page["items"],
            "page_info": page["page_info"],
            "snapshot_at": page["snapshot_at"],
            "allowed_actions": ["CREATE"],
            "component_errors": [],
            "scope": _scope(ctx),
            "request_id": ctx.request_id,
            "contract_version": CONTRACT_VERSION,
        }

    async def create_source(
        self, ctx: RequestContext, key: str, command: schemas.CreateDataSourceCommand
    ) -> dict[str, Any]:
        source_id = new_id("source")

        async def action() -> dict[str, Any]:
            binding_kind = _wire(command.binding)["kind"]
            configuration_kind = _wire(command.configuration)["kind"]
            if binding_kind != command.source_type or configuration_kind != command.source_type:
                raise ValidationError(code="DATA_SOURCE_KIND_MISMATCH")
            normalized = command.name.strip().casefold()
            duplicate = await self.session.scalar(
                select(models.DataSource.source_id).where(
                    scope_predicate(models.DataSource, ctx),
                    models.DataSource.normalized_name == normalized,
                )
            )
            if duplicate:
                raise VersionConflictError(code="DATA_SOURCE_NAME_CONFLICT")
            now = _now()
            credential_input = _wire(command.credential_input) if command.credential_input else None
            credential_version = 1 if credential_input else 0
            credential_kind = credential_input["kind"] if credential_input else "NONE"
            credential_ref_id = new_id("credential_ref") if credential_input else None
            projection = {
                "id": source_id,
                "scope": _scope(ctx),
                "name": command.name.strip(),
                "source_type": command.source_type,
                "source_format": command.source_format,
                "source_format_version": command.source_format_version,
                "adapter_version": os.getenv("INGEST_ADAPTER_VERSION", "adapter-v1"),
                "binding": _safe_binding(command.binding),
                "configuration": _safe_configuration(command.configuration),
                "administrative_state": "DISABLED",
                "credential": {
                    "kind": credential_kind,
                    "state": "CONFIGURED" if credential_input else "NOT_REQUIRED",
                    "credential_ref": credential_ref_id,
                    "masked_hint": None,
                    "version": str(credential_version),
                    "updated_at": _iso(now) if credential_input else None,
                    "expires_at": None,
                    "rotation_due_at": None,
                },
                "connectivity": {
                    "state": "UNKNOWN",
                    "last_check_state": "NOT_RUN",
                    "observed_config_version": None,
                    "observed_credential_version": None,
                    "checked_at": None,
                    "safe_error": None,
                },
                "heartbeat": None,
                "upload_policy": {
                    "code": command.upload_policy_code,
                    "label": command.upload_policy_code,
                    "max_object_size_bytes": "1099511627776",
                },
                "last_upload": None,
                "config_version": "1",
                "credential_version": str(credential_version),
                "etag": _source_etag(1),
                "allowed_actions": ["VIEW", "EDIT_CONFIGURATION", "TEST_CONNECTION", "ENABLE"],
                "blocked_reasons": [],
                "created_at": _iso(now),
                "updated_at": _iso(now),
            }
            row = models.DataSource(
                source_id=source_id,
                **_scope(ctx),
                normalized_name=normalized,
                source_type=command.source_type,
                source_format=command.source_format,
                source_format_version=command.source_format_version,
                adapter_version=projection["adapter_version"],
                administrative_state="DISABLED",
                config_version=1,
                credential_version=credential_version,
                upload_policy_version=1,
                resource_version=1,
                etag=projection["etag"],
                projection=projection,
                created_at=now,
                updated_at=now,
            )
            self.session.add(row)
            if credential_input:
                self.session.add(
                    models.CredentialRef(
                        credential_ref_id=credential_ref_id,
                        source_id=source_id,
                        version=1,
                        credential_type=credential_kind,
                        managed_secret_ref=f"managed://ingest/{source_id}/1",
                        state="CONFIGURED",
                        safe_hint=None,
                        created_at=now,
                    )
                )
            await self.session.flush()
            return envelope(projection, ctx)

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="createDataSource",
            audit_event="ingest.source.created",
            event_type="ingest.source.created",
            target_type="ingest.data_source",
            target_id=source_id,
            action=action,
        )

    async def update_source(
        self,
        ctx: RequestContext,
        key: str,
        request: Request,
        source_id: str,
        command: schemas.UpdateDataSourceCommand,
    ) -> dict[str, Any]:
        async def action() -> dict[str, Any]:
            row = await self.repo.source(ctx, source_id, lock=True)
            check_if_match(request, row.etag)
            if (
                _wire(command.binding)["kind"] != row.source_type
                or _wire(command.configuration)["kind"] != row.source_type
            ):
                raise ValidationError(code="DATA_SOURCE_KIND_MISMATCH")
            row.config_version += 1
            row.resource_version += 1
            row.updated_at = _now()
            row.normalized_name = command.name.strip().casefold()
            row.source_format = command.source_format
            row.source_format_version = command.source_format_version
            projection = dict(row.projection)
            projection.update(
                name=command.name.strip(),
                source_format=command.source_format,
                source_format_version=command.source_format_version,
                binding=_safe_binding(command.binding),
                configuration=_safe_configuration(command.configuration),
                config_version=str(row.config_version),
                updated_at=_iso(row.updated_at),
            )
            if projection["upload_policy"]["code"] != command.upload_policy_code:
                row.upload_policy_version += 1
            projection["upload_policy"] = {
                "code": command.upload_policy_code,
                "label": command.upload_policy_code,
                "max_object_size_bytes": projection["upload_policy"]["max_object_size_bytes"],
            }
            row.etag = projection["etag"] = _source_etag(row.resource_version)
            row.projection = projection
            await self.session.flush()
            return envelope(projection, ctx)

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="updateDataSource",
            audit_event="ingest.source.updated",
            event_type="ingest.source.updated",
            target_type="ingest.data_source",
            target_id=source_id,
            action=action,
        )

    async def rotate_credential(
        self,
        ctx: RequestContext,
        key: str,
        request: Request,
        source_id: str,
        command: schemas.RotateCredentialCommand,
    ) -> dict[str, Any]:
        async def action() -> dict[str, Any]:
            row = await self.repo.source(ctx, source_id, lock=True)
            check_if_match(request, row.etag)
            now = _now()
            row.credential_version += 1
            credential = _wire(command.credential_input)
            ref_id = new_id("credential_ref")
            self.session.add(
                models.CredentialRef(
                    credential_ref_id=ref_id,
                    source_id=source_id,
                    version=row.credential_version,
                    credential_type=credential["kind"],
                    managed_secret_ref=f"managed://ingest/{source_id}/{row.credential_version}",
                    state="CONFIGURED",
                    safe_hint=None,
                    created_at=now,
                )
            )
            projection = dict(row.projection)
            projection["credential"] = {
                "kind": credential["kind"],
                "state": "CONFIGURED",
                "credential_ref": ref_id,
                "masked_hint": None,
                "version": str(row.credential_version),
                "updated_at": _iso(now),
                "expires_at": None,
                "rotation_due_at": None,
            }
            projection["credential_version"] = str(row.credential_version)
            row.resource_version += 1
            row.updated_at = now
            row.etag = projection["etag"] = _source_etag(row.resource_version)
            projection["updated_at"] = _iso(now)
            row.projection = projection
            await self.session.flush()
            return envelope(projection, ctx)

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="rotateDataSourceCredential",
            audit_event="ingest.source.credential_rotated",
            event_type="ingest.source.credential_rotated",
            target_type="ingest.data_source",
            target_id=source_id,
            action=action,
        )

    async def test_connection(
        self,
        ctx: RequestContext,
        key: str,
        request: Request,
        source_id: str,
        command: schemas.TestConnectionCommand,
    ) -> dict[str, Any]:
        connection_test_id = new_id("connection_test")

        async def action() -> dict[str, Any]:
            source = await self.repo.source(ctx, source_id, lock=True)
            check_if_match(request, source.etag)
            observed_config = _int(command.observed_config_version)
            observed_credential = _int(command.observed_credential_version)
            if (observed_config, observed_credential) != (
                source.config_version,
                source.credential_version,
            ):
                raise VersionConflictError(code="OBSERVED_SOURCE_VERSION_STALE")
            now = _now()
            job_id = new_id("job")
            test_projection = {
                "connection_test_id": connection_test_id,
                "source_id": source_id,
                "status": "QUEUED",
                "observed_config_version": str(observed_config),
                "observed_credential_version": str(observed_credential),
                "job_id": job_id,
                "safe_error": None,
                "created_at": _iso(now),
                "updated_at": _iso(now),
            }
            job_projection = {
                "id": job_id,
                "type": "DATA_SOURCE_CONNECTION_TEST",
                "status": "QUEUED",
                "stage": "CONNECTIVITY_CHECK",
                "progress": {"completed": "0", "total": "1", "unit": "CHECK"},
                "resource_ref": {"resource_type": "DATA_SOURCE", "resource_id": source_id},
                "observed_versions": {
                    "config_version": str(observed_config),
                    "credential_version": str(observed_credential),
                },
                "result_ref": {"connection_test_id": connection_test_id},
                "safe_error": None,
                "etag": _job_etag(),
                "allowed_actions": ["VIEW"],
                "created_at": _iso(now),
                "updated_at": _iso(now),
            }
            test = models.ConnectionTest(
                connection_test_id=connection_test_id,
                source_id=source_id,
                job_id=job_id,
                status="QUEUED",
                observed_config_version=observed_config,
                observed_credential_version=observed_credential,
                projection=test_projection,
                created_at=now,
                updated_at=now,
                **_scope(ctx),
            )
            job = models.UploadJob(
                job_id=job_id,
                job_type="DATA_SOURCE_CONNECTION_TEST",
                status="QUEUED",
                resource_type="DATA_SOURCE",
                resource_id=source_id,
                projection=job_projection,
                created_at=now,
                updated_at=now,
                **_scope(ctx),
            )
            self.session.add_all((test, job))
            await self.session.flush()
            result = envelope(job_projection, ctx)
            result["job"] = _platform_job(job, ctx)
            return result

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="testDataSourceConnection",
            audit_event="ingest.source_connection_test.requested",
            event_type="ingest.source_connection_test.requested",
            target_type="ingest.data_source",
            target_id=source_id,
            action=action,
        )

    async def set_source_state(
        self,
        ctx: RequestContext,
        key: str,
        request: Request,
        source_id: str,
        command: schemas.SourceStateCommand,
        new_state: str,
    ) -> dict[str, Any]:
        operation_id = "enableDataSource" if new_state == "ENABLED" else "disableDataSource"
        audit_event = (
            "ingest.source.enabled" if new_state == "ENABLED" else "ingest.source.disabled"
        )

        async def action() -> dict[str, Any]:
            source = await self.repo.source(ctx, source_id, lock=True)
            check_if_match(request, source.etag)
            expected = str(_wire(command.expected_administrative_state))
            if expected != source.administrative_state:
                raise VersionConflictError(code="ADMINISTRATIVE_STATE_CHANGED")
            if source.administrative_state == new_state:
                raise VersionConflictError(code="SOURCE_ALREADY_IN_STATE")
            if new_state == "ENABLED" and source.projection["credential"]["state"] not in {
                "CONFIGURED",
                "NOT_REQUIRED",
            }:
                raise VersionConflictError(code="SOURCE_CREDENTIAL_NOT_READY")
            if new_state == "DISABLED":
                active = await self.session.scalar(
                    select(func.count())
                    .select_from(models.UploadSession)
                    .where(
                        models.UploadSession.source_id == source_id,
                        models.UploadSession.lifecycle_status.in_(ACTIVE_UPLOAD_STATES),
                    )
                )
                if active:
                    raise VersionConflictError(
                        code="ACTIVE_UPLOADS_BLOCK_DISABLE",
                        blocked_reasons=[
                            {
                                "code": "ACTIVE_UPLOADS",
                                "message": "Active uploads must finish first.",
                            }
                        ],
                    )
            source.administrative_state = new_state
            source.resource_version += 1
            source.updated_at = _now()
            projection = dict(source.projection)
            projection["administrative_state"] = new_state
            projection["etag"] = source.etag = _source_etag(source.resource_version)
            projection["updated_at"] = _iso(source.updated_at)
            projection["allowed_actions"] = (
                [
                    "VIEW",
                    "EDIT_CONFIGURATION",
                    "ROTATE_CREDENTIAL",
                    "TEST_CONNECTION",
                    "DISABLE",
                    "OPEN_UPLOADS",
                ]
                if new_state == "ENABLED"
                else [
                    "VIEW",
                    "EDIT_CONFIGURATION",
                    "ROTATE_CREDENTIAL",
                    "TEST_CONNECTION",
                    "ENABLE",
                ]
            )
            source.projection = projection
            await self.session.flush()
            return envelope(projection, ctx)

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id=operation_id,
            audit_event=audit_event,
            event_type=audit_event,
            target_type="ingest.data_source",
            target_id=source_id,
            action=action,
        )

    def _upload_projection(
        self,
        *,
        ctx: RequestContext,
        upload_id: str,
        source: models.DataSource,
        target_dataset_id: str | None,
        source_format: str,
        source_format_version: str | None,
        objects: list[Any],
        supersedes_upload_id: str | None = None,
    ) -> dict[str, Any]:
        now = _now()
        total_bytes = sum(_int(item.size_bytes) for item in objects)
        return {
            "upload_id": upload_id,
            "scope": _scope(ctx),
            "data_source": _source_ref(source),
            "target_dataset": (
                {"id": target_dataset_id, "name": target_dataset_id} if target_dataset_id else None
            ),
            "result": None,
            "supersedes_upload_id": supersedes_upload_id,
            "source_format": source_format,
            "source_format_version": source_format_version,
            "adapter_version": source.adapter_version,
            "lifecycle_status": "UPLOADING",
            "verification_status": "NOT_STARTED",
            "progress": {
                "expected_bytes": str(total_bytes),
                "confirmed_received_bytes": "0",
                "completed_parts": "0",
                "total_parts": None,
                "completed_objects": "0",
                "total_objects": str(len(objects)),
                "throughput_bytes_per_second": None,
                "estimated_remaining_seconds": None,
                "verification_stage": None,
            },
            "source_manifest": None,
            "latest_verification_run_id": None,
            "active_job_ids": [],
            "created_by": {"id": ctx.actor_id, "display_name": ctx.actor_id},
            "created_at": _iso(now),
            "updated_at": _iso(now),
            "etag": _upload_etag(1),
            "resource_version": "1",
            "allowed_actions": ["PAUSE", "CANCEL", "SUBMIT_MANIFEST"],
            "blocked_reasons": [],
        }

    def _short_authorization(self, upload_id: str) -> dict[str, Any]:
        if os.getenv("APP_ENV", "development").lower() in {"production", "prod"}:
            raise ServerError(
                code="UPLOAD_STS_POLICY_NOT_APPROVED",
                message=(
                    "Production upload authorization is disabled until the STS policy is approved."
                ),
            )
        issued = _now()
        expires = issued + timedelta(
            seconds=min(int(os.getenv("UPLOAD_AUTH_TTL_SECONDS", "900")), 900)
        )
        refresh = issued + (expires - issued) * 2 / 3
        return {
            "authorization_id": new_id("event"),
            "issued_at": _iso(issued),
            "expires_at": _iso(expires),
            "refresh_after": _iso(refresh),
            "oss_region": os.getenv("S3_REGION", "cn-shanghai"),
            "endpoint": os.getenv("S3_PUBLIC_ENDPOINT", "http://localhost:9000"),
            "bucket": os.getenv("S3_BUCKET", "platform-ingest"),
            "object_prefix": f"sessions/{upload_id}/",
            "credentials": {
                "access_key_id": "STS" + secrets.token_hex(8),
                "access_key_secret": secrets.token_urlsafe(24),
                "security_token": secrets.token_urlsafe(32),
            },
        }

    def _upload_policy(self, version: int) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "policy_version": str(version),
            "effective_until": _iso(_now() + timedelta(minutes=30)),
            "part_size_bytes": {
                "minimum": "5242880",
                "preferred": "67108864",
                "maximum": "5368709120",
            },
            "concurrency": {"minimum": 1, "preferred": 4, "maximum": 8},
            "retry": {
                "max_attempts_per_part": 5,
                "base_delay_ms": 250,
                "maximum_delay_ms": 10000,
                "jitter_ratio": 0.2,
                "retryable_http_statuses": [408, 429, 500, 502, 503, 504],
            },
            "checksums": {
                "part_algorithm": "CRC64_ECMA",
                "object_algorithm": "SHA256",
                "require_object_sha256_before_manifest": True,
            },
            "limits": {
                "max_object_count": "10000",
                "max_total_bytes": "10995116277760",
                "max_object_bytes": "1099511627776",
            },
        }

    async def _handoff_for_existing(
        self, row: models.UploadSession, *, include_policy: bool = True
    ) -> dict[str, Any]:
        objects = list(
            (
                await self.session.scalars(
                    select(models.UploadObject)
                    .where(models.UploadObject.upload_id == row.upload_id)
                    .order_by(models.UploadObject.upload_object_id)
                )
            ).all()
        )
        plans = []
        for item in objects:
            object_key = f"sessions/{row.upload_id}/{item.upload_object_id}"
            await self.object_storage.presign_put(
                os.getenv("S3_BUCKET", "platform-ingest"),
                object_key,
                expires_in=min(int(os.getenv("UPLOAD_AUTH_TTL_SECONDS", "900")), 900),
                content_type=item.projection.get("media_type"),
            )
            plans.append(
                {
                    "upload_object_id": item.upload_object_id,
                    "client_object_id": item.client_object_id,
                    "relative_path": item.normalized_relative_path,
                    "size_bytes": str(item.declared_size_bytes),
                    "multipart_upload_id": "mpu_" + secrets.token_hex(12),
                    "object_key": object_key,
                    "part_size_bytes": "67108864",
                    "confirmed_parts": [],
                }
            )
        return {
            "authorization": self._short_authorization(row.upload_id),
            "policy": self._upload_policy(row.upload_policy_version) if include_policy else None,
            "object_plans": plans,
        }

    async def _create_upload_rows(
        self,
        ctx: RequestContext,
        source: models.DataSource,
        target_dataset_id: str | None,
        source_format: str,
        source_format_version: str | None,
        declarations: list[Any],
        supersedes_upload_id: str | None = None,
    ) -> tuple[models.UploadSession, list[models.UploadObject], dict[str, Any]]:
        paths = [_wire(item.relative_path) for item in declarations]
        if len(set(paths)) != len(paths):
            raise ValidationError(code="DUPLICATE_RELATIVE_PATH")
        upload_id = new_id("upload")
        now = _now()
        projection = self._upload_projection(
            ctx=ctx,
            upload_id=upload_id,
            source=source,
            target_dataset_id=target_dataset_id,
            source_format=source_format,
            source_format_version=source_format_version,
            objects=declarations,
            supersedes_upload_id=supersedes_upload_id,
        )
        row = models.UploadSession(
            upload_id=upload_id,
            source_id=source.source_id,
            target_dataset_id=target_dataset_id,
            supersedes_upload_id=supersedes_upload_id,
            lifecycle_status="UPLOADING",
            verification_status="NOT_STARTED",
            source_config_version=source.config_version,
            source_credential_version=source.credential_version,
            upload_policy_version=source.upload_policy_version,
            resource_version=1,
            etag=projection["etag"],
            projection=projection,
            created_at=now,
            updated_at=now,
            **_scope(ctx),
        )
        object_rows: list[models.UploadObject] = []
        object_plans: list[dict[str, Any]] = []
        for declaration, relative_path in zip(declarations, paths, strict=True):
            object_id = new_id("object")
            size = _int(declaration.size_bytes)
            declared_sha = (
                _wire(declaration.declared_sha256) if declaration.declared_sha256 else None
            )
            object_projection = {
                "object_id": object_id,
                "relative_path": relative_path,
                "source_role": "DATA",
                "media_type": declaration.media_type,
                "size_bytes": str(size),
                "multipart_status": "DECLARED",
                "completed_parts": "0",
                "total_parts": None,
                "etag": None,
                "declared_sha256": declared_sha,
                "verified_sha256": None,
                "checksum_status": "NOT_CHECKED",
                "verification_status": "NOT_STARTED",
                "resource_version": "1",
                "updated_at": _iso(now),
                "allowed_actions": ["UPLOAD", "VIEW_PARTS"],
                "blocked_reasons": [],
            }
            object_row = models.UploadObject(
                upload_object_id=object_id,
                upload_id=upload_id,
                client_object_id=_wire(declaration.client_object_id),
                normalized_relative_path=relative_path,
                declared_size_bytes=size,
                declared_sha256=declared_sha,
                multipart_status="DECLARED",
                resource_version=1,
                projection=object_projection,
                created_at=now,
                updated_at=now,
            )
            object_rows.append(object_row)
            # The signed URL remains request-local and never enters a projection,
            # idempotency row, event, audit record or log.
            await self.object_storage.presign_put(
                os.getenv("S3_BUCKET", "platform-ingest"),
                f"sessions/{upload_id}/{object_id}",
                expires_in=min(int(os.getenv("UPLOAD_AUTH_TTL_SECONDS", "900")), 900),
                content_type=declaration.media_type,
            )
            object_plans.append(
                {
                    "upload_object_id": object_id,
                    "client_object_id": _wire(declaration.client_object_id),
                    "relative_path": relative_path,
                    "size_bytes": str(size),
                    "multipart_upload_id": "mpu_" + secrets.token_hex(12),
                    "object_key": f"sessions/{upload_id}/{object_id}",
                    "part_size_bytes": "67108864",
                    "confirmed_parts": [],
                }
            )
        self.session.add(row)
        self.session.add_all(object_rows)
        await self.session.flush()
        handoff = {
            "authorization": self._short_authorization(upload_id),
            "policy": self._upload_policy(source.upload_policy_version),
            "object_plans": object_plans,
        }
        return row, object_rows, handoff

    async def create_upload(
        self, ctx: RequestContext, key: str, command: schemas.CreateUploadSessionCommand
    ) -> dict[str, Any]:
        target_id = new_id("upload")

        async def action() -> dict[str, Any]:
            source_id = _wire(command.data_source_id)
            source = await self.repo.source(ctx, source_id, lock=True)
            if source.administrative_state != "ENABLED":
                raise VersionConflictError(code="SOURCE_DISABLED")
            expected = command.expected_source_versions
            if (
                _int(expected.configuration_version),
                _int(expected.credential_version),
                _int(expected.upload_policy_version),
            ) != (source.config_version, source.credential_version, source.upload_policy_version):
                raise VersionConflictError(code="SOURCE_VERSION_CHANGED")
            row, _objects, handoff = await self._create_upload_rows(
                ctx,
                source,
                _wire(command.target_dataset_id) if command.target_dataset_id else None,
                command.source_format,
                command.source_format_version,
                command.objects,
            )
            nonlocal target_id
            target_id = row.upload_id
            result = envelope({"upload_session": row.projection, "secret": handoff}, ctx)
            return result

        async def rehydrate(result: dict[str, Any]) -> dict[str, Any]:
            stable = deepcopy(result)
            upload_id = stable["data"]["upload_session"]["upload_id"]
            row = await self.repo.upload(ctx, upload_id)
            stable["data"]["secret"] = await self._handoff_for_existing(row)
            return stable

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="createUploadSession",
            audit_event="upload.session.created",
            event_type="upload.session.created",
            target_type="ingest.upload_session",
            target_id=lambda: target_id,
            action=action,
            rehydrate=rehydrate,
        )

    async def list_uploads(self, ctx: RequestContext, params: CursorParams) -> dict[str, Any]:
        stmt = select(models.UploadSession).where(scope_predicate(models.UploadSession, ctx))
        stmt = apply_keyset(
            stmt, params, models.UploadSession.updated_at, models.UploadSession.upload_id
        )
        rows = list((await self.session.scalars(stmt)).all())
        rows = restore_keyset_order(rows, params)
        page = build_page(rows, params, ("updated_at", "upload_id"))
        page["items"] = [row.projection for row in page["items"]]
        page.update(scope=_scope(ctx), request_id=ctx.request_id, contract_version=CONTRACT_VERSION)
        return page

    async def summarize_uploads(self, ctx: RequestContext) -> dict[str, Any]:
        values = list(
            (
                await self.session.scalars(
                    select(models.UploadSession.lifecycle_status).where(
                        scope_predicate(models.UploadSession, ctx)
                    )
                )
            ).all()
        )
        data = {
            "uploading_count": str(
                sum(x in {"AUTHORIZING", "UPLOADING", "PAUSED", "FINALIZING"} for x in values)
            ),
            "verifying_count": str(sum(x in {"PENDING_VERIFY", "VERIFYING"} for x in values)),
            "available_today_count": str(sum(x == "AVAILABLE" for x in values)),
            "failed_count": str(sum(x in {"FAILED", "QUARANTINED"} for x in values)),
            "uploaded_bytes_today": "0",
            "as_of": _iso(),
            "timezone": "UTC",
            "definition_version": "1",
        }
        return envelope(data, ctx)

    async def creation_options(self, ctx: RequestContext) -> dict[str, Any]:
        sources = list(
            (
                await self.session.scalars(
                    select(models.DataSource)
                    .where(scope_predicate(models.DataSource, ctx))
                    .order_by(models.DataSource.source_id)
                )
            ).all()
        )
        options = []
        for source in sources:
            allowed = source.administrative_state == "ENABLED"
            item = _source_ref(source)
            item.update(
                allowed=allowed,
                blocked_reasons=[]
                if allowed
                else [{"code": "SOURCE_DISABLED", "message": "The source is currently disabled."}],
            )
            options.append(item)
        return envelope(
            {
                "data_sources": options,
                "datasets": [],
                "formats": [
                    {
                        "code": "LEROBOT_V2",
                        "version": "2.1",
                        "adapter_version": "adapter-v1",
                        "allowed_extensions": ["parquet", "mp4", "jsonl"],
                    }
                ],
                "policy_summaries": [
                    {
                        "policy_version": "1",
                        "label": "Standard browser upload",
                        "max_object_count": "10000",
                        "max_total_bytes": "10995116277760",
                        "default_part_size_bytes": "67108864",
                        "max_concurrency": 8,
                    }
                ],
                "allowed_actions": ["CREATE"],
                "blocked_reasons": [],
            },
            ctx,
        )

    async def _append_upload_event(
        self,
        ctx: RequestContext,
        row: models.UploadSession,
        event_type: str,
        from_state: str | None,
        to_state: str | None,
        *,
        level: str = "INFO",
        job_id: str | None = None,
    ) -> None:
        now = _now()
        event_id = new_id("event")
        projection = {
            "event_id": event_id,
            "event_type": event_type,
            "event_level": level,
            "occurred_at": _iso(now),
            "actor": {"kind": "USER", "id": ctx.actor_id, "display_name": ctx.actor_id},
            "from_state": from_state,
            "to_state": to_state,
            "resource_ref": {
                "type": "UPLOAD_SESSION",
                "id": row.upload_id,
                "version": str(row.resource_version),
            },
            "job_id": job_id,
            "request_id": ctx.request_id,
            "safe_payload": {
                "previous_status": from_state,
                "new_status": to_state,
                "job_status": "QUEUED" if job_id else None,
            },
        }
        self.session.add(
            models.UploadEvent(
                event_id=event_id,
                upload_id=row.upload_id,
                event_type=event_type,
                event_level=level,
                request_id=ctx.request_id,
                projection=projection,
                occurred_at=now,
                **_scope(ctx),
            )
        )

    async def _transition(
        self,
        ctx: RequestContext,
        row: models.UploadSession,
        new_state: str,
        event_type: str,
        *,
        job_id: str | None = None,
    ) -> None:
        previous = row.lifecycle_status
        row.lifecycle_status = new_state
        row.resource_version += 1
        row.updated_at = _now()
        row.etag = _upload_etag(row.resource_version)
        projection = dict(row.projection)
        projection.update(
            lifecycle_status=new_state,
            resource_version=str(row.resource_version),
            etag=row.etag,
            updated_at=_iso(row.updated_at),
        )
        if new_state == "PAUSED":
            projection["allowed_actions"] = ["RESUME", "CANCEL"]
        elif new_state == "UPLOADING":
            projection["allowed_actions"] = ["PAUSE", "CANCEL", "SUBMIT_MANIFEST"]
        elif new_state in {"PENDING_VERIFY", "VERIFYING", "CANCELLING"}:
            projection["allowed_actions"] = ["VIEW"]
        row.projection = projection
        await self._append_upload_event(ctx, row, event_type, previous, new_state, job_id=job_id)

    async def bootstrap(self, ctx: RequestContext, upload_id: str) -> dict[str, Any]:
        upload = await self.repo.upload(ctx, upload_id)
        objects = list(
            (
                await self.session.scalars(
                    select(models.UploadObject)
                    .where(models.UploadObject.upload_id == upload_id)
                    .order_by(models.UploadObject.updated_at, models.UploadObject.upload_object_id)
                )
            ).all()
        )
        run = None
        if upload.current_verification_run_id:
            run = await self.session.get(models.VerificationRun, upload.current_verification_run_id)
        quarantine = None
        if upload.current_quarantine_id:
            quarantine = await self.session.get(models.Quarantine, upload.current_quarantine_id)
        jobs = list(
            (
                await self.session.scalars(
                    select(models.UploadJob).where(
                        models.UploadJob.resource_id.in_(
                            (upload_id, upload.current_verification_run_id or "")
                        ),
                        models.UploadJob.status.in_(("QUEUED", "RUNNING", "CANCELLING")),
                    )
                )
            ).all()
        )
        return envelope(
            {
                "session": upload.projection,
                "objects": [item.projection for item in objects],
                "latest_verification_run": run.projection if run else None,
                "latest_quarantine": quarantine.projection if quarantine else None,
                "active_jobs": [job.projection for job in jobs],
            },
            ctx,
        )

    async def renew_authorization(
        self,
        ctx: RequestContext,
        key: str,
        request: Request,
        upload_id: str,
        command: schemas.RenewUploadAuthorizationCommand,
    ) -> dict[str, Any]:
        async def action() -> dict[str, Any]:
            row = await self.repo.upload(ctx, upload_id, lock=True)
            check_if_match(request, row.etag)
            if _int(command.observed_resource_version) != row.resource_version:
                raise VersionConflictError(code="UPLOAD_VERSION_CHANGED")
            if row.lifecycle_status not in {"AUTHORIZING", "UPLOADING", "PAUSED"}:
                raise VersionConflictError(code="UPLOAD_AUTHORIZATION_NOT_RENEWABLE")
            authorization = self._short_authorization(upload_id)
            await self._append_upload_event(
                ctx, row, "upload.authorization.renewed", row.lifecycle_status, row.lifecycle_status
            )
            return envelope(
                {
                    "upload_session": row.projection,
                    "secret": {
                        "authorization": authorization,
                        "replaces_authorization_id": _wire(command.expected_authorization_id),
                        "policy": None,
                    },
                },
                ctx,
            )

        async def rehydrate(result: dict[str, Any]) -> dict[str, Any]:
            stable = deepcopy(result)
            stable["data"]["secret"] = {
                "authorization": self._short_authorization(upload_id),
                "replaces_authorization_id": _wire(command.expected_authorization_id),
                "policy": None,
            }
            return stable

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="renewUploadAuthorization",
            audit_event="upload.transfer.retry_requested",
            event_type="upload.authorization.renewed",
            target_type="ingest.upload_session",
            target_id=upload_id,
            action=action,
            rehydrate=rehydrate,
        )

    async def pause_upload(
        self,
        ctx: RequestContext,
        key: str,
        request: Request,
        upload_id: str,
        command: schemas.UploadStateCommand,
    ) -> dict[str, Any]:
        async def action() -> dict[str, Any]:
            row = await self.repo.upload(ctx, upload_id, lock=True)
            check_if_match(request, row.etag)
            expected = str(_wire(command.expected_lifecycle_status))
            if row.lifecycle_status != expected or expected not in {"AUTHORIZING", "UPLOADING"}:
                raise VersionConflictError(code="UPLOAD_STATE_CHANGED")
            await self._transition(ctx, row, "PAUSED", "upload.session.paused")
            await self.session.flush()
            return envelope(row.projection, ctx)

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="pauseUploadSession",
            audit_event="upload.session.paused",
            event_type="upload.session.paused",
            target_type="ingest.upload_session",
            target_id=upload_id,
            action=action,
        )

    async def resume_upload(
        self,
        ctx: RequestContext,
        key: str,
        request: Request,
        upload_id: str,
        command: schemas.ResumeUploadCommand,
    ) -> dict[str, Any]:
        async def action() -> dict[str, Any]:
            row = await self.repo.upload(ctx, upload_id, lock=True)
            check_if_match(request, row.etag)
            if (
                str(_wire(command.expected_lifecycle_status)) != "PAUSED"
                or row.lifecycle_status != "PAUSED"
            ):
                raise VersionConflictError(code="UPLOAD_NOT_PAUSED")
            await self._transition(ctx, row, "UPLOADING", "upload.session.resumed")
            handoff = await self._handoff_for_existing(row)
            await self.session.flush()
            return envelope({"upload_session": row.projection, "secret": handoff}, ctx)

        async def rehydrate(result: dict[str, Any]) -> dict[str, Any]:
            stable = deepcopy(result)
            row = await self.repo.upload(ctx, upload_id)
            stable["data"]["secret"] = await self._handoff_for_existing(row)
            return stable

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="resumeUploadSession",
            audit_event="upload.session.resumed",
            event_type="upload.session.resumed",
            target_type="ingest.upload_session",
            target_id=upload_id,
            action=action,
            rehydrate=rehydrate,
        )

    async def retry_parts(
        self,
        ctx: RequestContext,
        key: str,
        request: Request,
        upload_id: str,
        command: schemas.RetryUploadPartsCommand,
    ) -> dict[str, Any]:
        async def action() -> dict[str, Any]:
            row = await self.repo.upload(ctx, upload_id, lock=True)
            check_if_match(request, row.etag)
            if row.lifecycle_status not in {"UPLOADING", "PAUSED"}:
                raise VersionConflictError(code="UPLOAD_PART_RETRY_NOT_ALLOWED")
            plan = []
            now = _now()
            for requested in command.requested_parts:
                object_id = _wire(requested.upload_object_id)
                object_row = await self.repo.object(upload_id, object_id)
                part_number = _int(requested.part_number)
                observed_attempt = _int(requested.observed_attempt)
                latest = await self.session.scalar(
                    select(func.max(models.UploadPartAttempt.attempt)).where(
                        models.UploadPartAttempt.upload_object_id == object_id,
                        models.UploadPartAttempt.part_number == part_number,
                    )
                )
                if int(latest or 0) != observed_attempt:
                    raise VersionConflictError(code="UPLOAD_PART_ATTEMPT_CHANGED")
                offset = max(0, (part_number - 1) * 67108864)
                size = min(67108864, max(0, object_row.declared_size_bytes - offset))
                next_attempt = observed_attempt + 1
                projection = {
                    "part_id": new_id("part"),
                    "object_id": object_id,
                    "part_number": str(part_number),
                    "attempt_count": str(next_attempt),
                    "offset_bytes": str(offset),
                    "size_bytes": str(size),
                    "status": "PLANNED",
                    "etag": None,
                    "checksum": None,
                    "last_error": None,
                    "updated_at": _iso(now),
                    "resource_version": str(next_attempt),
                }
                self.session.add(
                    models.UploadPartAttempt(
                        part_attempt_id=projection["part_id"],
                        upload_object_id=object_id,
                        part_number=part_number,
                        attempt=next_attempt,
                        offset_bytes=offset,
                        size_bytes=size,
                        status="PLANNED",
                        projection=projection,
                        observed_at=now,
                    )
                )
                plan.append(
                    {
                        "upload_object_id": object_id,
                        "part_number": str(part_number),
                        "offset_bytes": str(offset),
                        "size_bytes": str(size),
                        "next_attempt": str(next_attempt),
                        "not_before": _iso(now),
                        "checksum_algorithm": "CRC64_ECMA",
                    }
                )
            await self._append_upload_event(
                ctx,
                row,
                "upload.transfer.retry_requested",
                row.lifecycle_status,
                row.lifecycle_status,
            )
            await self.session.flush()
            return envelope(
                {
                    "session": row.projection,
                    "retry_plan": plan,
                    "secret": {"authorization": self._short_authorization(upload_id)},
                },
                ctx,
            )

        async def rehydrate(result: dict[str, Any]) -> dict[str, Any]:
            stable = deepcopy(result)
            stable["data"]["secret"] = {"authorization": self._short_authorization(upload_id)}
            return stable

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="retryUploadParts",
            audit_event="upload.transfer.retry_requested",
            event_type="upload.transfer.retry_requested",
            target_type="ingest.upload_session",
            target_id=upload_id,
            action=action,
            rehydrate=rehydrate,
        )

    async def submit_manifest(
        self,
        ctx: RequestContext,
        key: str,
        request: Request,
        upload_id: str,
        command: schemas.SubmitUploadManifestCommand,
    ) -> dict[str, Any]:
        async def action() -> dict[str, Any]:
            row = await self.repo.upload(ctx, upload_id, lock=True)
            check_if_match(request, row.etag)
            if row.lifecycle_status not in {"UPLOADING", "FINALIZING"}:
                raise VersionConflictError(code="UPLOAD_FINALIZE_NOT_ALLOWED")
            if row.current_manifest_id:
                raise VersionConflictError(code="SOURCE_MANIFEST_ALREADY_SUBMITTED")
            manifest_body = _wire(command.manifest)
            canonical = json.dumps(
                manifest_body,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
            actual_manifest_sha = hashlib.sha256(canonical).hexdigest()
            supplied_manifest_sha = _wire(command.manifest_sha256)
            if supplied_manifest_sha != actual_manifest_sha:
                raise ValidationError(
                    code="MANIFEST_SHA256_MISMATCH",
                    field_errors=[
                        {
                            "path": "/manifest_sha256",
                            "code": "DIGEST_MISMATCH",
                            "message": (
                                "The digest does not match the RFC 8785 canonical Manifest body."
                            ),
                        }
                    ],
                )
            object_rows = {
                item.upload_object_id: item
                for item in (
                    await self.session.scalars(
                        select(models.UploadObject).where(
                            models.UploadObject.upload_id == upload_id
                        )
                    )
                ).all()
            }
            object_facts = []
            seen_paths: set[str] = set()
            for item in manifest_body["objects"]:
                object_id = item["upload_object_id"]
                declared = object_rows.get(object_id)
                if declared is None:
                    raise ValidationError(code="MANIFEST_OBJECT_NOT_DECLARED")
                if (
                    item["relative_path"] != declared.normalized_relative_path
                    or int(item["size_bytes"]) != declared.declared_size_bytes
                ):
                    raise ValidationError(code="MANIFEST_OBJECT_FACT_MISMATCH")
                if item["relative_path"] in seen_paths:
                    raise ValidationError(code="DUPLICATE_RELATIVE_PATH")
                seen_paths.add(item["relative_path"])
                storage_fact = await self.object_storage.head(
                    os.getenv("S3_BUCKET", "platform-ingest"),
                    f"sessions/{upload_id}/{object_id}",
                )
                if storage_fact is None:
                    raise VersionConflictError(code="UPLOAD_OBJECT_NOT_CONFIRMED")
                if int(storage_fact["size_bytes"]) != declared.declared_size_bytes:
                    raise ValidationError(code="UPLOAD_OBJECT_SIZE_MISMATCH")
                declared.provider_etag = str(storage_fact.get("etag") or "") or None
                object_projection = dict(declared.projection)
                object_projection["etag"] = declared.provider_etag
                declared.projection = object_projection
                object_facts.append(
                    {
                        "upload_object_id": object_id,
                        "relative_path": item["relative_path"],
                        "size_bytes": item["size_bytes"],
                        "sha256": item["sha256"],
                    }
                )
            object_set_hash = hashlib.sha256(
                json.dumps(
                    sorted(object_facts, key=lambda value: value["upload_object_id"]),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode()
            ).hexdigest()
            if _wire(command.expected_object_set_hash) != object_set_hash:
                raise VersionConflictError(code="OBJECT_SET_HASH_MISMATCH")
            now = _now()
            manifest_id = new_id("manifest")
            job_id = new_id("job")
            run_id = new_id("verification_run")
            declared_bytes = sum(int(item["size_bytes"]) for item in manifest_body["objects"])
            manifest_projection = {
                "manifest_id": manifest_id,
                "revision": "1",
                "schema_version": manifest_body["schema_version"],
                "canonicalization": manifest_body["canonicalization"],
                "sha256": actual_manifest_sha,
                "object_set_hash": object_set_hash,
                "source_format": manifest_body["source_format"],
                "source_format_version": manifest_body["source_format_version"],
                "adapter_version": row.projection["adapter_version"],
                "declared_object_count": str(len(manifest_body["objects"])),
                "declared_bytes": str(declared_bytes),
                "submitted_by": {"id": ctx.actor_id, "display_name": ctx.actor_id},
                "submitted_at": _iso(now),
                "status": "PARSED",
                "schema_issue_counts": {"error": "0", "warning": "0", "info": "0"},
            }
            self.session.add(
                models.SourceManifest(
                    manifest_id=manifest_id,
                    upload_id=upload_id,
                    revision=1,
                    canonical_bytes=canonical,
                    manifest_sha256=actual_manifest_sha,
                    object_set_hash=object_set_hash,
                    status="PARSED",
                    projection=manifest_projection,
                    submitted_at=now,
                )
            )
            root_node_id = new_id("manifest_node")
            self.session.add(
                models.ManifestNode(
                    node_id=root_node_id,
                    manifest_id=manifest_id,
                    parent_node_id=None,
                    json_pointer="/",
                    projection={
                        "node_id": root_node_id,
                        "parent_node_id": None,
                        "json_pointer": "/",
                        "node_type": "OBJECT",
                        "display_key": "manifest",
                        "value_summary": None,
                        "child_count": str(len(manifest_body)),
                        "issue_counts": {"error": "0", "warning": "0", "info": "0"},
                        "has_more_children": False,
                    },
                )
            )
            stages = [
                {
                    "code": code,
                    "status": "PENDING",
                    "started_at": None,
                    "finished_at": None,
                    "job_id": job_id,
                    "finding_count": "0",
                    "retryable": False,
                    "skip_reason": None,
                }
                for code in VERIFICATION_STAGES
            ]
            run_projection = {
                "verification_run_id": run_id,
                "supersedes_run_id": None,
                "object_set_hash": object_set_hash,
                "manifest_sha256": actual_manifest_sha,
                "adapter_version": row.projection["adapter_version"],
                "schema_ref": None,
                "status": "QUEUED",
                "stages": stages,
                "finding_counts": {"info": "0", "warning": "0", "error": "0"},
                "job_id": job_id,
                "started_at": None,
                "finished_at": None,
                "created_at": _iso(now),
                "resource_version": "1",
            }
            run = models.VerificationRun(
                verification_run_id=run_id,
                upload_id=upload_id,
                supersedes_run_id=None,
                job_id=job_id,
                object_set_hash=object_set_hash,
                manifest_sha256=actual_manifest_sha,
                status="QUEUED",
                resource_version=1,
                projection=run_projection,
                created_at=now,
            )
            self.session.add(run)
            for stage in stages:
                self.session.add(
                    models.VerificationStage(
                        stage_transition_id=new_id("verification_stage"),
                        verification_run_id=run_id,
                        stage_code=stage["code"],
                        transition_no=1,
                        status="PENDING",
                        projection=stage,
                        occurred_at=now,
                    )
                )
            job_projection = {
                "id": job_id,
                "type": "UPLOAD_VERIFICATION",
                "status": "QUEUED",
                "stage": "MANIFEST_SCHEMA",
                "progress": {"completed": "0", "total": "6", "unit": "STAGE"},
                "resource_ref": {"resource_type": "VERIFICATION_RUN", "resource_id": run_id},
                "observed_versions": None,
                "result_ref": {"upload_id": upload_id, "verification_run_id": run_id},
                "safe_error": None,
                "etag": _job_etag(),
                "allowed_actions": ["VIEW"],
                "created_at": _iso(now),
                "updated_at": _iso(now),
            }
            job = models.UploadJob(
                job_id=job_id,
                job_type="UPLOAD_VERIFICATION",
                status="QUEUED",
                resource_type="VERIFICATION_RUN",
                resource_id=run_id,
                projection=job_projection,
                created_at=now,
                updated_at=now,
                **_scope(ctx),
            )
            self.session.add(job)
            row.current_manifest_id = manifest_id
            row.current_verification_run_id = run_id
            row.object_set_hash = object_set_hash
            row.manifest_sha256 = actual_manifest_sha
            projection = dict(row.projection)
            projection["source_manifest"] = manifest_projection
            projection["latest_verification_run_id"] = run_id
            projection["verification_status"] = "QUEUED"
            projection["active_job_ids"] = [job_id]
            row.verification_status = "QUEUED"
            row.projection = projection
            await self._transition(
                ctx, row, "PENDING_VERIFY", "upload.manifest.submitted", job_id=job_id
            )
            await self.session.flush()
            result = envelope({"verification_run": run_projection}, ctx)
            result["job"] = _platform_job(job, ctx)
            return result

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="submitUploadManifest",
            audit_event="upload.manifest.submitted",
            event_type="upload.manifest.submitted",
            target_type="ingest.upload_session",
            target_id=upload_id,
            action=action,
        )

    async def retry_verification(
        self,
        ctx: RequestContext,
        key: str,
        request: Request,
        upload_id: str,
        command: schemas.RetryVerificationCommand,
    ) -> dict[str, Any]:
        async def action() -> dict[str, Any]:
            row = await self.repo.upload(ctx, upload_id, lock=True)
            check_if_match(request, row.etag)
            if row.lifecycle_status != "QUARANTINED":
                raise VersionConflictError(code="UPLOAD_NOT_QUARANTINED")
            quarantine = await self.repo.current_quarantine(upload_id, lock=True)
            if quarantine is None or quarantine.disposition != "OPEN":
                raise VersionConflictError(code="OPEN_QUARANTINE_REQUIRED")
            failed_run_id = _wire(command.failed_verification_run_id)
            if failed_run_id != row.current_verification_run_id:
                raise VersionConflictError(code="VERIFICATION_RUN_CHANGED")
            if (
                _wire(command.expected_object_set_hash) != row.object_set_hash
                or _wire(command.expected_manifest_sha256) != row.manifest_sha256
            ):
                raise VersionConflictError(code="IMMUTABLE_UPLOAD_FACT_CHANGED")
            now = _now()
            run_id = new_id("verification_run")
            job_id = new_id("job")
            stages = [
                {
                    "code": code,
                    "status": "PENDING",
                    "started_at": None,
                    "finished_at": None,
                    "job_id": job_id,
                    "finding_count": "0",
                    "retryable": False,
                    "skip_reason": None,
                }
                for code in VERIFICATION_STAGES
            ]
            run_projection = {
                "verification_run_id": run_id,
                "supersedes_run_id": failed_run_id,
                "object_set_hash": row.object_set_hash,
                "manifest_sha256": row.manifest_sha256,
                "adapter_version": row.projection["adapter_version"],
                "schema_ref": None,
                "status": "QUEUED",
                "stages": stages,
                "finding_counts": {"info": "0", "warning": "0", "error": "0"},
                "job_id": job_id,
                "started_at": None,
                "finished_at": None,
                "created_at": _iso(now),
                "resource_version": "1",
            }
            run = models.VerificationRun(
                verification_run_id=run_id,
                upload_id=upload_id,
                supersedes_run_id=failed_run_id,
                job_id=job_id,
                object_set_hash=row.object_set_hash or "",
                manifest_sha256=row.manifest_sha256 or "",
                status="QUEUED",
                resource_version=1,
                projection=run_projection,
                created_at=now,
            )
            job_projection = {
                "id": job_id,
                "type": "UPLOAD_VERIFICATION",
                "status": "QUEUED",
                "stage": "MANIFEST_SCHEMA",
                "progress": {"completed": "0", "total": "6", "unit": "STAGE"},
                "resource_ref": {"resource_type": "VERIFICATION_RUN", "resource_id": run_id},
                "observed_versions": None,
                "result_ref": {"upload_id": upload_id, "verification_run_id": run_id},
                "safe_error": None,
                "etag": _job_etag(),
                "allowed_actions": ["VIEW"],
                "created_at": _iso(now),
                "updated_at": _iso(now),
            }
            job = models.UploadJob(
                job_id=job_id,
                job_type="UPLOAD_VERIFICATION",
                status="QUEUED",
                resource_type="VERIFICATION_RUN",
                resource_id=run_id,
                projection=job_projection,
                created_at=now,
                updated_at=now,
                **_scope(ctx),
            )
            self.session.add_all((run, job))
            for stage in stages:
                self.session.add(
                    models.VerificationStage(
                        stage_transition_id=new_id("verification_stage"),
                        verification_run_id=run_id,
                        stage_code=stage["code"],
                        transition_no=1,
                        status="PENDING",
                        projection=stage,
                        occurred_at=now,
                    )
                )
            quarantine.disposition = "REVERIFY_REQUESTED"
            quarantine.resource_version += 1
            q_projection = dict(quarantine.projection)
            q_projection["disposition"] = "REVERIFY_REQUESTED"
            quarantine.projection = q_projection
            row.current_verification_run_id = run_id
            projection = dict(row.projection)
            projection["latest_verification_run_id"] = run_id
            projection["verification_status"] = "QUEUED"
            projection["active_job_ids"] = [job_id]
            row.verification_status = "QUEUED"
            row.projection = projection
            await self._transition(
                ctx, row, "PENDING_VERIFY", "upload.verification.retry_requested", job_id=job_id
            )
            await self.session.flush()
            result = envelope({"verification_run": run_projection}, ctx)
            result["job"] = _platform_job(job, ctx)
            return result

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="retryUploadVerification",
            audit_event="upload.verification.retry_requested",
            event_type="upload.verification.retry_requested",
            target_type="ingest.upload_session",
            target_id=upload_id,
            action=action,
        )

    async def create_replacement(
        self,
        ctx: RequestContext,
        key: str,
        request: Request,
        upload_id: str,
        command: schemas.CreateReplacementUploadCommand,
    ) -> dict[str, Any]:
        replacement_id = "pending"

        async def action() -> dict[str, Any]:
            original = await self.repo.upload(ctx, upload_id, lock=True)
            check_if_match(request, original.etag)
            if original.lifecycle_status != "QUARANTINED":
                raise VersionConflictError(code="UPLOAD_NOT_QUARANTINED")
            quarantine = await self.repo.current_quarantine(upload_id, lock=True)
            if quarantine is None or quarantine.disposition != "OPEN":
                raise VersionConflictError(code="OPEN_QUARANTINE_REQUIRED")
            source = await self.repo.source(ctx, original.source_id, lock=True)
            if source.administrative_state != "ENABLED":
                raise VersionConflictError(code="SOURCE_DISABLED")
            row, _objects, handoff = await self._create_upload_rows(
                ctx,
                source,
                _wire(command.target_dataset_id) if command.target_dataset_id else None,
                original.projection["source_format"],
                original.projection["source_format_version"],
                command.objects,
                supersedes_upload_id=upload_id,
            )
            nonlocal replacement_id
            replacement_id = row.upload_id
            quarantine.disposition = "REPLACED"
            quarantine.resource_version += 1
            q_projection = dict(quarantine.projection)
            q_projection["disposition"] = "REPLACED"
            quarantine.projection = q_projection
            await self._append_upload_event(
                ctx, row, "upload.session.replacement_created", None, "UPLOADING"
            )
            await self.session.flush()
            return envelope({"upload_session": row.projection, "secret": handoff}, ctx)

        async def rehydrate(result: dict[str, Any]) -> dict[str, Any]:
            stable = deepcopy(result)
            replacement_upload_id = stable["data"]["upload_session"]["upload_id"]
            row = await self.repo.upload(ctx, replacement_upload_id)
            stable["data"]["secret"] = await self._handoff_for_existing(row)
            return stable

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="createReplacementUpload",
            audit_event="upload.session.replacement_created",
            event_type="upload.session.replacement_created",
            target_type="ingest.upload_session",
            target_id=lambda: replacement_id,
            action=action,
            rehydrate=rehydrate,
        )

    async def cancel_upload(
        self,
        ctx: RequestContext,
        key: str,
        request: Request,
        upload_id: str,
        command: schemas.CancelUploadCommand,
    ) -> dict[str, Any]:
        async def action() -> dict[str, Any]:
            row = await self.repo.upload(ctx, upload_id, lock=True)
            check_if_match(request, row.etag)
            state_command = command.root
            expected = str(_wire(state_command.expected_lifecycle_status))
            if expected != row.lifecycle_status:
                raise VersionConflictError(code="UPLOAD_STATE_CHANGED")
            if row.lifecycle_status in {"AVAILABLE", "CANCELLED", "CANCELLING"}:
                raise VersionConflictError(code="UPLOAD_CANCEL_NOT_ALLOWED")
            quarantine = await self.repo.current_quarantine(upload_id, lock=True)
            if quarantine and quarantine.disposition == "REVERIFY_REQUESTED":
                raise VersionConflictError(code="ACTIVE_REVERIFICATION_BLOCKS_CANCEL")
            now = _now()
            job_id = new_id("job")
            job_projection = {
                "id": job_id,
                "type": "UPLOAD_CANCEL",
                "status": "QUEUED",
                "stage": "CANCELLING",
                "progress": {"completed": "0", "total": "1", "unit": "SESSION"},
                "resource_ref": {"resource_type": "UPLOAD_SESSION", "resource_id": upload_id},
                "observed_versions": None,
                "result_ref": {"upload_id": upload_id},
                "safe_error": None,
                "etag": _job_etag(),
                "allowed_actions": ["VIEW"],
                "created_at": _iso(now),
                "updated_at": _iso(now),
            }
            job = models.UploadJob(
                job_id=job_id,
                job_type="UPLOAD_CANCEL",
                status="QUEUED",
                resource_type="UPLOAD_SESSION",
                resource_id=upload_id,
                projection=job_projection,
                created_at=now,
                updated_at=now,
                **_scope(ctx),
            )
            self.session.add(job)
            projection = dict(row.projection)
            projection["active_job_ids"] = [job_id]
            row.projection = projection
            await self._transition(
                ctx, row, "CANCELLING", "upload.session.cancel_requested", job_id=job_id
            )
            await self.session.flush()
            result = envelope(job_projection, ctx)
            result["job"] = _platform_job(job, ctx)
            return result

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="cancelUploadSession",
            audit_event="upload.session.cancel_requested",
            event_type="upload.session.cancel_requested",
            target_type="ingest.upload_session",
            target_id=upload_id,
            action=action,
        )

    async def _projection_page(
        self,
        ctx: RequestContext,
        params: CursorParams,
        statement: Any,
        primary: Any,
        identity: Any,
    ) -> dict[str, Any]:
        statement = apply_keyset(statement, params, primary, identity)
        rows = list((await self.session.scalars(statement)).all())
        rows = restore_keyset_order(rows, params)
        page = build_page(rows, params, (primary.key, identity.key))
        page["items"] = [row.projection for row in page["items"]]
        page.update(scope=_scope(ctx), request_id=ctx.request_id, contract_version=CONTRACT_VERSION)
        return page

    async def list_objects(
        self, ctx: RequestContext, upload_id: str, params: CursorParams
    ) -> dict[str, Any]:
        await self.repo.upload(ctx, upload_id)
        return await self._projection_page(
            ctx,
            params,
            select(models.UploadObject).where(models.UploadObject.upload_id == upload_id),
            models.UploadObject.updated_at,
            models.UploadObject.upload_object_id,
        )

    async def list_parts(
        self, ctx: RequestContext, upload_id: str, object_id: str, params: CursorParams
    ) -> dict[str, Any]:
        await self.repo.upload(ctx, upload_id)
        await self.repo.object(upload_id, object_id)
        return await self._projection_page(
            ctx,
            params,
            select(models.UploadPartAttempt).where(
                models.UploadPartAttempt.upload_object_id == object_id
            ),
            models.UploadPartAttempt.part_number,
            models.UploadPartAttempt.part_attempt_id,
        )

    async def source_manifest(self, ctx: RequestContext, upload_id: str) -> dict[str, Any]:
        upload = await self.repo.upload(ctx, upload_id)
        if upload.current_manifest_id is None:
            raise NotFoundError(code="SOURCE_MANIFEST_NOT_FOUND")
        manifest = await self.session.get(models.SourceManifest, upload.current_manifest_id)
        if manifest is None:
            raise NotFoundError(code="SOURCE_MANIFEST_NOT_FOUND")
        return envelope(manifest.projection, ctx)

    async def list_manifest_nodes(
        self,
        ctx: RequestContext,
        upload_id: str,
        params: CursorParams,
        parent_node_id: str | None = None,
        path_prefix: str | None = None,
    ) -> dict[str, Any]:
        upload = await self.repo.upload(ctx, upload_id)
        if upload.current_manifest_id is None:
            raise NotFoundError(code="SOURCE_MANIFEST_NOT_FOUND")
        stmt = select(models.ManifestNode).where(
            models.ManifestNode.manifest_id == upload.current_manifest_id
        )
        if parent_node_id is not None:
            stmt = stmt.where(models.ManifestNode.parent_node_id == parent_node_id)
        if path_prefix is not None:
            stmt = stmt.where(models.ManifestNode.json_pointer.startswith(path_prefix))
        return await self._projection_page(
            ctx, params, stmt, models.ManifestNode.json_pointer, models.ManifestNode.node_id
        )

    async def list_runs(
        self, ctx: RequestContext, upload_id: str, params: CursorParams
    ) -> dict[str, Any]:
        await self.repo.upload(ctx, upload_id)
        return await self._projection_page(
            ctx,
            params,
            select(models.VerificationRun).where(models.VerificationRun.upload_id == upload_id),
            models.VerificationRun.created_at,
            models.VerificationRun.verification_run_id,
        )

    async def list_findings(
        self,
        ctx: RequestContext,
        upload_id: str,
        run_id: str,
        params: CursorParams,
        severity: str | None = None,
        stage: str | None = None,
        code: str | None = None,
    ) -> dict[str, Any]:
        await self.repo.upload(ctx, upload_id)
        await self.repo.run(upload_id, run_id)
        stmt = select(models.VerificationFinding).where(
            models.VerificationFinding.verification_run_id == run_id
        )
        if severity:
            stmt = stmt.where(models.VerificationFinding.severity == severity)
        if code:
            stmt = stmt.where(models.VerificationFinding.code == code)
        if stage:
            # Stage lives in the safe immutable projection in V1.
            stmt = stmt.where(models.VerificationFinding.projection["stage"].as_string() == stage)
        return await self._projection_page(
            ctx,
            params,
            stmt,
            models.VerificationFinding.created_at,
            models.VerificationFinding.finding_id,
        )

    async def list_events(
        self,
        ctx: RequestContext,
        upload_id: str,
        params: CursorParams,
        level: str | None = None,
    ) -> dict[str, Any]:
        await self.repo.upload(ctx, upload_id)
        stmt = select(models.UploadEvent).where(models.UploadEvent.upload_id == upload_id)
        if level:
            stmt = stmt.where(models.UploadEvent.event_level == level)
        return await self._projection_page(
            ctx, params, stmt, models.UploadEvent.occurred_at, models.UploadEvent.event_id
        )
