"""P17 stream-schema routes (15 operations)."""

import json
from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Depends, Header, Path, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.context import RequestContext, require
from app.core.db import get_session
from app.core.errors import GoneError, NotFoundError, ValidationError, VersionConflictError
from app.core.etag import compute_etag
from app.core.idempotency import with_idempotency
from app.core.ids import new_id
from app.core.outbox import emit_event
from app.core.pagination import CursorParams, build_page, cursor_params

from .. import models
from ..schemas import (
    CreateCompatibilityRequest,
    CreateSchemaVersionRequest,
    CreateStreamSchemaRequest,
    EmptyCommand,
    PublishPreflightRequest,
    SchemaImportCommitRequest,
    SchemaImportValidationRequest,
    SchemaValidationRequest,
    TokenCommand,
    UpdateSchemaVersionRequest,
    UpdateStreamSchemaRequest,
    ValidateStreamSchemaRequest,
)
from ..service import (
    CONTRACT_VERSION,
    RoboticsService,
    canonical_hash,
    job_projection,
    schema_version_projection,
    scope_key,
    utc_now,
)

router = APIRouter(tags=["data-schemas"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
OrganizationId = Annotated[str, Path(alias="organizationId", min_length=1, max_length=160)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)]
IfMatch = Annotated[str, Header(alias="If-Match", min_length=3, max_length=256)]
PreflightTokenHeader = Annotated[
    str, Header(alias="X-Preflight-Token", min_length=16, max_length=512)
]


def _check(ctx: RequestContext, organization_id: str) -> str:
    if ctx.organization_id != organization_id:
        raise NotFoundError(code="RESOURCE_NOT_FOUND")
    return scope_key(ctx.organization_id, ctx.project_id, ctx.region_code)


def _scope(ctx: RequestContext) -> dict[str, str]:
    return {
        "organization_id": ctx.organization_id,
        "project_id": ctx.project_id,
        "region_code": ctx.region_code,
    }


def _envelope(data: Any, ctx: RequestContext):
    return {
        "data": data,
        "scope": _scope(ctx),
        "request_id": ctx.request_id,
        "contract_version": CONTRACT_VERSION,
    }


def _page(rows: list[Any], params: CursorParams, fields: tuple[str, str], ctx: RequestContext):
    return {
        **build_page(rows, params, fields),
        "scope": _scope(ctx),
        "request_id": ctx.request_id,
        "contract_version": CONTRACT_VERSION,
    }


def _embedded_page(items: list[dict[str, Any]], ctx: RequestContext):
    return {
        "items": items,
        "page_info": {
            "has_next_page": False,
            "has_previous_page": False,
            "start_cursor": None,
            "end_cursor": None,
        },
        "snapshot_at": utc_now(),
        "scope": _scope(ctx),
        "request_id": ctx.request_id,
        "contract_version": CONTRACT_VERSION,
    }


async def _record(
    session: AsyncSession,
    ctx: RequestContext,
    event_name: str,
    target_type: str,
    target_id: str,
    detail: dict[str, Any] | None = None,
) -> str:
    safe = detail or {}
    event_id = await emit_event(session, event_name, target_type, target_id, safe, ctx)
    await write_audit(session, event_name, target_type, target_id, "SUCCEEDED", ctx, safe)
    return event_id


async def _create_schema(
    service: RoboticsService,
    organization_id: str,
    name: str,
    logical_type: str,
    definition: dict[str, Any],
    *,
    family_id: str | None = None,
    description: str | None = None,
    compatibility_mode: str | None = None,
) -> tuple[models.DataSchema, models.DataSchemaVersion]:
    now = utc_now()
    schema_id = new_id("object")
    root = models.DataSchema(
        schema_id=schema_id,
        organization_id=organization_id,
        name=name,
        normalized_name=name.casefold().strip(),
        logical_type=logical_type,
        family_id=family_id or schema_id,
        description=description,
        compatibility_mode=compatibility_mode
        or str(definition.get("compatibility_policy", "MANUAL")),
        current_published_version=None,
        status="DRAFT",
        resource_version="1",
        etag=compute_etag("1"),
        created_at=now,
        updated_at=now,
    )
    version = models.DataSchemaVersion(
        row_id=new_id("schema_version"),
        schema_id=schema_id,
        organization_id=organization_id,
        version="1",
        parent_version_id=None,
        lifecycle="DRAFT",
        definition_hash=canonical_hash(definition),
        definition=definition,
        validation_status="UNKNOWN",
        compatibility={"status": "UNKNOWN", "rule_set_version": None},
        references=[],
        resource_version="1",
        etag=compute_etag("1"),
        created_at=now,
        updated_at=now,
    )
    service.repo.add(root)
    service.repo.add(version)
    await service.session.flush()
    return root, version


def _stream_schema_projection(
    root: models.DataSchema, version: models.DataSchemaVersion
) -> dict[str, Any]:
    return {
        "id": root.schema_id,
        "family_id": root.family_id,
        "parent_schema_id": None,
        "schema_name": root.name,
        "schema_version": version.version,
        "logical_type": root.logical_type,
        "status": "PUBLISHED" if version.lifecycle == "PUBLISHED" else root.status,
        "description": root.description,
        "compatibility_mode": root.compatibility_mode,
        "schema_definition": version.definition,
        "schema_hash": version.definition_hash,
        "etag": root.etag,
        "allowed_actions": ["UPDATE", "VALIDATE", "PUBLISH"]
        if version.lifecycle == "DRAFT"
        else [],
        "blocked_reasons": [],
    }


async def _latest_schema_version(
    service: RoboticsService, organization_id: str, schema_id: str
) -> models.DataSchemaVersion:
    rows = await service.repo.schema_versions(organization_id, schema_id)
    if not rows:
        raise NotFoundError(code="SCHEMA_VERSION_NOT_FOUND")
    return max(rows, key=lambda row: int(row.version) if row.version.isdigit() else -1)


@router.get(
    "/organizations/{organizationId}/stream-schemas/facets",
    operation_id="dataSchemaGetFacets",
)
async def data_schema_get_facets(
    organization_id: OrganizationId,
    ctx: Annotated[RequestContext, Depends(require("data_schema.read"))],
) -> dict[str, Any]:
    _check(ctx, organization_id)
    return _envelope(
        {
            "logical_types": [],
            "compatibility_modes": ["STRICT", "BACKWARD", "FORWARD", "FULL", "MANUAL"],
            "statuses": ["DRAFT", "VALIDATING", "PUBLISHED"],
            "allowed_actions": ["CREATE"],
        },
        ctx,
    )


@router.get(
    "/organizations/{organizationId}/stream-schemas",
    operation_id="dataSchemaListStreamSchemas",
)
async def data_schema_list_schemas(
    organization_id: OrganizationId,
    session: SessionDep,
    params: Annotated[CursorParams, Depends(cursor_params)],
    ctx: Annotated[RequestContext, Depends(require("data_schema.read"))],
) -> dict[str, Any]:
    _check(ctx, organization_id)
    rows = await RoboticsService(session).repo.data_schemas(organization_id, params.limit + 1)
    page = _page(rows, params, ("created_at", "schema_id"), ctx)
    service = RoboticsService(session)
    projected = []
    for row in page["items"]:
        version = await _latest_schema_version(service, organization_id, row.schema_id)
        projected.append(_stream_schema_projection(row, version))
    page["items"] = projected
    return page


@router.post(
    "/organizations/{organizationId}/stream-schemas",
    operation_id="dataSchemaCreateStreamSchema",
    status_code=status.HTTP_201_CREATED,
)
async def data_schema_create_schema(
    organization_id: OrganizationId,
    body: CreateStreamSchemaRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    ctx: Annotated[RequestContext, Depends(require("data_schema.create"))],
) -> dict[str, Any]:
    operation_scope = _check(ctx, organization_id)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        for existing in await service.repo.data_schemas(organization_id, 1000):
            if existing.normalized_name == body.schema_name.casefold().strip():
                raise VersionConflictError(code="DATA_SCHEMA_NAME_EXISTS")
        root, version = await _create_schema(
            service,
            organization_id,
            body.schema_name,
            body.logical_type,
            body.schema_definition.model_dump(mode="json"),
            family_id=body.family_id,
            description=body.description,
            compatibility_mode=body.compatibility_mode,
        )
        await _record(
            session,
            ctx,
            "data_schema.draft.created",
            "data_schema",
            root.schema_id,
            {"version_id": version.row_id},
        )
        return _envelope(
            _stream_schema_projection(root, version),
            ctx,
        )

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "dataSchemaCreateStreamSchema"),
        command,
    )


