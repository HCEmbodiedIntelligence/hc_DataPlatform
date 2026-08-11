"""P16 calibration routes (20 operations)."""

from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Path, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.context import RequestContext, require
from app.core.db import get_session
from app.core.errors import NotFoundError, ValidationError, VersionConflictError
from app.core.etag import compute_etag
from app.core.idempotency import with_idempotency
from app.core.ids import new_id
from app.core.outbox import emit_event
from app.core.pagination import CursorParams, build_page, cursor_params

from .. import models
from ..schemas import (
    CalibrationImportRequest,
    CalibrationRecordPatchRequest,
    CalibrationRecordWriteRequest,
    CalibrationSetValidationRequest,
    CloneCalibrationSetRequest,
    CreateCalibrationSetRequest,
    CreateCalibrationValidationRequest,
    CreateVersionRequest,
    EmptyCommand,
    PublishPreflightRequest,
    TokenCommand,
)
from ..service import (
    CONTRACT_VERSION,
    RoboticsService,
    calibration_set_projection,
    calibration_version_projection,
    canonical_hash,
    job_projection,
    scope_key,
    utc_now,
)

router = APIRouter(tags=["calibrations"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ProjectId = Annotated[str, Path(alias="projectId", min_length=1, max_length=160)]
RegionCode = Annotated[str, Path(alias="regionCode", min_length=1, max_length=64)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)]
IfMatch = Annotated[str, Header(alias="If-Match", min_length=3, max_length=256)]
PreflightTokenHeader = Annotated[
    str, Header(alias="X-Preflight-Token", min_length=16, max_length=512)
]


def _check(ctx: RequestContext, project_id: str, region_code: str) -> str:
    if ctx.project_id != project_id or ctx.region_code != region_code:
        raise NotFoundError(code="RESOURCE_NOT_FOUND")
    return scope_key(ctx.organization_id, project_id, region_code)


def _scope(ctx: RequestContext) -> dict[str, str]:
    return {
        "organization_id": ctx.organization_id,
        "project_id": ctx.project_id,
        "region_code": ctx.region_code,
    }


def _envelope(data: Any, ctx: RequestContext) -> dict[str, Any]:
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


async def _create_set_and_version(
    service: RoboticsService,
    ctx: RequestContext,
    project_id: str,
    region_code: str,
    body: CreateCalibrationSetRequest,
    source_artifacts: list[dict[str, Any]] | None = None,
) -> tuple[models.CalibrationSet, models.CalibrationVersion]:
    robot = await service.require_robot(project_id, region_code, body.robot_id)
    component = await service.require_component(project_id, region_code, body.component_id)
    if component.robot_id != robot.robot_id:
        raise ValidationError(code="COMPONENT_ROBOT_MISMATCH")
    now = utc_now()
    set_id = new_id("calibration_set")
    calibration_set = models.CalibrationSet(
        set_id=set_id,
        organization_id=ctx.organization_id,
        project_id=project_id,
        region_code=region_code,
        robot_id=body.robot_id,
        component_id=body.component_id,
        display_name=body.display_name,
        valid_from=body.valid_from,
        valid_to=body.valid_to,
        lifecycle="DRAFT",
        resource_version="1",
        etag=compute_etag("1"),
        created_at=now,
        updated_at=now,
    )
    definition = {"transforms": [], "frames": [], "parameters": {}}
    version = models.CalibrationVersion(
        row_id=f"{set_id}:1",
        set_id=set_id,
        organization_id=ctx.organization_id,
        project_id=project_id,
        region_code=region_code,
        version="1",
        lifecycle="DRAFT",
        content_hash=canonical_hash(definition),
        validation_context_hash=canonical_hash(
            {"robot_id": body.robot_id, "component_id": body.component_id}
        ),
        definition=definition,
        source_artifacts=source_artifacts or [],
        availability={"state": "DRAFT", "revision": "1", "consumers": []},
        resource_version="1",
        etag=compute_etag("1"),
        created_at=now,
        updated_at=now,
    )
    service.repo.add(calibration_set)
    service.repo.add(version)
    await service.session.flush()
    return calibration_set, version


@router.get(
    "/projects/{projectId}/regions/{regionCode}/calibration-sets/facets",
    operation_id="calibrationGetFacets",
)
async def calibration_get_facets(
    project_id: ProjectId,
    region_code: RegionCode,
    ctx: Annotated[RequestContext, Depends(require("calibration.read"))],
) -> dict[str, Any]:
    _check(ctx, project_id, region_code)
    return _envelope(
        {
            "section_codes": [
                "intrinsics",
                "transforms",
                "time-calibrations",
                "joint-calibrations",
            ],
            "snapshot_statuses": ["DRAFT", "READY"],
            "availability_states": ["DRAFT", "ACTIVE", "EXPIRED", "REVOKED"],
            "allowed_actions": ["CREATE", "IMPORT"],
        },
        ctx,
    )


@router.get(
    "/projects/{projectId}/regions/{regionCode}/calibration-sets",
    operation_id="calibrationListSets",
)
async def calibration_list_sets(
    project_id: ProjectId,
    region_code: RegionCode,
    session: SessionDep,
    params: Annotated[CursorParams, Depends(cursor_params)],
    ctx: Annotated[RequestContext, Depends(require("calibration.read"))],
) -> dict[str, Any]:
    _check(ctx, project_id, region_code)
    rows = await RoboticsService(session).repo.calibration_sets(
        project_id, region_code, params.limit + 1
    )
    page = _page(rows, params, ("created_at", "set_id"), ctx)
    page["items"] = [calibration_set_projection(row) for row in page["items"]]
    return page


@router.post(
    "/projects/{projectId}/regions/{regionCode}/calibration-sets",
    operation_id="calibrationCreateSet",
    status_code=status.HTTP_201_CREATED,
)
async def calibration_create_set(
    project_id: ProjectId,
    region_code: RegionCode,
    body: CreateCalibrationSetRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    ctx: Annotated[RequestContext, Depends(require("calibration.create"))],
) -> dict[str, Any]:
    operation_scope = _check(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        calibration_set, version = await _create_set_and_version(
            RoboticsService(session), ctx, project_id, region_code, body
        )
        await _record(
            session,
            ctx,
            "calibration.draft.created",
            "calibration_set",
            calibration_set.set_id,
            {"version": version.version},
        )
        return _envelope(
            {
                "calibration_set": calibration_set_projection(calibration_set),
                "initial_version": calibration_version_projection(version),
            },
            ctx,
        )

    return await with_idempotency(
        session, idempotency_key, (operation_scope, "calibrationCreateSet"), command
    )


@router.post(
    "/projects/{projectId}/regions/{regionCode}/calibration-sets/imports",
    operation_id="calibrationStartImport",
    status_code=status.HTTP_202_ACCEPTED,
)
async def calibration_start_import(
    project_id: ProjectId,
    region_code: RegionCode,
    body: CalibrationImportRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    ctx: Annotated[RequestContext, Depends(require("calibration.create"))],
) -> dict[str, Any]:
    operation_scope = _check(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        artifact = body.source_artifact.model_dump(mode="json")
        provider_object_id = artifact.pop("provider_object_id")
        artifact.update({"id": new_id("object"), "storage_ref": canonical_hash(provider_object_id)})
        set_body = CreateCalibrationSetRequest.model_validate(
            body.model_dump(exclude={"source_artifact"})
        )
        service = RoboticsService(session)
        calibration_set, version = await _create_set_and_version(
            service, ctx, project_id, region_code, set_body, [artifact]
        )
        job = await service.create_job(
            operation_scope, "CALIBRATION_IMPORT", "CALIBRATION_VERSION", version.row_id
        )
        await _record(
            session,
            ctx,
            "calibration.import.requested",
            "calibration_version",
            version.row_id,
            {"job_id": job.job_id},
        )
        await _record(
            session,
            ctx,
            "calibration.import.completed",
            "calibration_version",
            version.row_id,
            {"job_id": job.job_id, "outcome": "SUCCEEDED"},
        )
        response = _envelope({}, ctx)
        response.pop("data")
        response["job"] = job_projection(job)
        response["result"] = {"set_id": calibration_set.set_id, "version": version.version}
        return response

    return await with_idempotency(
        session, idempotency_key, (operation_scope, "calibrationStartImport"), command
    )


@router.get(
    "/projects/{projectId}/regions/{regionCode}/calibration-jobs",
    operation_id="calibrationListJobs",
)
async def calibration_list_jobs(
    project_id: ProjectId,
    region_code: RegionCode,
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("calibration.read"))],
) -> dict[str, Any]:
    operation_scope = _check(ctx, project_id, region_code)
    rows = await RoboticsService(session).repo.jobs(operation_scope)
    return _embedded_page([job_projection(row) for row in rows], ctx)


@router.get(
    "/projects/{projectId}/regions/{regionCode}/calibration-jobs/{jobId}",
    operation_id="calibrationGetJob",
)
async def calibration_get_job(
    project_id: ProjectId,
    region_code: RegionCode,
    job_id: Annotated[str, Path(alias="jobId")],
    response: Response,
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("calibration.read"))],
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> Any:
    operation_scope = _check(ctx, project_id, region_code)
    row = await RoboticsService(session).repo.job(operation_scope, job_id)
    if row is None:
        raise NotFoundError(code="CALIBRATION_JOB_NOT_FOUND")
    etag = compute_etag(row.updated_at.isoformat())
    if if_none_match == etag:
        return Response(
            status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag, "Retry-After": "2"}
        )
    response.headers["ETag"] = etag
    response.headers["Retry-After"] = "2"
    return _envelope(job_projection(row), ctx)