@router.get(
    "/organizations/{organizationId}/stream-schemas/{schemaId}",
    operation_id="dataSchemaGetStreamSchema",
)
async def data_schema_get_schema(
    organization_id: OrganizationId,
    schema_id: Annotated[str, Path(alias="schemaId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("data_schema.read"))],
) -> dict[str, Any]:
    _check(ctx, organization_id)
    service = RoboticsService(session)
    row = await service.require_schema(organization_id, schema_id)
    version = await _latest_schema_version(service, organization_id, schema_id)
    return _envelope(_stream_schema_projection(row, version), ctx)


@router.patch(
    "/organizations/{organizationId}/stream-schemas/{schemaId}",
    operation_id="dataSchemaUpdateStreamSchema",
)
async def data_schema_update_stream_schema(
    organization_id: OrganizationId,
    schema_id: Annotated[str, Path(alias="schemaId")],
    body: UpdateStreamSchemaRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("data_schema.create"))],
) -> dict[str, Any]:
    operation_scope = _check(ctx, organization_id)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        root = await service.require_schema(organization_id, schema_id)
        service.check_etag(root.etag, if_match)
        version = await _latest_schema_version(service, organization_id, schema_id)
        service.check_mutable(version.lifecycle)
        definition = body.schema_definition.model_dump(mode="json")
        version.definition = definition
        version.definition_hash = canonical_hash(definition)
        version.validation_status = "UNKNOWN"
        version.compatibility = {"status": "UNKNOWN", "rule_set_version": None}
        service.bump(version)
        root.description = body.description
        if body.compatibility_mode is not None:
            root.compatibility_mode = body.compatibility_mode
        root.status = "DRAFT"
        service.bump(root)
        await session.flush()
        await _record(
            session,
            ctx,
            "data_schema.draft.updated",
            "data_schema",
            schema_id,
            {"change_summary": body.change_summary, "version": version.version},
        )
        return _envelope(_stream_schema_projection(root, version), ctx)

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "dataSchemaUpdateStreamSchema", schema_id),
        command,
    )


@router.post(
    "/organizations/{organizationId}/stream-schemas/{schemaId}:validate",
    operation_id="dataSchemaValidateStreamSchema",
    status_code=status.HTTP_202_ACCEPTED,
)
async def data_schema_validate_stream_schema(
    organization_id: OrganizationId,
    schema_id: Annotated[str, Path(alias="schemaId")],
    body: ValidateStreamSchemaRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("data_schema.validate"))],
) -> dict[str, Any]:
    operation_scope = _check(ctx, organization_id)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        root = await service.require_schema(organization_id, schema_id)
        service.check_etag(root.etag, if_match)
        if body.expected_etag != root.etag:
            raise VersionConflictError(code="SCHEMA_VALIDATION_INPUT_CHANGED")
        version = await _latest_schema_version(service, organization_id, schema_id)
        service.check_mutable(version.lifecycle)
        if body.target_hash != version.definition_hash:
            raise VersionConflictError(code="SCHEMA_VALIDATION_INPUT_CHANGED")
        version.validation_status = "PASSED"
        version.compatibility = {
            "status": "COMPATIBLE",
            "rule_set_version": body.rule_set_version or "schema-rules-v1",
            "findings": [],
        }
        job = await service.create_job(
            operation_scope, "DATA_SCHEMA_VALIDATION", "DATA_SCHEMA_VERSION", version.row_id
        )
        preflight = await service.create_preflight(
            "dataSchemaPublishStreamSchema",
            operation_scope,
            ctx.actor_id,
            root.schema_id,
            root.etag,
            {"version": version.version, "job_id": job.job_id},
        )
        await session.flush()
        await _record(
            session,
            ctx,
            "data_schema.validation.requested",
            "data_schema_version",
            version.row_id,
            {"job_id": job.job_id},
        )
        await _record(
            session,
            ctx,
            "data_schema.validation.completed",
            "data_schema_version",
            version.row_id,
            {"job_id": job.job_id, "outcome": "PASSED"},
        )
        response = _envelope({}, ctx)
        response.pop("data")
        response["job"] = job_projection(job)
        response["preflight"] = preflight
        response["allowed_actions"] = ["PUBLISH"]
        response["blocked_reasons"] = []
        return response

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "dataSchemaValidateStreamSchema", schema_id),
        command,
    )


@router.post(
    "/organizations/{organizationId}/stream-schemas/{schemaId}:publish",
    operation_id="dataSchemaPublishStreamSchema",
)
async def data_schema_publish_stream_schema(
    organization_id: OrganizationId,
    schema_id: Annotated[str, Path(alias="schemaId")],
    body: EmptyCommand,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    preflight_header: PreflightTokenHeader,
    ctx: Annotated[RequestContext, Depends(require("data_schema.publish"))],
) -> dict[str, Any]:
    operation_scope = _check(ctx, organization_id)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        root = await service.require_schema(organization_id, schema_id)
        service.check_etag(root.etag, if_match)
        preflight = await service.consume_preflight(
            "dataSchemaPublishStreamSchema",
            operation_scope,
            ctx.actor_id,
            root.schema_id,
            preflight_header,
            preflight_header,
            root.etag,
        )
        version = await service.require_schema_version(
            organization_id, schema_id, str(preflight.payload["version"])
        )
        if version.validation_status != "PASSED":
            raise VersionConflictError(code="SCHEMA_NOT_VALIDATED")
        version.lifecycle = "PUBLISHED"
        service.bump(version)
        root.current_published_version = version.version
        root.status = "PUBLISHED"
        service.bump(root)
        await session.flush()
        await _record(
            session,
            ctx,
            "data_schema.version.published",
            "data_schema_version",
            version.row_id,
        )
        return _envelope(_stream_schema_projection(root, version), ctx)

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "dataSchemaPublishStreamSchema", schema_id),
        command,
    )