@router.get(
    "/projects/{projectId}/regions/{regionCode}/calibration-sets/{setId}",
    operation_id="calibrationGetSet",
)
async def calibration_get_set(
    project_id: ProjectId,
    region_code: RegionCode,
    set_id: Annotated[str, Path(alias="setId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("calibration.read"))],
) -> dict[str, Any]:
    _check(ctx, project_id, region_code)
    row = await RoboticsService(session).require_calibration_set(project_id, region_code, set_id)
    return _envelope(calibration_set_projection(row), ctx)


@router.patch(
    "/projects/{projectId}/regions/{regionCode}/calibration-sets/{setId}",
    operation_id="calibrationUpdateSet",
)
async def calibration_update_set(
    project_id: ProjectId,
    region_code: RegionCode,
    set_id: Annotated[str, Path(alias="setId")],
    body: dict[str, Any],
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("calibration.create"))],
) -> dict[str, Any]:
    operation_scope = _check(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        row = await service.require_calibration_set(project_id, region_code, set_id)
        service.check_etag(row.etag, if_match)
        forbidden = set(body) - {"display_name", "valid_from", "valid_to"}
        if forbidden:
            raise ValidationError(code="SERVER_OWNED_FIELD")
        for key, value in body.items():
            setattr(row, key, value)
        service.bump(row)
        await session.flush()
        await _record(
            session,
            ctx,
            "calibration.set.updated",
            "calibration_set",
            set_id,
            {"changed_fields": sorted(body)},
        )
        return _envelope(calibration_set_projection(row), ctx)

    return await with_idempotency(
        session, idempotency_key, (operation_scope, "calibrationUpdateSet", set_id), command
    )


@router.get(
    "/projects/{projectId}/regions/{regionCode}/calibration-sets/{setId}/versions",
    operation_id="calibrationListVersions",
)
async def calibration_list_versions(
    project_id: ProjectId,
    region_code: RegionCode,
    set_id: Annotated[str, Path(alias="setId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("calibration.read"))],
) -> dict[str, Any]:
    _check(ctx, project_id, region_code)
    service = RoboticsService(session)
    await service.require_calibration_set(project_id, region_code, set_id)
    rows = await service.repo.calibration_versions(project_id, region_code, set_id)
    return _embedded_page([calibration_version_projection(row) for row in rows], ctx)


@router.post(
    "/projects/{projectId}/regions/{regionCode}/calibration-sets/{setId}/versions",
    operation_id="calibrationCreateVersion",
    status_code=status.HTTP_201_CREATED,
)
async def calibration_create_version(
    project_id: ProjectId,
    region_code: RegionCode,
    set_id: Annotated[str, Path(alias="setId")],
    body: CreateVersionRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("calibration.create"))],
) -> dict[str, Any]:
    operation_scope = _check(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        calibration_set = await service.require_calibration_set(project_id, region_code, set_id)
        service.check_etag(calibration_set.etag, if_match)
        versions = await service.repo.calibration_versions(project_id, region_code, set_id)
        base = None
        if body.base_version_id:
            base = next((row for row in versions if row.row_id == body.base_version_id), None)
            if base is None:
                raise NotFoundError(code="BASE_CALIBRATION_VERSION_NOT_FOUND")
        next_number = str(
            max([int(row.version) for row in versions if row.version.isdigit()] or [0]) + 1
        )
        now = utc_now()
        definition = (
            dict(base.definition) if base else {"transforms": [], "frames": [], "parameters": {}}
        )
        row = models.CalibrationVersion(
            row_id=f"{set_id}:{next_number}",
            set_id=set_id,
            organization_id=ctx.organization_id,
            project_id=project_id,
            region_code=region_code,
            version=next_number,
            lifecycle="DRAFT",
            content_hash=canonical_hash(definition),
            validation_context_hash=canonical_hash(
                {"robot_id": calibration_set.robot_id, "component_id": calibration_set.component_id}
            ),
            definition=definition,
            source_artifacts=list(base.source_artifacts) if base else [],
            availability={"state": "DRAFT", "revision": "1", "consumers": []},
            resource_version="1",
            etag=compute_etag("1"),
            created_at=now,
            updated_at=now,
        )
        service.repo.add(row)
        service.bump(calibration_set)
        await session.flush()
        await _record(
            session,
            ctx,
            "calibration.draft.created",
            "calibration_version",
            row.row_id,
            {"set_id": set_id},
        )
        return _envelope(calibration_version_projection(row), ctx)

    return await with_idempotency(
        session, idempotency_key, (operation_scope, "calibrationCreateVersion", set_id), command
    )


@router.get(
    "/projects/{projectId}/regions/{regionCode}/calibration-sets/{setId}/versions/{calibrationVersion}",
    operation_id="calibrationGetVersion",
)
async def calibration_get_version(
    project_id: ProjectId,
    region_code: RegionCode,
    set_id: Annotated[str, Path(alias="setId")],
    calibration_version: Annotated[str, Path(alias="calibrationVersion")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("calibration.read"))],
) -> dict[str, Any]:
    _check(ctx, project_id, region_code)
    row = await RoboticsService(session).require_calibration_version(
        project_id, region_code, set_id, calibration_version
    )
    return _envelope(calibration_version_projection(row), ctx)


@router.patch(
    "/projects/{projectId}/regions/{regionCode}/calibration-sets/{setId}/versions/{calibrationVersion}",
    operation_id="calibrationUpdateVersion",
)
async def calibration_update_version(
    project_id: ProjectId,
    region_code: RegionCode,
    set_id: Annotated[str, Path(alias="setId")],
    calibration_version: Annotated[str, Path(alias="calibrationVersion")],
    body: dict[str, Any],
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("calibration.create"))],
) -> dict[str, Any]:
    operation_scope = _check(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        row = await service.require_calibration_version(
            project_id, region_code, set_id, calibration_version
        )
        service.check_mutable(row.lifecycle)
        service.check_etag(row.etag, if_match)
        row.definition = body
        row.content_hash = canonical_hash(body)
        service.bump(row)
        await session.flush()
        await _record(session, ctx, "calibration.draft.updated", "calibration_version", row.row_id)
        return _envelope(calibration_version_projection(row), ctx)

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "calibrationUpdateVersion", set_id, calibration_version),
        command,
    )