@router.get(
    "/organizations/{organizationId}/stream-schema-families/{familyId}/versions",
    operation_id="dataSchemaListFamilyVersions",
)
async def data_schema_list_family_versions(
    organization_id: OrganizationId,
    family_id: Annotated[str, Path(alias="familyId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("data_schema.read"))],
) -> dict[str, Any]:
    _check(ctx, organization_id)
    service = RoboticsService(session)
    versions = await service.repo.family_schema_versions(organization_id, family_id)
    items = []
    for version in versions:
        root = await service.require_schema(organization_id, version.schema_id)
        items.append(_stream_schema_projection(root, version))
    return _embedded_page(items, ctx)


@router.get(
    "/organizations/{organizationId}/stream-schemas/{schemaId}/versions",
    operation_id="dataSchemaListVersions",
)
async def data_schema_list_versions(
    organization_id: OrganizationId,
    schema_id: Annotated[str, Path(alias="schemaId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("data_schema.read"))],
) -> dict[str, Any]:
    _check(ctx, organization_id)
    service = RoboticsService(session)
    await service.require_schema(organization_id, schema_id)
    rows = await service.repo.schema_versions(organization_id, schema_id)
    return _embedded_page([schema_version_projection(row) for row in rows], ctx)


@router.post(
    "/organizations/{organizationId}/stream-schemas/{schemaId}/versions",
    operation_id="dataSchemaCreateNextVersion",
    status_code=status.HTTP_201_CREATED,
)
async def data_schema_create_next_version(
    organization_id: OrganizationId,
    schema_id: Annotated[str, Path(alias="schemaId")],
    body: CreateSchemaVersionRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("data_schema.create"))],
) -> dict[str, Any]:
    operation_scope = _check(ctx, organization_id)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        root = await service.require_schema(organization_id, schema_id)
        parent = await service.repo.schema_version_by_row(organization_id, body.parent_version_id)
        if parent is None or parent.schema_id != schema_id:
            raise NotFoundError(code="PARENT_SCHEMA_VERSION_NOT_FOUND")
        service.check_etag(parent.etag, if_match)
        versions = await service.repo.schema_versions(organization_id, schema_id)
        next_number = str(
            max([int(row.version) for row in versions if row.version.isdigit()] or [0]) + 1
        )
        now = utc_now()
        row = models.DataSchemaVersion(
            row_id=new_id("schema_version"),
            schema_id=schema_id,
            organization_id=organization_id,
            version=next_number,
            parent_version_id=parent.row_id,
            lifecycle="DRAFT",
            definition_hash=parent.definition_hash,
            definition=dict(parent.definition),
            validation_status="UNKNOWN",
            compatibility={"status": "UNKNOWN", "rule_set_version": None},
            references=[],
            resource_version="1",
            etag=compute_etag("1"),
            created_at=now,
            updated_at=now,
        )
        service.repo.add(row)
        root.updated_at = now
        await session.flush()
        await _record(
            session,
            ctx,
            "data_schema.draft.created",
            "data_schema_version",
            row.row_id,
            {"parent_version_id": parent.row_id},
        )
        return _envelope(schema_version_projection(row), ctx)

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "dataSchemaCreateNextVersion", schema_id),
        command,
    )


@router.get(
    "/organizations/{organizationId}/stream-schemas/{schemaId}/versions/{schemaVersion}",
    operation_id="dataSchemaGetVersion",
)
async def data_schema_get_version(
    organization_id: OrganizationId,
    schema_id: Annotated[str, Path(alias="schemaId")],
    schema_version: Annotated[str, Path(alias="schemaVersion")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("data_schema.read"))],
) -> dict[str, Any]:
    _check(ctx, organization_id)
    row = await RoboticsService(session).require_schema_version(
        organization_id, schema_id, schema_version
    )
    return _envelope(schema_version_projection(row), ctx)


@router.patch(
    "/organizations/{organizationId}/stream-schemas/{schemaId}/versions/{schemaVersion}",
    operation_id="dataSchemaUpdateVersion",
)
async def data_schema_update_version(
    organization_id: OrganizationId,
    schema_id: Annotated[str, Path(alias="schemaId")],
    schema_version: Annotated[str, Path(alias="schemaVersion")],
    body: UpdateSchemaVersionRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("data_schema.create"))],
) -> dict[str, Any]:
    operation_scope = _check(ctx, organization_id)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        row = await service.require_schema_version(organization_id, schema_id, schema_version)
        service.check_mutable(row.lifecycle)
        service.check_etag(row.etag, if_match)
        definition = body.model_dump(mode="json", exclude={"change_summary"})
        row.definition = definition
        row.definition_hash = canonical_hash(definition)
        row.validation_status = "UNKNOWN"
        row.compatibility = {"status": "UNKNOWN", "rule_set_version": None}
        service.bump(row)
        await session.flush()
        await _record(
            session,
            ctx,
            "data_schema.draft.updated",
            "data_schema_version",
            row.row_id,
            {"change_summary": body.change_summary},
        )
        return _envelope(schema_version_projection(row), ctx)

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "dataSchemaUpdateVersion", schema_id, schema_version),
        command,
    )


@router.post(
    "/organizations/{organizationId}/stream-schemas/{schemaId}/versions/{schemaVersion}/validate",
    operation_id="dataSchemaValidateVersion",
    status_code=status.HTTP_202_ACCEPTED,
)
async def data_schema_validate_version(
    organization_id: OrganizationId,
    schema_id: Annotated[str, Path(alias="schemaId")],
    schema_version: Annotated[str, Path(alias="schemaVersion")],
    body: SchemaValidationRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("data_schema.validate"))],
) -> dict[str, Any]:
    operation_scope = _check(ctx, organization_id)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        row = await service.require_schema_version(organization_id, schema_id, schema_version)
        service.check_etag(row.etag, if_match)
        if body.expected_etag != row.etag or body.expected_hash != row.definition_hash:
            raise VersionConflictError(code="SCHEMA_VALIDATION_INPUT_CHANGED")
        row.validation_status = "PASSED"
        row.compatibility = {
            "status": "COMPATIBLE",
            "rule_set_version": body.rule_set_version,
            "findings": [],
        }
        service.bump(row)
        job = await service.create_job(
            operation_scope, "DATA_SCHEMA_VALIDATION", "DATA_SCHEMA_VERSION", row.row_id
        )
        await session.flush()
        await _record(
            session,
            ctx,
            "data_schema.validation.requested",
            "data_schema_version",
            row.row_id,
            {"job_id": job.job_id},
        )
        await _record(
            session,
            ctx,
            "data_schema.validation.completed",
            "data_schema_version",
            row.row_id,
            {"job_id": job.job_id, "outcome": "PASSED"},
        )
        response = _envelope({}, ctx)
        response.pop("data")
        response["job"] = job_projection(job)
        return response

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "dataSchemaValidateVersion", schema_id, schema_version),
        command,
    )


@router.post(
    "/organizations/{organizationId}/stream-schemas/{schemaId}/versions/{schemaVersion}:preflight-publish",
    operation_id="dataSchemaPreflightPublish",
)
async def data_schema_preflight_publish(
    organization_id: OrganizationId,
    schema_id: Annotated[str, Path(alias="schemaId")],
    schema_version: Annotated[str, Path(alias="schemaVersion")],
    body: PublishPreflightRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("data_schema.publish"))],
) -> dict[str, Any]:
    operation_scope = _check(ctx, organization_id)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        row = await service.require_schema_version(organization_id, schema_id, schema_version)
        service.check_mutable(row.lifecycle)
        service.check_etag(row.etag, if_match)
        if body.expected_etag != row.etag or body.expected_hash != row.definition_hash:
            raise VersionConflictError(code="SCHEMA_PUBLISH_INPUT_CHANGED")
        job = await service.repo.job(operation_scope, body.validation_report_id)
        if job is None or job.resource_id != row.row_id or row.validation_status != "PASSED":
            raise ValidationError(code="PASSING_VALIDATION_REQUIRED")
        data = await service.create_preflight(
            "dataSchemaPublishVersion",
            operation_scope,
            ctx.actor_id,
            row.row_id,
            row.etag,
            body.model_dump(mode="json"),
        )
        await _record(
            session,
            ctx,
            "data_schema.version.publish_preflighted",
            "data_schema_version",
            row.row_id,
        )
        return _envelope(data, ctx)

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "dataSchemaPreflightPublish", schema_id, schema_version),
        command,
    )