@router.get(
    "/projects/{projectId}/regions/{regionCode}/calibration-sets/{setId}/versions/{calibrationVersion}/source-artifacts",
    operation_id="calibrationListSourceArtifacts",
)
async def calibration_list_source_artifacts(
    project_id: ProjectId,
    region_code: RegionCode,
    set_id: Annotated[str, Path(alias="setId")],
    calibration_version: Annotated[str, Path(alias="calibrationVersion")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("calibration.read"))],
) -> dict[str, Any]:
    _check(ctx, project_id, region_code)
    row = await RoboticsService(session).require_calibration_version(
        project_id, region_code, set_id, calibration_version
    )
    return _embedded_page(row.source_artifacts, ctx)


@router.post(
    "/projects/{projectId}/regions/{regionCode}/calibration-sets/{setId}/versions/{calibrationVersion}/validations",
    operation_id="calibrationValidateVersion",
    status_code=status.HTTP_202_ACCEPTED,
)
async def calibration_validate_version(
    project_id: ProjectId,
    region_code: RegionCode,
    set_id: Annotated[str, Path(alias="setId")],
    calibration_version: Annotated[str, Path(alias="calibrationVersion")],
    body: CreateCalibrationValidationRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("calibration.validate"))],
) -> dict[str, Any]:
    operation_scope = _check(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        row = await service.require_calibration_version(
            project_id, region_code, set_id, calibration_version
        )
        service.check_etag(row.etag, if_match)
        if (
            body.expected_etag != row.etag
            or body.content_hash != row.content_hash
            or body.validation_context_hash != row.validation_context_hash
        ):
            raise VersionConflictError(code="CALIBRATION_VALIDATION_INPUT_CHANGED")
        report_id = new_id("finding")
        job = await service.create_job(
            operation_scope, "CALIBRATION_VALIDATION", "CALIBRATION_REPORT", report_id
        )
        report_data = {
            "id": report_id,
            "set_id": set_id,
            "version": calibration_version,
            "status": "PASSED",
            "content_hash": row.content_hash,
            "validation_context_hash": row.validation_context_hash,
            "findings": [],
            "job_id": job.job_id,
        }
        report = models.CalibrationReport(
            report_id=report_id,
            organization_id=ctx.organization_id,
            project_id=project_id,
            region_code=region_code,
            version_row_id=row.row_id,
            status="PASSED",
            report=report_data,
            job_id=job.job_id,
            created_at=utc_now(),
        )
        service.repo.add(report)
        await session.flush()
        await _record(
            session,
            ctx,
            "calibration.validation.requested",
            "calibration_report",
            report_id,
            {"job_id": job.job_id},
        )
        await _record(
            session,
            ctx,
            "calibration.validation.completed",
            "calibration_report",
            report_id,
            {"job_id": job.job_id, "outcome": "PASSED"},
        )
        response = _envelope({}, ctx)
        response.pop("data")
        response["job"] = job_projection(job)
        return response

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "calibrationValidateVersion", set_id, calibration_version),
        command,
    )


@router.get(
    "/projects/{projectId}/regions/{regionCode}/calibration-sets/{setId}/versions/{calibrationVersion}/reports",
    operation_id="calibrationListReports",
)
async def calibration_list_reports(
    project_id: ProjectId,
    region_code: RegionCode,
    set_id: Annotated[str, Path(alias="setId")],
    calibration_version: Annotated[str, Path(alias="calibrationVersion")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("calibration.read"))],
) -> dict[str, Any]:
    _check(ctx, project_id, region_code)
    service = RoboticsService(session)
    version = await service.require_calibration_version(
        project_id, region_code, set_id, calibration_version
    )
    rows = await service.repo.calibration_reports(version.row_id)
    return _embedded_page([row.report for row in rows], ctx)


@router.get(
    "/projects/{projectId}/regions/{regionCode}/calibration-reports/{reportId}",
    operation_id="calibrationGetReport",
)
async def calibration_get_report(
    project_id: ProjectId,
    region_code: RegionCode,
    report_id: Annotated[str, Path(alias="reportId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("calibration.read"))],
) -> dict[str, Any]:
    _check(ctx, project_id, region_code)
    row = await RoboticsService(session).repo.calibration_report(project_id, region_code, report_id)
    if row is None:
        raise NotFoundError(code="CALIBRATION_REPORT_NOT_FOUND")
    return _envelope(row.report, ctx)


@router.get(
    "/projects/{projectId}/regions/{regionCode}/calibration-sets/{setId}/versions/{calibrationVersion}/availability",
    operation_id="calibrationGetAvailability",
)
async def calibration_get_availability(
    project_id: ProjectId,
    region_code: RegionCode,
    set_id: Annotated[str, Path(alias="setId")],
    calibration_version: Annotated[str, Path(alias="calibrationVersion")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("calibration.read"))],
) -> dict[str, Any]:
    _check(ctx, project_id, region_code)
    row = await RoboticsService(session).require_calibration_version(
        project_id, region_code, set_id, calibration_version
    )
    return _envelope(row.availability, ctx)


@router.get(
    "/projects/{projectId}/regions/{regionCode}/calibration-sets/{setId}/binding-usages",
    operation_id="calibrationListBindingUsages",
)
async def calibration_list_binding_usages(
    project_id: ProjectId,
    region_code: RegionCode,
    set_id: Annotated[str, Path(alias="setId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("episode.read"))],
) -> dict[str, Any]:
    _check(ctx, project_id, region_code)
    await RoboticsService(session).require_calibration_set(project_id, region_code, set_id)
    return _embedded_page([], ctx)


@router.post(
    "/projects/{projectId}/regions/{regionCode}/calibration-sets/{setId}/versions/{calibrationVersion}:preflight-publish",
    operation_id="calibrationPreflightPublish",
)
async def calibration_preflight_publish(
    project_id: ProjectId,
    region_code: RegionCode,
    set_id: Annotated[str, Path(alias="setId")],
    calibration_version: Annotated[str, Path(alias="calibrationVersion")],
    body: PublishPreflightRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("calibration.publish"))],
) -> dict[str, Any]:
    operation_scope = _check(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        row = await service.require_calibration_version(
            project_id, region_code, set_id, calibration_version
        )
        service.check_mutable(row.lifecycle)
        service.check_etag(row.etag, if_match)
        if body.expected_etag != row.etag or body.expected_hash != row.content_hash:
            raise VersionConflictError(code="CALIBRATION_PUBLISH_INPUT_CHANGED")
        report = await service.repo.calibration_report(
            project_id, region_code, body.validation_report_id
        )
        if report is None or report.version_row_id != row.row_id or report.status != "PASSED":
            raise ValidationError(code="PASSING_VALIDATION_REQUIRED")
        data = await service.create_preflight(
            "calibrationPublishVersion",
            operation_scope,
            ctx.actor_id,
            row.row_id,
            row.etag,
            body.model_dump(mode="json"),
        )
        await _record(
            session,
            ctx,
            "calibration.version.publish_preflighted",
            "calibration_version",
            row.row_id,
        )
        return _envelope(data, ctx)

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "calibrationPreflightPublish", set_id, calibration_version),
        command,
    )