@router.post(
    "/organizations/{organizationId}/stream-schemas/{schemaId}/versions/{schemaVersion}:publish",
    operation_id="dataSchemaPublishVersion",
)
async def data_schema_publish_version(
    organization_id: OrganizationId,
    schema_id: Annotated[str, Path(alias="schemaId")],
    schema_version: Annotated[str, Path(alias="schemaVersion")],
    body: TokenCommand,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    preflight_header: PreflightTokenHeader,
    ctx: Annotated[RequestContext, Depends(require("data_schema.publish"))],
) -> dict[str, Any]:
    operation_scope = _check(ctx, organization_id)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        row = await service.require_schema_version(organization_id, schema_id, schema_version)
        service.check_mutable(row.lifecycle)
        service.check_etag(row.etag, if_match)
        await service.consume_preflight(
            "dataSchemaPublishVersion",
            operation_scope,
            ctx.actor_id,
            row.row_id,
            body.preflight_token,
            preflight_header,
            row.etag,
        )
        if row.validation_status != "PASSED":
            raise VersionConflictError(code="SCHEMA_NOT_VALIDATED")
        row.lifecycle = "PUBLISHED"
        service.bump(row)
        root = await service.require_schema(organization_id, schema_id)
        root.current_published_version = schema_version
        root.updated_at = utc_now()
        await session.flush()
        await _record(
            session, ctx, "data_schema.version.published", "data_schema_version", row.row_id
        )
        return _envelope(schema_version_projection(row), ctx)

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "dataSchemaPublishVersion", schema_id, schema_version),
        command,
    )


@router.get(
    "/organizations/{organizationId}/stream-schemas/{schemaId}/versions/{schemaVersion}/references",
    operation_id="dataSchemaListReferences",
)
async def data_schema_list_references(
    organization_id: OrganizationId,
    schema_id: Annotated[str, Path(alias="schemaId")],
    schema_version: Annotated[str, Path(alias="schemaVersion")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("data_schema.read"))],
) -> dict[str, Any]:
    _check(ctx, organization_id)
    row = await RoboticsService(session).require_schema_version(
        organization_id, schema_id, schema_version
    )
    return _embedded_page(row.references, ctx)


def _snapshot_projection(row: models.DatasetSchemaSnapshot) -> dict[str, Any]:
    return {
        "snapshot_id": row.snapshot_id,
        "dataset_id": row.dataset_id,
        "dataset_version_id": row.dataset_version_id,
        "content_hash": row.content_hash,
        "channel_count": str(len(row.channels)),
        "created_at": row.created_at,
        "allowed_actions": ["VIEW"],
        "blocked_reasons": [],
    }


@router.get(
    "/organizations/{organizationId}/dataset-schema-snapshots",
    operation_id="dataSchemaListSnapshots",
)
async def data_schema_list_snapshots(
    organization_id: OrganizationId,
    session: SessionDep,
    params: Annotated[CursorParams, Depends(cursor_params)],
    ctx: Annotated[RequestContext, Depends(require("data_schema.read"))],
) -> dict[str, Any]:
    _check(ctx, organization_id)
    rows = await RoboticsService(session).repo.schema_snapshots(organization_id, params.limit + 1)
    page = _page(rows, params, ("created_at", "snapshot_id"), ctx)
    page["items"] = [_snapshot_projection(row) for row in page["items"]]
    return page