@router.post(
    "/projects/{projectId}/regions/{regionCode}/calibration-sets/{setId}/versions/{calibrationVersion}:publish",
    operation_id="calibrationPublishVersion",
)
async def calibration_publish_version(
    project_id: ProjectId,
    region_code: RegionCode,
    set_id: Annotated[str, Path(alias="setId")],
    calibration_version: Annotated[str, Path(alias="calibrationVersion")],
    body: TokenCommand,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    preflight_header: PreflightTokenHeader,
    ctx: Annotated[RequestContext, Depends(require("calibration.publish"))],
) -> dict[str, Any]:
    operation_scope = _check(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        row = await service.require_calibration_version(
            project_id, region_code, set_id, calibration_version
        )
        service.check_mutable(row.lifecycle)
        service.check_etag(row.etag, if_match)
        await service.consume_preflight(
            "calibrationPublishVersion",
            operation_scope,
            ctx.actor_id,
            row.row_id,
            body.preflight_token,
            preflight_header,
            row.etag,
        )
        now = utc_now()
        row.lifecycle = "READY"
        row.availability = {
            "state": "ACTIVE",
            "revision": "1",
            "consumers": ["CAPTURE", "VIEWER", "CLEANING", "EXPORT", "VALIDATION"],
            "valid_from": now.isoformat(),
            "valid_to": None,
            "content_hash": row.content_hash,
        }
        service.bump(row)
        calibration_set = await service.require_calibration_set(project_id, region_code, set_id)
        calibration_set.lifecycle = "READY"
        service.bump(calibration_set)
        await session.flush()
        await _record(
            session, ctx, "calibration.version.published", "calibration_version", row.row_id
        )
        return _envelope(calibration_version_projection(row), ctx)

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "calibrationPublishVersion", set_id, calibration_version),
        command,
    )


@router.get(
    "/projects/{projectId}/regions/{regionCode}/calibration-sets/{setId}/preview-context",
    operation_id="calibrationGetPreviewContext",
)
async def calibration_get_preview_context(
    project_id: ProjectId,
    region_code: RegionCode,
    set_id: Annotated[str, Path(alias="setId")],
    response: Response,
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("calibration.read", "robot.read"))],
) -> dict[str, Any]:
    _check(ctx, project_id, region_code)
    service = RoboticsService(session)
    calibration_set = await service.require_calibration_set(project_id, region_code, set_id)
    robot = await service.require_robot(project_id, region_code, calibration_set.robot_id)
    component = await service.require_component(
        project_id, region_code, calibration_set.component_id
    )
    response.headers["Cache-Control"] = "private, no-store"
    return _envelope(
        {
            "set_id": set_id,
            "robot_id": robot.robot_id,
            "robot_model_id": robot.robot_model_id,
            "component_id": component.component_id,
            "frames": component.frames,
            "asset_read_authorization_ref": new_id("event"),
            "expires_at": utc_now() + timedelta(minutes=5),
            "allowed_actions": ["VIEW"],
        },
        ctx,
    )


async def _latest_calibration_version(
    service: RoboticsService, project_id: str, region_code: str, set_id: str
) -> models.CalibrationVersion:
    versions = await service.repo.calibration_versions(project_id, region_code, set_id)
    if not versions:
        raise NotFoundError(code="CALIBRATION_VERSION_NOT_FOUND")
    return max(versions, key=lambda row: int(row.version) if row.version.isdigit() else -1)


def _validation_run_projection(report: models.CalibrationReport) -> dict[str, Any]:
    data = report.report
    return {
        "run_id": str(data.get("run_id") or report.report_id),
        "set_id": str(data.get("set_id") or report.version_row_id.split(":", 1)[0]),
        "status": "SUCCEEDED" if report.status == "PASSED" else report.status,
        "content_hash": data.get("content_hash"),
        "validation_context_hash": data.get("validation_context_hash"),
        "issues": data.get("issues", data.get("findings", [])),
        "allowed_actions": ["VIEW"],
        "blocked_reasons": [],
    }


@router.post(
    "/projects/{projectId}/regions/{regionCode}/calibration-jobs/{jobId}:cancel",
    operation_id="calibrationCancelJob",
)
async def calibration_cancel_job(
    project_id: ProjectId,
    region_code: RegionCode,
    job_id: Annotated[str, Path(alias="jobId")],
    body: EmptyCommand,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("calibration.validate"))],
) -> dict[str, Any]:
    operation_scope = _check(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        job = await service.repo.job(operation_scope, job_id)
        if job is None:
            raise NotFoundError(code="CALIBRATION_JOB_NOT_FOUND")
        service.check_etag(compute_etag(job.updated_at.isoformat()), if_match)
        if job.status not in {"QUEUED", "RUNNING"}:
            raise VersionConflictError(code="CALIBRATION_JOB_NOT_CANCELLABLE")
        job.status = "CANCELLED"
        job.updated_at = utc_now()
        await session.flush()
        await _record(session, ctx, "calibration.validation.cancelled", "job", job_id)
        return _envelope(job_projection(job), ctx)

    return await with_idempotency(
        session, idempotency_key, (operation_scope, "calibrationCancelJob", job_id), command
    )


@router.post(
    "/projects/{projectId}/regions/{regionCode}/calibration-jobs/{jobId}:retry",
    operation_id="calibrationRetryJob",
    status_code=status.HTTP_202_ACCEPTED,
)
async def calibration_retry_job(
    project_id: ProjectId,
    region_code: RegionCode,
    job_id: Annotated[str, Path(alias="jobId")],
    body: EmptyCommand,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("calibration.validate"))],
) -> dict[str, Any]:
    operation_scope = _check(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        job = await service.repo.job(operation_scope, job_id)
        if job is None:
            raise NotFoundError(code="CALIBRATION_JOB_NOT_FOUND")
        service.check_etag(compute_etag(job.updated_at.isoformat()), if_match)
        if job.status not in {"FAILED", "CANCELLED", "PARTIAL"}:
            raise VersionConflictError(code="CALIBRATION_JOB_NOT_RETRYABLE")
        retry = await service.create_job(
            operation_scope, job.job_type, job.resource_type, job.resource_id
        )
        retry.result_ref = {"retry_of_job_id": job_id}
        await session.flush()
        await _record(
            session,
            ctx,
            "calibration.validation.requested",
            "job",
            retry.job_id,
            {"retry_of_job_id": job_id},
        )
        response = _envelope({}, ctx)
        response.pop("data")
        response["job"] = job_projection(retry)
        return response

    return await with_idempotency(
        session, idempotency_key, (operation_scope, "calibrationRetryJob", job_id), command
    )


@router.get(
    "/projects/{projectId}/regions/{regionCode}/calibration-sets/{setId}/validation-runs",
    operation_id="calibrationListValidationRuns",
)
async def calibration_list_validation_runs(
    project_id: ProjectId,
    region_code: RegionCode,
    set_id: Annotated[str, Path(alias="setId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("calibration.read"))],
) -> dict[str, Any]:
    _check(ctx, project_id, region_code)
    service = RoboticsService(session)
    await service.require_calibration_set(project_id, region_code, set_id)
    versions = await service.repo.calibration_versions(project_id, region_code, set_id)
    reports = []
    for version in versions:
        reports.extend(await service.repo.calibration_reports(version.row_id))
    return _embedded_page([_validation_run_projection(row) for row in reports], ctx)


@router.get(
    "/projects/{projectId}/regions/{regionCode}/calibration-validation-runs/{runId}",
    operation_id="calibrationGetValidationRun",
)
async def calibration_get_validation_run(
    project_id: ProjectId,
    region_code: RegionCode,
    run_id: Annotated[str, Path(alias="runId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("calibration.read"))],
) -> dict[str, Any]:
    _check(ctx, project_id, region_code)
    report = await RoboticsService(session).repo.calibration_report(project_id, region_code, run_id)
    if report is None:
        raise NotFoundError(code="CALIBRATION_VALIDATION_RUN_NOT_FOUND")
    return _envelope(_validation_run_projection(report), ctx)


@router.post(
    "/projects/{projectId}/regions/{regionCode}/calibration-sets/{setId}/validations",
    operation_id="calibrationValidateSet",
    status_code=status.HTTP_202_ACCEPTED,
)
async def calibration_validate_set(
    project_id: ProjectId,
    region_code: RegionCode,
    set_id: Annotated[str, Path(alias="setId")],
    body: CalibrationSetValidationRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    ctx: Annotated[RequestContext, Depends(require("calibration.validate"))],
) -> dict[str, Any]:
    operation_scope = _check(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        calibration_set = await service.require_calibration_set(project_id, region_code, set_id)
        if body.expected_revision != calibration_set.resource_version:
            raise VersionConflictError(code="CALIBRATION_REVISION_CHANGED")
        version = await _latest_calibration_version(service, project_id, region_code, set_id)
        service.check_mutable(version.lifecycle)
        report_id = new_id("finding")
        job = await service.create_job(
            operation_scope, "CALIBRATION_VALIDATION", "CALIBRATION_REPORT", report_id
        )
        report = models.CalibrationReport(
            report_id=report_id,
            organization_id=ctx.organization_id,
            project_id=project_id,
            region_code=region_code,
            version_row_id=version.row_id,
            status="PASSED",
            report={
                "id": report_id,
                "run_id": report_id,
                "set_id": set_id,
                "status": "SUCCEEDED",
                "content_hash": version.content_hash,
                "validation_context_hash": version.validation_context_hash,
                "issues": [],
                "job_id": job.job_id,
            },
            job_id=job.job_id,
            created_at=utc_now(),
        )
        service.repo.add(report)
        preflight = await service.create_preflight(
            "calibrationPublishSet",
            operation_scope,
            ctx.actor_id,
            set_id,
            calibration_set.etag,
            {"version": version.version, "report_id": report_id},
        )
        await session.flush()
        await _record(
            session,
            ctx,
            "calibration.validation.requested",
            "calibration_set",
            set_id,
            {"job_id": job.job_id, "report_id": report_id},
        )
        await _record(
            session,
            ctx,
            "calibration.availability.changed",
            "calibration_set",
            set_id,
            {"job_id": job.job_id, "validation_run_id": report_id},
        )
        response = _envelope({}, ctx)
        response.pop("data")
        response["job"] = job_projection(job)
        response["preflight"] = preflight
        response["allowed_actions"] = ["PUBLISH"]
        response["blocked_reasons"] = []
        return response

    return await with_idempotency(
        session, idempotency_key, (operation_scope, "calibrationValidateSet", set_id), command
    )


@router.post(
    "/projects/{projectId}/regions/{regionCode}/calibration-sets/{setId}:publish",
    operation_id="calibrationPublishSet",
)
async def calibration_publish_set(
    project_id: ProjectId,
    region_code: RegionCode,
    set_id: Annotated[str, Path(alias="setId")],
    body: EmptyCommand,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    preflight_header: PreflightTokenHeader,
    ctx: Annotated[RequestContext, Depends(require("calibration.publish"))],
) -> dict[str, Any]:
    operation_scope = _check(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        calibration_set = await service.require_calibration_set(project_id, region_code, set_id)
        service.check_etag(calibration_set.etag, if_match)
        preflight = await service.consume_preflight(
            "calibrationPublishSet",
            operation_scope,
            ctx.actor_id,
            set_id,
            preflight_header,
            preflight_header,
            calibration_set.etag,
        )
        version = await service.require_calibration_version(
            project_id, region_code, set_id, str(preflight.payload["version"])
        )
        report = await service.repo.calibration_report(
            project_id, region_code, str(preflight.payload["report_id"])
        )
        if report is None or report.status != "PASSED":
            raise ValidationError(code="PASSING_VALIDATION_REQUIRED")
        now = utc_now()
        version.lifecycle = "READY"
        version.availability = {
            "state": "ACTIVE",
            "revision": "1",
            "consumers": ["CAPTURE", "VIEWER", "CLEANING", "EXPORT", "VALIDATION"],
            "valid_from": now.isoformat(),
            "valid_to": None,
            "content_hash": version.content_hash,
        }
        service.bump(version)
        calibration_set.lifecycle = "READY"
        service.bump(calibration_set)
        await session.flush()
        await _record(
            session, ctx, "calibration.version.published", "calibration_version", version.row_id
        )
        return _envelope(calibration_set_projection(calibration_set), ctx)

    return await with_idempotency(
        session, idempotency_key, (operation_scope, "calibrationPublishSet", set_id), command
    )


@router.post(
    "/projects/{projectId}/regions/{regionCode}/calibration-sets/{setId}:clone",
    operation_id="calibrationCloneSet",
    status_code=status.HTTP_201_CREATED,
)
async def calibration_clone_set(
    project_id: ProjectId,
    region_code: RegionCode,
    set_id: Annotated[str, Path(alias="setId")],
    body: CloneCalibrationSetRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("calibration.create"))],
) -> dict[str, Any]:
    operation_scope = _check(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        source = await service.require_calibration_set(project_id, region_code, set_id)
        service.check_etag(source.etag, if_match)
        source_version = await _latest_calibration_version(service, project_id, region_code, set_id)
        clone_body = CreateCalibrationSetRequest(
            robot_id=source.robot_id,
            component_id=source.component_id,
            display_name=f"{source.display_name} (clone)",
            valid_from=body.valid_from,
            valid_to=body.valid_to,
        )
        clone, version = await _create_set_and_version(
            service, ctx, project_id, region_code, clone_body, list(source_version.source_artifacts)
        )
        version.definition = dict(source_version.definition)
        version.content_hash = source_version.content_hash
        version.validation_context_hash = source_version.validation_context_hash
        await session.flush()
        await _record(
            session,
            ctx,
            "calibration.draft.created",
            "calibration_set",
            clone.set_id,
            {"source_set_id": set_id, "reason": body.reason},
        )
        return _envelope(
            {
                "calibration_set": calibration_set_projection(clone),
                "initial_version": calibration_version_projection(version),
            },
            ctx,
        )

    return await with_idempotency(
        session, idempotency_key, (operation_scope, "calibrationCloneSet", set_id), command
    )


CALIBRATION_SECTIONS = frozenset(
    {"intrinsics", "transforms", "time-calibrations", "joint-calibrations"}
)


def _section_records(version: models.CalibrationVersion, section: str) -> list[dict[str, Any]]:
    if section not in CALIBRATION_SECTIONS:
        raise NotFoundError(code="CALIBRATION_SECTION_NOT_FOUND")
    return list(version.definition.get("records", {}).get(section, []))


def _record_projection(set_id: str, section: str, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_id": item["record_id"],
        "set_id": set_id,
        "section": section,
        "revision": item["revision"],
        "record": item["record"],
        "allowed_actions": ["UPDATE", "DELETE"],
        "blocked_reasons": [],
    }


async def _draft_record_context(
    service: RoboticsService,
    project_id: str,
    region_code: str,
    set_id: str,
) -> tuple[models.CalibrationSet, models.CalibrationVersion]:
    calibration_set = await service.require_calibration_set(project_id, region_code, set_id)
    if calibration_set.lifecycle != "DRAFT":
        raise VersionConflictError(code="IMMUTABLE_RESOURCE")
    version = await _latest_calibration_version(service, project_id, region_code, set_id)
    service.check_mutable(version.lifecycle)
    return calibration_set, version


def _store_section_records(
    version: models.CalibrationVersion, section: str, records: list[dict[str, Any]]
) -> None:
    definition = dict(version.definition)
    sections = dict(definition.get("records", {}))
    sections[section] = records
    definition["records"] = sections
    version.definition = definition
    version.content_hash = canonical_hash(definition)


@router.get(
    "/projects/{projectId}/regions/{regionCode}/calibration-sets/{setId}/{section}",
    operation_id="calibrationListRecords",
)
async def calibration_list_records(
    project_id: ProjectId,
    region_code: RegionCode,
    set_id: Annotated[str, Path(alias="setId")],
    section: Annotated[str, Path()],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("calibration.read"))],
) -> dict[str, Any]:
    _check(ctx, project_id, region_code)
    service = RoboticsService(session)
    await service.require_calibration_set(project_id, region_code, set_id)
    version = await _latest_calibration_version(service, project_id, region_code, set_id)
    items = [_record_projection(set_id, section, row) for row in _section_records(version, section)]
    return _embedded_page(items, ctx)


@router.post(
    "/projects/{projectId}/regions/{regionCode}/calibration-sets/{setId}/{section}",
    operation_id="calibrationCreateRecord",
    status_code=status.HTTP_201_CREATED,
)
async def calibration_create_record(
    project_id: ProjectId,
    region_code: RegionCode,
    set_id: Annotated[str, Path(alias="setId")],
    section: Annotated[str, Path()],
    body: CalibrationRecordWriteRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("calibration.create"))],
) -> dict[str, Any]:
    operation_scope = _check(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        calibration_set, version = await _draft_record_context(
            service, project_id, region_code, set_id
        )
        service.check_etag(calibration_set.etag, if_match)
        records = _section_records(version, section)
        item = {
            "record_id": new_id("event"),
            "revision": "1",
            "record": body.record,
        }
        records.append(item)
        _store_section_records(version, section, records)
        service.bump(version)
        service.bump(calibration_set)
        await session.flush()
        await _record(
            session,
            ctx,
            "calibration.draft.updated",
            "calibration_set",
            set_id,
            {"section": section, "record_id": item["record_id"]},
        )
        return _envelope(_record_projection(set_id, section, item), ctx)

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "calibrationCreateRecord", set_id, section),
        command,
    )


@router.get(
    "/projects/{projectId}/regions/{regionCode}/calibration-sets/{setId}/{section}/{recordId}",
    operation_id="calibrationGetRecord",
)
async def calibration_get_record(
    project_id: ProjectId,
    region_code: RegionCode,
    set_id: Annotated[str, Path(alias="setId")],
    section: Annotated[str, Path()],
    record_id: Annotated[str, Path(alias="recordId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("calibration.read"))],
) -> dict[str, Any]:
    _check(ctx, project_id, region_code)
    service = RoboticsService(session)
    await service.require_calibration_set(project_id, region_code, set_id)
    version = await _latest_calibration_version(service, project_id, region_code, set_id)
    item = next(
        (row for row in _section_records(version, section) if row["record_id"] == record_id),
        None,
    )
    if item is None:
        raise NotFoundError(code="CALIBRATION_RECORD_NOT_FOUND")
    return _envelope(_record_projection(set_id, section, item), ctx)


@router.patch(
    "/projects/{projectId}/regions/{regionCode}/calibration-sets/{setId}/{section}/{recordId}",
    operation_id="calibrationUpdateRecord",
)
async def calibration_update_record(
    project_id: ProjectId,
    region_code: RegionCode,
    set_id: Annotated[str, Path(alias="setId")],
    section: Annotated[str, Path()],
    record_id: Annotated[str, Path(alias="recordId")],
    body: CalibrationRecordPatchRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("calibration.create"))],
) -> dict[str, Any]:
    operation_scope = _check(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        calibration_set, version = await _draft_record_context(
            service, project_id, region_code, set_id
        )
        service.check_etag(calibration_set.etag, if_match)
        records = _section_records(version, section)
        item = next((row for row in records if row["record_id"] == record_id), None)
        if item is None:
            raise NotFoundError(code="CALIBRATION_RECORD_NOT_FOUND")
        item["record"] = {**item["record"], **body.patch}
        item["revision"] = str(int(item["revision"]) + 1)
        _store_section_records(version, section, records)
        service.bump(version)
        service.bump(calibration_set)
        await session.flush()
        await _record(
            session,
            ctx,
            "calibration.draft.updated",
            "calibration_set",
            set_id,
            {"section": section, "record_id": record_id},
        )
        return _envelope(_record_projection(set_id, section, item), ctx)

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "calibrationUpdateRecord", set_id, section, record_id),
        command,
    )