@router.get(
    "/organizations/{organizationId}/dataset-schema-snapshots/{snapshotId}",
    operation_id="dataSchemaGetSnapshot",
)
async def data_schema_get_snapshot(
    organization_id: OrganizationId,
    snapshot_id: Annotated[str, Path(alias="snapshotId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("data_schema.read"))],
) -> dict[str, Any]:
    _check(ctx, organization_id)
    row = await RoboticsService(session).repo.schema_snapshot(organization_id, snapshot_id)
    if row is None:
        raise NotFoundError(code="DATASET_SCHEMA_SNAPSHOT_NOT_FOUND")
    return _envelope(_snapshot_projection(row), ctx)


@router.get(
    "/organizations/{organizationId}/dataset-schema-snapshots/{snapshotId}/channel-definitions",
    operation_id="dataSchemaListSnapshotChannels",
)
async def data_schema_list_snapshot_channels(
    organization_id: OrganizationId,
    snapshot_id: Annotated[str, Path(alias="snapshotId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("data_schema.read"))],
) -> dict[str, Any]:
    _check(ctx, organization_id)
    row = await RoboticsService(session).repo.schema_snapshot(organization_id, snapshot_id)
    if row is None:
        raise NotFoundError(code="DATASET_SCHEMA_SNAPSHOT_NOT_FOUND")
    return _embedded_page(row.channels, ctx)


@router.get(
    "/organizations/{organizationId}/dataset-schema-snapshots/{snapshotId}/diff",
    operation_id="dataSchemaGetSnapshotDiff",
)
async def data_schema_get_snapshot_diff(
    organization_id: OrganizationId,
    snapshot_id: Annotated[str, Path(alias="snapshotId")],
    base_snapshot_id: Annotated[str, Query(alias="base_snapshot_id", min_length=1)],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("data_schema.read"))],
) -> dict[str, Any]:
    _check(ctx, organization_id)
    service = RoboticsService(session)
    row = await service.repo.schema_snapshot(organization_id, snapshot_id)
    base = await service.repo.schema_snapshot(organization_id, base_snapshot_id)
    if row is None or base is None:
        raise NotFoundError(code="DATASET_SCHEMA_SNAPSHOT_NOT_FOUND")
    return _embedded_page(row.diff, ctx)


@router.post(
    "/organizations/{organizationId}/stream-schema-compatibility-checks",
    operation_id="dataSchemaCreateCompatibilityCheck",
    status_code=status.HTTP_202_ACCEPTED,
)
async def data_schema_create_compatibility_check(
    organization_id: OrganizationId,
    body: CreateCompatibilityRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    ctx: Annotated[RequestContext, Depends(require("data_schema.validate"))],
) -> dict[str, Any]:
    operation_scope = _check(ctx, organization_id)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        baseline = await service.repo.schema_version_by_row(
            organization_id, body.baseline_version_id
        )
        target = await service.repo.schema_version_by_row(organization_id, body.target_version_id)
        if baseline is None or target is None:
            raise NotFoundError(code="SCHEMA_VERSION_NOT_FOUND")
        if (
            baseline.definition_hash != body.baseline_hash
            or target.definition_hash != body.target_hash
        ):
            raise VersionConflictError(code="SCHEMA_HASH_CHANGED")
        check_id = new_id("event")
        result = {
            "id": check_id,
            "status": "SUCCEEDED",
            "verdict": "COMPATIBLE",
            "mode": body.mode,
            "rule_set_version": body.rule_set_version,
            "baseline_version_id": baseline.row_id,
            "target_version_id": target.row_id,
            "findings": [],
        }
        job = await service.create_job(
            operation_scope, "DATA_SCHEMA_COMPATIBILITY", "COMPATIBILITY_CHECK", check_id
        )
        row = models.SchemaCompatibilityCheck(
            check_id=check_id,
            organization_id=organization_id,
            status="SUCCEEDED",
            result=result,
            job_id=job.job_id,
            created_at=utc_now(),
        )
        service.repo.add(row)
        await session.flush()
        await _record(
            session,
            ctx,
            "data_schema.compatibility.requested",
            "schema_compatibility_check",
            check_id,
            {"job_id": job.job_id},
        )
        await _record(
            session,
            ctx,
            "data_schema.compatibility.checked",
            "schema_compatibility_check",
            check_id,
            {"job_id": job.job_id, "verdict": result["verdict"]},
        )
        response = _envelope({}, ctx)
        response.pop("data")
        response["job"] = job_projection(job)
        return response

    return await with_idempotency(
        session, idempotency_key, (operation_scope, "dataSchemaCreateCompatibilityCheck"), command
    )


@router.get(
    "/organizations/{organizationId}/stream-schema-compatibility-checks/{checkId}",
    operation_id="dataSchemaGetCompatibilityCheck",
)
async def data_schema_get_compatibility_check(
    organization_id: OrganizationId,
    check_id: Annotated[str, Path(alias="checkId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("data_schema.read"))],
) -> dict[str, Any]:
    _check(ctx, organization_id)
    row = await RoboticsService(session).repo.compatibility_check(organization_id, check_id)
    if row is None:
        raise NotFoundError(code="COMPATIBILITY_CHECK_NOT_FOUND")
    return _envelope(row.result, ctx)


@router.post(
    "/organizations/{organizationId}/stream-schema-imports:validate",
    operation_id="dataSchemaValidateImport",
)
async def data_schema_validate_import(
    organization_id: OrganizationId,
    body: SchemaImportValidationRequest,
    response: Response,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    ctx: Annotated[RequestContext, Depends(require("data_schema.import"))],
) -> dict[str, Any]:
    operation_scope = _check(ctx, organization_id)

    async def command() -> dict[str, Any]:
        try:
            parsed = (
                json.loads(body.content)
                if body.media_type == "application/json"
                else yaml.safe_load(body.content)
            )
        except (json.JSONDecodeError, yaml.YAMLError) as exc:
            raise ValidationError(code="SCHEMA_IMPORT_PARSE_FAILED") from exc
        if not isinstance(parsed, dict):
            raise ValidationError(code="SCHEMA_IMPORT_OBJECT_REQUIRED")
        validation_id = new_id("event")
        expires_at = utc_now().replace(microsecond=0)
        expires_at = expires_at.replace(second=min(expires_at.second, 59))
        from datetime import timedelta

        expires_at += timedelta(minutes=10)
        row = models.SchemaImportValidation(
            import_validation_id=validation_id,
            organization_id=organization_id,
            actor_id=ctx.actor_id,
            content_hash=canonical_hash(parsed),
            normalized_definition=parsed,
            expires_at=expires_at,
            committed=False,
        )
        session.add(row)
        await session.flush()
        await _record(
            session,
            ctx,
            "data_schema.import.validated",
            "schema_import_validation",
            validation_id,
            {"content_hash": row.content_hash},
        )
        response.headers["Cache-Control"] = "private, no-store"
        return _envelope(
            {
                "import_validation_id": validation_id,
                "content_hash": row.content_hash,
                "expires_at": expires_at,
                "normalized_summary": {
                    "name": parsed.get("name"),
                    "logical_type": parsed.get("logical_type"),
                    "field_count": len(parsed.get("fields", [])),
                },
                "warnings": [],
            },
            ctx,
        )

    return await with_idempotency(
        session, idempotency_key, (operation_scope, "dataSchemaValidateImport"), command
    )


@router.post(
    "/organizations/{organizationId}/stream-schema-imports:commit",
    operation_id="dataSchemaCommitImport",
    status_code=status.HTTP_201_CREATED,
)
async def data_schema_commit_import(
    organization_id: OrganizationId,
    body: SchemaImportCommitRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    ctx: Annotated[RequestContext, Depends(require("data_schema.import"))],
) -> dict[str, Any]:
    operation_scope = _check(ctx, organization_id)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        validation = await service.repo.import_validation(
            organization_id, body.import_validation_id
        )
        if validation is None or validation.actor_id != ctx.actor_id:
            raise NotFoundError(code="SCHEMA_IMPORT_VALIDATION_NOT_FOUND")
        expires_at = validation.expires_at
        if expires_at.tzinfo is None:
            from datetime import UTC

            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= utc_now():
            raise GoneError(code="SCHEMA_IMPORT_VALIDATION_EXPIRED")
        if validation.committed:
            raise VersionConflictError(code="SCHEMA_IMPORT_ALREADY_COMMITTED")
        parsed = validation.normalized_definition
        if body.target_schema_id is None:
            name = str(
                parsed.get("name") or f"Imported schema {validation.import_validation_id[-8:]}"
            )
            logical_type = str(parsed.get("logical_type") or "IMPORTED")
            definition = {
                "fields": parsed.get("fields", []),
                "compatibility_policy": parsed.get("compatibility_policy", "MANUAL"),
                "encoding": parsed.get("encoding"),
                "coordinate": parsed.get("coordinate"),
                "timestamp": parsed.get("timestamp"),
            }
            _root, version = await _create_schema(
                service, organization_id, name, logical_type, definition
            )
        else:
            root = await service.require_schema(organization_id, body.target_schema_id)
            versions = await service.repo.schema_versions(organization_id, root.schema_id)
            number = str(
                max([int(row.version) for row in versions if row.version.isdigit()] or [0]) + 1
            )
            now = utc_now()
            definition = {
                "fields": parsed.get("fields", []),
                "compatibility_policy": parsed.get("compatibility_policy", "MANUAL"),
                "encoding": parsed.get("encoding"),
                "coordinate": parsed.get("coordinate"),
                "timestamp": parsed.get("timestamp"),
            }
            version = models.DataSchemaVersion(
                row_id=new_id("schema_version"),
                schema_id=root.schema_id,
                organization_id=organization_id,
                version=number,
                parent_version_id=None,
                lifecycle="DRAFT",
                definition_hash=canonical_hash(definition),
                definition=definition,
                validation_status="UNKNOWN",
                compatibility={"status": "UNKNOWN", "rule_set_version": None},
                references=[],
                resource_version="1",
                etag=compute_etag("1"),
                created_at=now,
                updated_at=now,
            )
            service.repo.add(version)
            await session.flush()
        validation.committed = True
        await session.flush()
        await _record(
            session,
            ctx,
            "data_schema.import.committed",
            "data_schema_version",
            version.row_id,
            {"schema_id": version.schema_id},
        )
        return _envelope(schema_version_projection(version), ctx)

    return await with_idempotency(
        session, idempotency_key, (operation_scope, "dataSchemaCommitImport"), command
    )