@router.delete(
    "/projects/{projectId}/regions/{regionCode}/calibration-sets/{setId}/{section}/{recordId}",
    operation_id="calibrationDeleteRecord",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def calibration_delete_record(
    project_id: ProjectId,
    region_code: RegionCode,
    set_id: Annotated[str, Path(alias="setId")],
    section: Annotated[str, Path()],
    record_id: Annotated[str, Path(alias="recordId")],
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("calibration.create"))],
) -> Response:
    operation_scope = _check(ctx, project_id, region_code)

    async def command() -> dict[str, str]:
        service = RoboticsService(session)
        calibration_set, version = await _draft_record_context(
            service, project_id, region_code, set_id
        )
        service.check_etag(calibration_set.etag, if_match)
        records = _section_records(version, section)
        remaining = [row for row in records if row["record_id"] != record_id]
        if len(remaining) == len(records):
            raise NotFoundError(code="CALIBRATION_RECORD_NOT_FOUND")
        _store_section_records(version, section, remaining)
        service.bump(version)
        service.bump(calibration_set)
        await session.flush()
        await _record(
            session,
            ctx,
            "calibration.draft.updated",
            "calibration_set",
            set_id,
            {"section": section, "record_id": record_id, "deleted": True},
        )
        return {"deleted_record_id": record_id}

    await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "calibrationDeleteRecord", set_id, section, record_id),
        command,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
