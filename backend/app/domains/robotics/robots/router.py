"""P14/P15 routes: 43 robot model, binding, robot and component operations."""

from datetime import timedelta
from typing import Annotated, Any

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
    CompleteAssetUploadRequest,
    ComponentPreflightRequest,
    CreateAssetUploadSessionRequest,
    CreateRobotModelRequest,
    CreateRobotModelSampleValidationRequest,
    CreateRobotRequest,
    CreateValidationRequest,
    CreateVersionRequest,
    EmptyCommand,
    MountChangePreflightRequest,
    PublishPreflightRequest,
    ReasonPreflightRequest,
    RobotModelBindingPreflightRequest,
    TokenCommand,
    UploadAuthorizationRequest,
)
from ..service import (
    CONTRACT_VERSION,
    RoboticsService,
    as_utc,
    binding_projection,
    canonical_hash,
    component_projection,
    job_projection,
    model_version_projection,
    robot_model_projection,
    robot_projection,
    scope_key,
    upload_projection,
    utc_now,
)

router = APIRouter(tags=["robotics"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
OrganizationId = Annotated[str, Path(alias="organizationId", min_length=1, max_length=160)]
ProjectId = Annotated[str, Path(alias="projectId", min_length=1, max_length=160)]
RegionCode = Annotated[str, Path(alias="regionCode", min_length=1, max_length=64)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)]
IfMatch = Annotated[str, Header(alias="If-Match", min_length=3, max_length=256)]
PreflightTokenHeader = Annotated[
    str, Header(alias="X-Preflight-Token", min_length=16, max_length=512)
]


def _scope(ctx: RequestContext) -> dict[str, str]:
    return {
        "organization_id": ctx.organization_id,
        "project_id": ctx.project_id,
        "region_code": ctx.region_code,
    }


def _check_org(ctx: RequestContext, organization_id: str) -> str:
    if ctx.organization_id != organization_id:
        raise NotFoundError(code="RESOURCE_NOT_FOUND")
    return scope_key(ctx.organization_id, ctx.project_id, ctx.region_code)


def _check_region(ctx: RequestContext, project_id: str, region_code: str) -> str:
    if ctx.project_id != project_id or ctx.region_code != region_code:
        raise NotFoundError(code="RESOURCE_NOT_FOUND")
    return scope_key(ctx.organization_id, project_id, region_code)


def _check_project(ctx: RequestContext, project_id: str) -> str:
    if ctx.project_id != project_id:
        raise NotFoundError(code="RESOURCE_NOT_FOUND")
    return scope_key(ctx.organization_id, project_id, ctx.region_code)


def _envelope(data: Any, ctx: RequestContext) -> dict[str, Any]:
    return {
        "data": data,
        "scope": _scope(ctx),
        "request_id": ctx.request_id,
        "contract_version": CONTRACT_VERSION,
    }


def _page(
    rows: list[Any], params: CursorParams, fields: tuple[str, str], ctx: RequestContext
) -> dict[str, Any]:
    return {
        **build_page(rows, params, fields),
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


@router.get("/organizations/{organizationId}/robot-models", operation_id="listRobotModels")
async def list_robot_models(
    organization_id: OrganizationId,
    session: SessionDep,
    params: Annotated[CursorParams, Depends(cursor_params)],
    ctx: Annotated[RequestContext, Depends(require("robot_model.read"))],
) -> dict[str, Any]:
    _check_org(ctx, organization_id)
    rows = await RoboticsService(session).repo.robot_models(organization_id, params.limit + 1)
    page = _page(rows, params, ("created_at", "model_id"), ctx)
    page["items"] = [robot_model_projection(row) for row in page["items"]]
    return page


@router.post(
    "/organizations/{organizationId}/robot-models",
    operation_id="createRobotModel",
    status_code=status.HTTP_201_CREATED,
)
async def create_robot_model(
    organization_id: OrganizationId,
    body: CreateRobotModelRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    ctx: Annotated[RequestContext, Depends(require("robot_model.create"))],
) -> dict[str, Any]:
    operation_scope = _check_org(ctx, organization_id)

    async def command() -> dict[str, Any]:
        now = utc_now()
        model_id = new_id("object")
        version_id = new_id("object")
        model = models.RobotModel(
            model_id=model_id,
            organization_id=organization_id,
            manufacturer=body.manufacturer,
            normalized_manufacturer=body.manufacturer.casefold().strip(),
            model_code=body.model_code,
            normalized_model_code=body.model_code.casefold().strip(),
            display_name=body.display_name,
            current_published_version_id=None,
            status="ACTIVE",
            resource_version="1",
            etag=compute_etag("1"),
            created_at=now,
            updated_at=now,
        )
        version = models.RobotModelVersion(
            version_id=version_id,
            organization_id=organization_id,
            model_id=model_id,
            version_label="1",
            lifecycle="DRAFT",
            upload_status="IDLE",
            validation_status="UNKNOWN",
            asset_availability="UNKNOWN",
            publish_readiness="CONFIGURATION_REQUIRED",
            manifest_hash=None,
            validation_input_hash=canonical_hash(
                {"manifest": None, "configuration": {}, "mappings": {}}
            ),
            manifest={"entry_path": None, "assets": []},
            configuration={},
            joint_mappings={"context": None, "mappings": []},
            sample_candidates=[],
            resource_version="1",
            etag=compute_etag("1"),
            created_at=now,
            updated_at=now,
        )
        session.add_all([model, version])
        await session.flush()
        await _record(
            session,
            ctx,
            "robot_model.draft.created",
            "robot_model",
            model_id,
            {"version_id": version_id, "version_label": "1"},
        )
        return _envelope(
            {
                "robot_model": robot_model_projection(model),
                "initial_version": model_version_projection(version),
            },
            ctx,
        )

    return await with_idempotency(
        session, idempotency_key, (operation_scope, "createRobotModel"), command
    )


@router.get("/organizations/{organizationId}/robot-models/{modelId}", operation_id="getRobotModel")
async def get_robot_model(
    organization_id: OrganizationId,
    model_id: Annotated[str, Path(alias="modelId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("robot_model.read"))],
) -> dict[str, Any]:
    _check_org(ctx, organization_id)
    row = await RoboticsService(session).require_robot_model(organization_id, model_id)
    return _envelope(robot_model_projection(row), ctx)


@router.get(
    "/organizations/{organizationId}/robot-models/{modelId}/versions",
    operation_id="listRobotModelVersions",
)
async def list_robot_model_versions(
    organization_id: OrganizationId,
    model_id: Annotated[str, Path(alias="modelId")],
    session: SessionDep,
    params: Annotated[CursorParams, Depends(cursor_params)],
    ctx: Annotated[RequestContext, Depends(require("robot_model.read"))],
) -> dict[str, Any]:
    _check_org(ctx, organization_id)
    service = RoboticsService(session)
    await service.require_robot_model(organization_id, model_id)
    rows = await service.repo.model_versions(organization_id, model_id, params.limit + 1)
    page = _page(rows, params, ("created_at", "version_id"), ctx)
    page["items"] = [model_version_projection(row) for row in page["items"]]
    return page


@router.post(
    "/organizations/{organizationId}/robot-models/{modelId}/versions",
    operation_id="createRobotModelVersion",
    status_code=status.HTTP_201_CREATED,
)
async def create_robot_model_version(
    organization_id: OrganizationId,
    model_id: Annotated[str, Path(alias="modelId")],
    body: CreateVersionRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("robot_model.create"))],
) -> dict[str, Any]:
    operation_scope = _check_org(ctx, organization_id)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        model = await service.require_robot_model(organization_id, model_id)
        service.check_etag(model.etag, if_match)
        versions = await service.repo.model_versions(organization_id, model_id, 1000)
        base = None
        if body.base_version_id:
            base = await service.require_model_version(organization_id, body.base_version_id)
            if base.model_id != model_id:
                raise NotFoundError(code="BASE_VERSION_NOT_FOUND")
        label = str(
            max([int(row.version_label) for row in versions if row.version_label.isdigit()] or [0])
            + 1
        )
        now = utc_now()
        row = models.RobotModelVersion(
            version_id=new_id("object"),
            organization_id=organization_id,
            model_id=model_id,
            version_label=label,
            lifecycle="DRAFT",
            upload_status="IDLE",
            validation_status="UNKNOWN",
            asset_availability="UNKNOWN",
            publish_readiness="CONFIGURATION_REQUIRED",
            manifest_hash=None,
            validation_input_hash=canonical_hash({"base": body.base_version_id}),
            manifest=dict(base.manifest) if base else {"entry_path": None, "assets": []},
            configuration=dict(base.configuration) if base else {},
            joint_mappings=dict(base.joint_mappings) if base else {"context": None, "mappings": []},
            sample_candidates=list(base.sample_candidates) if base else [],
            resource_version="1",
            etag=compute_etag("1"),
            created_at=now,
            updated_at=now,
        )
        service.repo.add(row)
        service.bump(model)
        await session.flush()
        await _record(
            session,
            ctx,
            "robot_model.draft.created",
            "robot_model_version",
            row.version_id,
            {"model_id": model_id, "version_label": label, "base_version_id": body.base_version_id},
        )
        return _envelope(model_version_projection(row), ctx)

    return await with_idempotency(
        session, idempotency_key, (operation_scope, "createRobotModelVersion", model_id), command
    )


@router.get(
    "/organizations/{organizationId}/robot-model-versions/{versionId}",
    operation_id="getRobotModelVersion",
)
async def get_robot_model_version(
    organization_id: OrganizationId,
    version_id: Annotated[str, Path(alias="versionId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("robot_model.read"))],
) -> dict[str, Any]:
    _check_org(ctx, organization_id)
    row = await RoboticsService(session).require_model_version(organization_id, version_id)
    return _envelope(model_version_projection(row), ctx)


@router.get(
    "/organizations/{organizationId}/robot-model-versions/{versionId}/asset-manifest",
    operation_id="getRobotAssetManifest",
)
async def get_robot_asset_manifest(
    organization_id: OrganizationId,
    version_id: Annotated[str, Path(alias="versionId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("robot_model.read"))],
) -> dict[str, Any]:
    _check_org(ctx, organization_id)
    row = await RoboticsService(session).require_model_version(organization_id, version_id)
    return _envelope(
        {
            "robot_model_version_id": row.version_id,
            "manifest_hash": row.manifest_hash,
            "entry_path": row.manifest.get("entry_path"),
            "assets": [
                {key: value for key, value in item.items() if key != "provider_object_id"}
                for item in row.manifest.get("assets", [])
            ],
        },
        ctx,
    )


@router.get(
    "/organizations/{organizationId}/robot-model-versions/{versionId}/viewer-manifest",
    operation_id="getRobotModelViewerManifest",
)
async def get_robot_model_viewer_manifest(
    organization_id: OrganizationId,
    version_id: Annotated[str, Path(alias="versionId")],
    response: Response,
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("robot_model.read"))],
) -> dict[str, Any]:
    _check_org(ctx, organization_id)
    row = await RoboticsService(session).require_model_version(organization_id, version_id)
    if row.asset_availability != "AVAILABLE":
        raise GoneError(code="ROBOT_MODEL_ASSETS_UNAVAILABLE")
    response.headers["Cache-Control"] = "private, no-store"
    expires_at = utc_now() + timedelta(minutes=5)
    assets = [
        {
            "relative_path": item["relative_path"],
            "role": item["role"],
            "sha256": item["sha256"],
            "read_authorization_ref": new_id("event"),
        }
        for item in row.manifest.get("assets", [])
    ]
    return _envelope(
        {
            "robot_model_version_id": row.version_id,
            "manifest_hash": row.manifest_hash,
            "assets": assets,
            "expires_at": expires_at,
            "allowed_actions": ["VIEW"],
        },
        ctx,
    )


@router.post(
    "/organizations/{organizationId}/robot-model-versions/{versionId}/upload-sessions",
    operation_id="createRobotAssetUploadSession",
    status_code=status.HTTP_201_CREATED,
)
async def create_robot_asset_upload_session(
    organization_id: OrganizationId,
    version_id: Annotated[str, Path(alias="versionId")],
    body: CreateAssetUploadSessionRequest,
    response: Response,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    ctx: Annotated[RequestContext, Depends(require("robot_model.create"))],
) -> dict[str, Any]:
    operation_scope = _check_org(ctx, organization_id)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        version = await service.require_model_version(organization_id, version_id)
        service.check_mutable(version.lifecycle)
        now = utc_now()
        objects = [
            {"object_id": new_id("object"), **item.model_dump(mode="json")} for item in body.objects
        ]
        row = models.RobotAssetUploadSession(
            upload_session_id=new_id("upload"),
            organization_id=organization_id,
            version_id=version_id,
            status="OPEN",
            objects=objects,
            expires_at=now + timedelta(hours=24),
            created_at=now,
            updated_at=now,
        )
        service.repo.add(row)
        version.upload_status = "QUEUED"
        service.bump(version)
        await session.flush()
        await _record(
            session,
            ctx,
            "robot_model.asset_upload.created",
            "robot_asset_upload_session",
            row.upload_session_id,
            {"version_id": version_id, "object_count": len(objects)},
        )
        response.headers["Cache-Control"] = "private, no-store"
        return _envelope(upload_projection(row), ctx)

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "createRobotAssetUploadSession", version_id),
        command,
    )


@router.get(
    "/organizations/{organizationId}/robot-asset-upload-sessions/{uploadSessionId}",
    operation_id="getRobotAssetUploadSession",
)
async def get_robot_asset_upload_session(
    organization_id: OrganizationId,
    upload_session_id: Annotated[str, Path(alias="uploadSessionId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("robot_model.create"))],
) -> dict[str, Any]:
    _check_org(ctx, organization_id)
    row = await RoboticsService(session).repo.upload_session(organization_id, upload_session_id)
    if row is None:
        raise NotFoundError(code="UPLOAD_SESSION_NOT_FOUND")
    return _envelope(upload_projection(row), ctx)


@router.post(
    "/organizations/{organizationId}/robot-asset-upload-sessions/{uploadSessionId}:authorize",
    operation_id="authorizeRobotAssetUploadSession",
)
async def authorize_robot_asset_upload_session(
    organization_id: OrganizationId,
    upload_session_id: Annotated[str, Path(alias="uploadSessionId")],
    body: UploadAuthorizationRequest,
    response: Response,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    ctx: Annotated[RequestContext, Depends(require("robot_model.create"))],
) -> dict[str, Any]:
    operation_scope = _check_org(ctx, organization_id)

    async def command() -> dict[str, Any]:
        row = await RoboticsService(session).repo.upload_session(organization_id, upload_session_id)
        if row is None:
            raise NotFoundError(code="UPLOAD_SESSION_NOT_FOUND")
        if row.status != "OPEN":
            raise VersionConflictError(code="UPLOAD_SESSION_NOT_OPEN")
        known = {item["object_id"] for item in row.objects}
        if not set(body.object_ids) <= known:
            raise NotFoundError(code="UPLOAD_OBJECT_NOT_FOUND")
        expires_at = utc_now() + timedelta(minutes=5)
        grants = [
            {"object_id": object_id, "authorization_ref": new_id("event"), "expires_at": expires_at}
            for object_id in body.object_ids
        ]
        await _record(
            session,
            ctx,
            "robot_model.asset_upload.authorized",
            "robot_asset_upload_session",
            upload_session_id,
            {"object_count": len(grants)},
        )
        response.headers["Cache-Control"] = "private, no-store"
        return _envelope({"upload_session_id": upload_session_id, "grants": grants}, ctx)

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "authorizeRobotAssetUploadSession", upload_session_id),
        command,
    )


@router.post(
    "/organizations/{organizationId}/robot-asset-upload-sessions/{uploadSessionId}:complete",
    operation_id="completeRobotAssetUpload",
)
async def complete_robot_asset_upload(
    organization_id: OrganizationId,
    upload_session_id: Annotated[str, Path(alias="uploadSessionId")],
    body: CompleteAssetUploadRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    ctx: Annotated[RequestContext, Depends(require("robot_model.create"))],
) -> dict[str, Any]:
    operation_scope = _check_org(ctx, organization_id)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        upload = await service.repo.upload_session(organization_id, upload_session_id)
        if upload is None:
            raise NotFoundError(code="UPLOAD_SESSION_NOT_FOUND")
        if upload.status != "OPEN":
            raise VersionConflictError(code="UPLOAD_SESSION_NOT_OPEN")
        declared = {item["relative_path"]: item for item in upload.objects}
        observed = {item.relative_path: item for item in body.objects}
        if set(declared) != set(observed):
            raise ValidationError(code="UPLOAD_OBJECT_SET_MISMATCH")
        assets = []
        for path, fact in observed.items():
            expected = declared[path]
            if expected["bytes"] != fact.bytes or expected["sha256"] != fact.sha256:
                raise ValidationError(code="UPLOAD_OBJECT_FACT_MISMATCH")
            assets.append({**expected, "provider_object_id": fact.provider_object_id})
        version = await service.require_model_version(organization_id, upload.version_id)
        service.check_mutable(version.lifecycle)
        entry = next(
            (item["relative_path"] for item in assets if item["role"] == "ENTRY_URDF"), None
        )
        if entry is None:
            raise ValidationError(code="ENTRY_URDF_REQUIRED")
        upload.status = "COMPLETED"
        upload.updated_at = utc_now()
        version.upload_status = "SUCCEEDED"
        version.asset_availability = "AVAILABLE"
        version.manifest_hash = body.manifest_hash
        version.manifest = {"entry_path": entry, "assets": assets}
        version.validation_input_hash = canonical_hash(
            {
                "manifest": body.manifest_hash,
                "configuration": version.configuration,
                "mappings": version.joint_mappings,
            }
        )
        version.publish_readiness = "MAPPING_REQUIRED"
        service.bump(version)
        await session.flush()
        await _record(
            session,
            ctx,
            "robot_model.asset_upload.completed",
            "robot_asset_upload_session",
            upload_session_id,
            {"version_id": version.version_id, "manifest_hash": body.manifest_hash},
        )
        return _envelope(upload_projection(upload), ctx)

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "completeRobotAssetUpload", upload_session_id),
        command,
    )


@router.post(
    "/organizations/{organizationId}/robot-asset-upload-sessions/{uploadSessionId}:cancel",
    operation_id="cancelRobotAssetUpload",
)
async def cancel_robot_asset_upload(
    organization_id: OrganizationId,
    upload_session_id: Annotated[str, Path(alias="uploadSessionId")],
    _body: EmptyCommand,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    ctx: Annotated[RequestContext, Depends(require("robot_model.create"))],
) -> dict[str, Any]:
    operation_scope = _check_org(ctx, organization_id)

    async def command() -> dict[str, Any]:
        row = await RoboticsService(session).repo.upload_session(organization_id, upload_session_id)
        if row is None:
            raise NotFoundError(code="UPLOAD_SESSION_NOT_FOUND")
        if row.status == "COMPLETED":
            raise VersionConflictError(code="UPLOAD_ALREADY_COMPLETED")
        row.status = "CANCELLED"
        row.updated_at = utc_now()
        await session.flush()
        await _record(
            session,
            ctx,
            "robot_model.asset_upload.cancelled",
            "robot_asset_upload_session",
            upload_session_id,
        )
        return _envelope(upload_projection(row), ctx)

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "cancelRobotAssetUpload", upload_session_id),
        command,
    )


@router.get(
    "/organizations/{organizationId}/robot-model-versions/{versionId}/validations",
    operation_id="listRobotModelValidations",
)
async def list_robot_model_validations(
    organization_id: OrganizationId,
    version_id: Annotated[str, Path(alias="versionId")],
    session: SessionDep,
    params: Annotated[CursorParams, Depends(cursor_params)],
    ctx: Annotated[RequestContext, Depends(require("robot_model.read"))],
) -> dict[str, Any]:
    _check_org(ctx, organization_id)
    service = RoboticsService(session)
    await service.require_model_version(organization_id, version_id)
    rows = await service.repo.validation_reports(organization_id, version_id, params.limit + 1)
    page = _page(rows, params, ("created_at", "report_id"), ctx)
    page["items"] = [row.report for row in page["items"]]
    return page


@router.post(
    "/organizations/{organizationId}/robot-model-versions/{versionId}/validations",
    operation_id="createRobotModelValidation",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_robot_model_validation(
    organization_id: OrganizationId,
    version_id: Annotated[str, Path(alias="versionId")],
    body: CreateValidationRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    ctx: Annotated[RequestContext, Depends(require("robot_model.validate"))],
) -> dict[str, Any]:
    operation_scope = _check_org(ctx, organization_id)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        version = await service.require_model_version(organization_id, version_id)
        if version.validation_input_hash != body.validation_input_hash:
            raise VersionConflictError(code="VALIDATION_INPUT_CHANGED")
        report_id = new_id("finding")
        job = await service.create_job(
            operation_scope, "ROBOT_MODEL_VALIDATION", "VALIDATION_REPORT", report_id
        )
        report_data = {
            "id": report_id,
            "robot_model_version_id": version_id,
            "status": "PASSED",
            "validation_input_hash": body.validation_input_hash,
            "findings": [],
            "job_id": job.job_id,
            "created_at": utc_now().isoformat(),
        }
        report = models.RobotValidationReport(
            report_id=report_id,
            organization_id=organization_id,
            version_id=version_id,
            status="PASSED",
            input_hash=body.validation_input_hash,
            report=report_data,
            job_id=job.job_id,
            created_at=utc_now(),
        )
        service.repo.add(report)
        version.validation_status = "PASSED"
        version.publish_readiness = "READY"
        service.bump(version)
        await session.flush()
        await _record(
            session,
            ctx,
            "robot_model.validation.requested",
            "robot_model_validation_report",
            report_id,
            {"version_id": version_id, "job_id": job.job_id},
        )
        await _record(
            session,
            ctx,
            "robot_model.validation.completed",
            "robot_model_validation_report",
            report_id,
            {"version_id": version_id, "job_id": job.job_id, "outcome": "PASSED"},
        )
        response = _envelope({}, ctx)
        response.pop("data")
        response["job"] = job_projection(job)
        return response

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "createRobotModelValidation", version_id),
        command,
    )


@router.get(
    "/organizations/{organizationId}/robot-model-validation-reports/{reportId}",
    operation_id="getRobotModelValidation",
)
async def get_robot_model_validation(
    organization_id: OrganizationId,
    report_id: Annotated[str, Path(alias="reportId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("robot_model.read"))],
) -> dict[str, Any]:
    _check_org(ctx, organization_id)
    row = await RoboticsService(session).repo.validation_report(organization_id, report_id)
    if row is None:
        raise NotFoundError(code="VALIDATION_REPORT_NOT_FOUND")
    return _envelope(row.report, ctx)


@router.get(
    "/organizations/{organizationId}/robot-model-versions/{versionId}/configuration",
    operation_id="getRobotModelConfiguration",
)
async def get_robot_model_configuration(
    organization_id: OrganizationId,
    version_id: Annotated[str, Path(alias="versionId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("robot_model.read"))],
) -> dict[str, Any]:
    _check_org(ctx, organization_id)
    row = await RoboticsService(session).require_model_version(organization_id, version_id)
    return _envelope({**row.configuration, "etag": row.etag}, ctx)


@router.put(
    "/organizations/{organizationId}/robot-model-versions/{versionId}/configuration",
    operation_id="updateRobotModelConfiguration",
)
async def update_robot_model_configuration(
    organization_id: OrganizationId,
    version_id: Annotated[str, Path(alias="versionId")],
    body: dict[str, Any],
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("robot_model.create"))],
) -> dict[str, Any]:
    operation_scope = _check_org(ctx, organization_id)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        row = await service.require_model_version(organization_id, version_id)
        service.check_mutable(row.lifecycle)
        service.check_etag(row.etag, if_match)
        row.configuration = body
        row.validation_status = "UNKNOWN"
        row.validation_input_hash = canonical_hash(
            {"manifest": row.manifest_hash, "configuration": body, "mappings": row.joint_mappings}
        )
        row.publish_readiness = "MAPPING_REQUIRED"
        service.bump(row)
        await session.flush()
        await _record(
            session,
            ctx,
            "robot_model.draft.updated",
            "robot_model_version",
            version_id,
            {"change_kind": "CONFIGURATION"},
        )
        return _envelope({**row.configuration, "etag": row.etag}, ctx)

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "updateRobotModelConfiguration", version_id),
        command,
    )


@router.get(
    "/organizations/{organizationId}/robot-model-versions/{versionId}/joint-mappings",
    operation_id="getRobotJointMappings",
)
async def get_robot_joint_mappings(
    organization_id: OrganizationId,
    version_id: Annotated[str, Path(alias="versionId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("robot_model.read"))],
) -> dict[str, Any]:
    _check_org(ctx, organization_id)
    row = await RoboticsService(session).require_model_version(organization_id, version_id)
    return _envelope({**row.joint_mappings, "etag": row.etag}, ctx)


@router.put(
    "/organizations/{organizationId}/robot-model-versions/{versionId}/joint-mappings",
    operation_id="updateRobotJointMappings",
)
async def update_robot_joint_mappings(
    organization_id: OrganizationId,
    version_id: Annotated[str, Path(alias="versionId")],
    body: dict[str, Any],
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("robot_model.create"))],
) -> dict[str, Any]:
    operation_scope = _check_org(ctx, organization_id)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        row = await service.require_model_version(organization_id, version_id)
        service.check_mutable(row.lifecycle)
        service.check_etag(row.etag, if_match)
        row.joint_mappings = body
        row.validation_status = "UNKNOWN"
        row.validation_input_hash = canonical_hash(
            {"manifest": row.manifest_hash, "configuration": row.configuration, "mappings": body}
        )
        row.publish_readiness = "SAMPLE_VALIDATION_REQUIRED"
        service.bump(row)
        await session.flush()
        await _record(
            session,
            ctx,
            "robot_model.mapping.updated",
            "robot_model_version",
            version_id,
            {"mapping_kind": "JOINT"},
        )
        return _envelope({**row.joint_mappings, "etag": row.etag}, ctx)

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "updateRobotJointMappings", version_id),
        command,
    )


@router.post(
    "/organizations/{organizationId}/robot-model-versions/{versionId}:preflight-publish",
    operation_id="preflightRobotModelPublish",
)
async def preflight_robot_model_publish(
    organization_id: OrganizationId,
    version_id: Annotated[str, Path(alias="versionId")],
    body: PublishPreflightRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("robot_model.publish"))],
) -> dict[str, Any]:
    operation_scope = _check_org(ctx, organization_id)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        row = await service.require_model_version(organization_id, version_id)
        service.check_mutable(row.lifecycle)
        service.check_etag(row.etag, if_match)
        if body.expected_etag != row.etag or body.expected_hash != row.validation_input_hash:
            raise VersionConflictError(code="PUBLISH_INPUT_CHANGED")
        report = await service.repo.validation_report(organization_id, body.validation_report_id)
        if report is None or report.version_id != version_id or report.status != "PASSED":
            raise ValidationError(code="PASSING_VALIDATION_REQUIRED")
        data = await service.create_preflight(
            "publishRobotModelVersion",
            operation_scope,
            ctx.actor_id,
            version_id,
            row.etag,
            body.model_dump(mode="json"),
        )
        await _record(
            session,
            ctx,
            "robot_model.version.publish_preflighted",
            "robot_model_version",
            version_id,
        )
        return _envelope(data, ctx)

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "preflightRobotModelPublish", version_id),
        command,
    )


@router.post(
    "/organizations/{organizationId}/robot-model-versions/{versionId}:publish",
    operation_id="publishRobotModelVersion",
)
async def publish_robot_model_version(
    organization_id: OrganizationId,
    version_id: Annotated[str, Path(alias="versionId")],
    body: TokenCommand,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    preflight_header: PreflightTokenHeader,
    ctx: Annotated[RequestContext, Depends(require("robot_model.publish"))],
) -> dict[str, Any]:
    operation_scope = _check_org(ctx, organization_id)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        row = await service.require_model_version(organization_id, version_id)
        service.check_mutable(row.lifecycle)
        service.check_etag(row.etag, if_match)
        await service.consume_preflight(
            "publishRobotModelVersion",
            operation_scope,
            ctx.actor_id,
            version_id,
            body.preflight_token,
            preflight_header,
            row.etag,
        )
        if row.publish_readiness != "READY":
            raise VersionConflictError(code="ROBOT_MODEL_NOT_READY")
        row.lifecycle = "PUBLISHED"
        service.bump(row)
        model = await service.require_robot_model(organization_id, row.model_id)
        model.current_published_version_id = row.version_id
        service.bump(model)
        await session.flush()
        await _record(
            session,
            ctx,
            "robot_model.version.published",
            "robot_model_version",
            version_id,
            {"model_id": row.model_id},
        )
        return _envelope(model_version_projection(row), ctx)

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "publishRobotModelVersion", version_id),
        command,
    )


@router.post(
    "/organizations/{organizationId}/robot-model-versions/{versionId}:preflight-disable",
    operation_id="preflightRobotModelDisable",
)
async def preflight_robot_model_disable(
    organization_id: OrganizationId,
    version_id: Annotated[str, Path(alias="versionId")],
    body: ReasonPreflightRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("robot_model.disable"))],
) -> dict[str, Any]:
    operation_scope = _check_org(ctx, organization_id)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        row = await service.require_model_version(organization_id, version_id)
        service.check_etag(row.etag, if_match)
        if row.lifecycle != "PUBLISHED":
            raise VersionConflictError(code="ROBOT_MODEL_VERSION_NOT_PUBLISHED")
        data = await service.create_preflight(
            "disableRobotModelVersion",
            operation_scope,
            ctx.actor_id,
            version_id,
            row.etag,
            body.model_dump(mode="json"),
        )
        return _envelope(data, ctx)

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "preflightRobotModelDisable", version_id),
        command,
    )


@router.post(
    "/organizations/{organizationId}/robot-model-versions/{versionId}:disable",
    operation_id="disableRobotModelVersion",
)
async def disable_robot_model_version(
    organization_id: OrganizationId,
    version_id: Annotated[str, Path(alias="versionId")],
    body: TokenCommand,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    preflight_header: PreflightTokenHeader,
    ctx: Annotated[RequestContext, Depends(require("robot_model.disable"))],
) -> dict[str, Any]:
    operation_scope = _check_org(ctx, organization_id)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        row = await service.require_model_version(organization_id, version_id)
        service.check_etag(row.etag, if_match)
        await service.consume_preflight(
            "disableRobotModelVersion",
            operation_scope,
            ctx.actor_id,
            version_id,
            body.preflight_token,
            preflight_header,
            row.etag,
        )
        if row.lifecycle != "PUBLISHED":
            raise VersionConflictError(code="ROBOT_MODEL_VERSION_NOT_PUBLISHED")
        row.lifecycle = "DISABLED"
        service.bump(row)
        await session.flush()
        await _record(
            session, ctx, "robot_model.version.disabled", "robot_model_version", version_id
        )
        return _envelope(model_version_projection(row), ctx)

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "disableRobotModelVersion", version_id),
        command,
    )


@router.get("/projects/{projectId}/robot-model-bindings", operation_id="listRobotModelBindings")
async def list_robot_model_bindings(
    project_id: ProjectId,
    session: SessionDep,
    params: Annotated[CursorParams, Depends(cursor_params)],
    ctx: Annotated[RequestContext, Depends(require("robot_model_binding.read"))],
) -> dict[str, Any]:
    _check_project(ctx, project_id)
    rows = await RoboticsService(session).repo.bindings(project_id, params.limit + 1)
    page = _page(rows, params, ("valid_from", "binding_id"), ctx)
    page["items"] = [binding_projection(row) for row in page["items"]]
    return page


@router.post(
    "/projects/{projectId}/robot-model-bindings:preflight",
    operation_id="preflightRobotModelBindings",
)
async def preflight_robot_model_bindings(
    project_id: ProjectId,
    body: RobotModelBindingPreflightRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("robot_model_binding.manage"))],
) -> dict[str, Any]:
    operation_scope = _check_project(ctx, project_id)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        existing = await service.repo.bindings(project_id, 1000)
        binding_etag = compute_etag(str(len(existing)))
        service.check_etag(binding_etag, if_match)
        version = await service.require_model_version(
            ctx.organization_id, body.robot_model_version_id
        )
        if version.lifecycle != "PUBLISHED":
            raise VersionConflictError(code="BINDING_VERSION_NOT_PUBLISHED")
        target_results = []
        for target in body.targets:
            if target.scope_type == "ROBOT_INSTANCE":
                robot = await service.require_robot(project_id, ctx.region_code, target.scope_id)
                if robot.robot_model_id != version.model_id:
                    raise VersionConflictError(code="ROBOT_MODEL_BINDING_INCOMPATIBLE")
            target_results.append(
                {"scope_type": target.scope_type, "scope_id": target.scope_id, "allowed": True}
            )
        data = await service.create_preflight(
            "createRobotModelBindings",
            operation_scope,
            ctx.actor_id,
            body.robot_model_version_id,
            binding_etag,
            body.model_dump(mode="json"),
        )
        data["target_results"] = target_results
        await _record(
            session,
            ctx,
            "robot_model.binding.preflighted",
            "robot_model_version",
            body.robot_model_version_id,
        )
        return _envelope(data, ctx)

    return await with_idempotency(
        session, idempotency_key, (operation_scope, "preflightRobotModelBindings"), command
    )


@router.post("/projects/{projectId}/robot-model-bindings", operation_id="createRobotModelBindings")
async def create_robot_model_bindings(
    project_id: ProjectId,
    body: TokenCommand,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    preflight_header: PreflightTokenHeader,
    ctx: Annotated[RequestContext, Depends(require("robot_model_binding.manage"))],
) -> dict[str, Any]:
    operation_scope = _check_project(ctx, project_id)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        preflight_id = body.preflight_token.partition(".")[0]
        pending = await service.repo.preflight(preflight_id)
        if pending is None:
            raise NotFoundError(code="PREFLIGHT_NOT_FOUND")
        await service.consume_preflight(
            "createRobotModelBindings",
            operation_scope,
            ctx.actor_id,
            pending.target_id,
            body.preflight_token,
            preflight_header,
            if_match,
        )
        payload = pending.payload
        now = utc_now()
        results = []
        existing = await service.repo.bindings(project_id, 1000)
        for target in payload["targets"]:
            valid_from = target["valid_from"]
            if isinstance(valid_from, str):
                from datetime import datetime

                valid_from = datetime.fromisoformat(valid_from.replace("Z", "+00:00"))
            for current in existing:
                if (
                    current.status == "ACTIVE"
                    and current.valid_to is None
                    and current.scope_type == target["scope_type"]
                    and current.scope_id == target["scope_id"]
                ):
                    current.status = "SUPERSEDED"
                    current.valid_to = valid_from
            binding = models.RobotModelBinding(
                binding_id=new_id("object"),
                organization_id=ctx.organization_id,
                project_id=project_id,
                region_code=ctx.region_code,
                scope_type=target["scope_type"],
                scope_id=target["scope_id"],
                robot_model_version_id=payload["robot_model_version_id"],
                status="ACTIVE",
                valid_from=valid_from,
                valid_to=None,
                created_by=ctx.actor_id,
                created_at=now,
                reason=payload["reason"],
                etag=compute_etag("1"),
            )
            service.repo.add(binding)
            results.append(binding)
        await session.flush()
        await _record(
            session,
            ctx,
            "robot_model.binding.changed",
            "robot_model_version",
            payload["robot_model_version_id"],
            {"binding_ids": [item.binding_id for item in results]},
        )
        return _envelope({"bindings": [binding_projection(row) for row in results]}, ctx)

    return await with_idempotency(
        session, idempotency_key, (operation_scope, "createRobotModelBindings"), command
    )


@router.get("/projects/{projectId}/regions/{regionCode}/robots", operation_id="listRobots")
async def list_robots(
    project_id: ProjectId,
    region_code: RegionCode,
    session: SessionDep,
    params: Annotated[CursorParams, Depends(cursor_params)],
    ctx: Annotated[RequestContext, Depends(require("robot.read"))],
) -> dict[str, Any]:
    _check_region(ctx, project_id, region_code)
    rows = await RoboticsService(session).repo.robots(project_id, region_code, params.limit + 1)
    page = _page(rows, params, ("created_at", "robot_id"), ctx)
    page["items"] = [robot_projection(row) for row in page["items"]]
    return page


@router.post(
    "/projects/{projectId}/regions/{regionCode}/robots",
    operation_id="createRobot",
    status_code=status.HTTP_201_CREATED,
)
async def create_robot(
    project_id: ProjectId,
    region_code: RegionCode,
    body: CreateRobotRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    ctx: Annotated[RequestContext, Depends(require("robot.create"))],
) -> dict[str, Any]:
    operation_scope = _check_region(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        await service.require_robot_model(ctx.organization_id, body.robot_model_id)
        for existing in await service.repo.robots(project_id, region_code, 1000):
            if existing.serial_no.casefold() == body.serial_no.casefold():
                raise VersionConflictError(code="ROBOT_SERIAL_ALREADY_EXISTS")
        now = utc_now()
        row = models.Robot(
            robot_id=new_id("robot"),
            organization_id=ctx.organization_id,
            project_id=project_id,
            region_code=region_code,
            display_name=body.display_name,
            serial_no=body.serial_no,
            robot_model_id=body.robot_model_id,
            lifecycle="ACTIVE",
            connectivity={
                "state": "UNKNOWN",
                "observed_at": now.isoformat(),
                "source": "SERVER",
                "reason": None,
            },
            topology_revision="1",
            resource_version="1",
            etag=compute_etag("1"),
            created_at=now,
            updated_at=now,
        )
        service.repo.add(row)
        await session.flush()
        await _record(
            session,
            ctx,
            "robot.created",
            "robot",
            row.robot_id,
            {"robot_model_id": body.robot_model_id},
        )
        return _envelope(robot_projection(row), ctx)

    return await with_idempotency(
        session, idempotency_key, (operation_scope, "createRobot"), command
    )


@router.get(
    "/projects/{projectId}/regions/{regionCode}/robots/{robotId}/bootstrap",
    operation_id="getRobotBootstrap",
)
async def get_robot_bootstrap(
    project_id: ProjectId,
    region_code: RegionCode,
    robot_id: Annotated[str, Path(alias="robotId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("robot.read"))],
) -> dict[str, Any]:
    _check_region(ctx, project_id, region_code)
    service = RoboticsService(session)
    robot = await service.require_robot(project_id, region_code, robot_id)
    components = await service.repo.components(project_id, region_code, robot_id)
    bindings = await service.repo.bindings(project_id, 1000)
    now = utc_now()
    candidates = [
        row
        for row in bindings
        if row.status == "ACTIVE"
        and (row.region_code in {None, region_code})
        and as_utc(row.valid_from) <= now
        and (row.valid_to is None or as_utc(row.valid_to) > now)
        and (
            (row.scope_type == "ROBOT_INSTANCE" and row.scope_id == robot_id)
            or (row.scope_type == "ROBOT_MODEL_DEFAULT" and row.scope_id == robot.robot_model_id)
        )
    ]
    candidates.sort(key=lambda row: row.scope_type == "ROBOT_INSTANCE", reverse=True)
    return _envelope(
        {
            "robot": robot_projection(robot),
            "topology_revision": robot.topology_revision,
            "components": [component_projection(row) for row in components],
            "effective_model_binding": binding_projection(candidates[0]) if candidates else None,
            "allowed_actions": ["UPDATE", "ADD_COMPONENT"],
            "blocked_reasons": [] if candidates else [{"code": "MODEL_BINDING_MISSING"}],
        },
        ctx,
    )


@router.patch(
    "/projects/{projectId}/regions/{regionCode}/robots/{robotId}", operation_id="updateRobot"
)
async def update_robot(
    project_id: ProjectId,
    region_code: RegionCode,
    robot_id: Annotated[str, Path(alias="robotId")],
    body: dict[str, Any],
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("robot.update"))],
) -> dict[str, Any]:
    operation_scope = _check_region(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        row = await service.require_robot(project_id, region_code, robot_id)
        service.check_etag(row.etag, if_match)
        forbidden = set(body) - {"display_name", "lifecycle"}
        if forbidden:
            raise ValidationError(
                code="SERVER_OWNED_FIELD",
                field_errors=[
                    {"path": f"/{key}", "code": "READ_ONLY", "message": "Field is server-owned."}
                    for key in sorted(forbidden)
                ],
            )
        if "display_name" in body:
            row.display_name = str(body["display_name"])
        if "lifecycle" in body:
            row.lifecycle = str(body["lifecycle"])
        service.bump(row)
        await session.flush()
        await _record(
            session, ctx, "robot.updated", "robot", robot_id, {"changed_fields": sorted(body)}
        )
        return _envelope(robot_projection(row), ctx)

    return await with_idempotency(
        session, idempotency_key, (operation_scope, "updateRobot", robot_id), command
    )


@router.get(
    "/projects/{projectId}/regions/{regionCode}/robots/{robotId}/components",
    operation_id="listRobotComponents",
)
async def list_robot_components(
    project_id: ProjectId,
    region_code: RegionCode,
    robot_id: Annotated[str, Path(alias="robotId")],
    session: SessionDep,
    params: Annotated[CursorParams, Depends(cursor_params)],
    ctx: Annotated[RequestContext, Depends(require("robot.read"))],
) -> dict[str, Any]:
    _check_region(ctx, project_id, region_code)
    service = RoboticsService(session)
    await service.require_robot(project_id, region_code, robot_id)
    rows = await service.repo.components(project_id, region_code, robot_id)
    page = _page(rows[: params.limit + 1], params, ("sort_key", "component_id"), ctx)
    page["items"] = [component_projection(row) for row in page["items"]]
    return page


@router.post(
    "/projects/{projectId}/regions/{regionCode}/robots/{robotId}/components:preflight",
    operation_id="preflightCreateComponent",
)
async def preflight_create_component(
    project_id: ProjectId,
    region_code: RegionCode,
    robot_id: Annotated[str, Path(alias="robotId")],
    body: ComponentPreflightRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    ctx: Annotated[RequestContext, Depends(require("robot_component.create"))],
) -> dict[str, Any]:
    operation_scope = _check_region(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        robot = await service.require_robot(project_id, region_code, robot_id)
        if body.topology_revision != robot.topology_revision:
            raise VersionConflictError(code="TOPOLOGY_REVISION_CHANGED")
        if body.parent_component_id:
            parent = await service.require_component(
                project_id, region_code, body.parent_component_id
            )
            if parent.robot_id != robot_id:
                raise ValidationError(code="PARENT_COMPONENT_CROSS_ROBOT")
        data = await service.create_preflight(
            "createComponent",
            operation_scope,
            ctx.actor_id,
            robot_id,
            robot.etag,
            body.model_dump(mode="json"),
        )
        return _envelope(data, ctx)

    return await with_idempotency(
        session, idempotency_key, (operation_scope, "preflightCreateComponent", robot_id), command
    )


@router.post(
    "/projects/{projectId}/regions/{regionCode}/robots/{robotId}/components",
    operation_id="createComponent",
    status_code=status.HTTP_201_CREATED,
)
async def create_component(
    project_id: ProjectId,
    region_code: RegionCode,
    robot_id: Annotated[str, Path(alias="robotId")],
    body: TokenCommand,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    preflight_header: PreflightTokenHeader,
    ctx: Annotated[RequestContext, Depends(require("robot_component.create"))],
) -> dict[str, Any]:
    operation_scope = _check_region(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        robot = await service.require_robot(project_id, region_code, robot_id)
        preflight = await service.consume_preflight(
            "createComponent",
            operation_scope,
            ctx.actor_id,
            robot_id,
            body.preflight_token,
            preflight_header,
            robot.etag,
        )
        payload = preflight.payload
        now = utc_now()
        component_id = new_id("object")
        relation = {
            "relation_id": new_id("event"),
            "type": "MOUNTED_TO",
            "robot_id": robot_id,
            "parent_component_id": payload.get("parent_component_id"),
            "valid_from": payload["valid_from"],
            "valid_to": None,
        }
        row = models.Component(
            component_id=component_id,
            organization_id=ctx.organization_id,
            project_id=project_id,
            region_code=region_code,
            robot_id=robot_id,
            parent_component_id=payload.get("parent_component_id"),
            component_type=payload["component_type"],
            display_name=payload["display_name"],
            serial_no=payload.get("serial_no"),
            lifecycle="ACTIVE",
            connectivity={"state": "UNKNOWN", "observed_at": now.isoformat(), "source": "SERVER"},
            sort_key=f"{len(await service.repo.components(project_id, region_code, robot_id)):08d}",
            bindings=[relation],
            frames=[],
            channels=[],
            resource_version="1",
            etag=compute_etag("1"),
            created_at=now,
            updated_at=now,
        )
        service.repo.add(row)
        service.bump(robot)
        robot.topology_revision = robot.resource_version
        await session.flush()
        await _record(
            session,
            ctx,
            "robot_component.created",
            "robot_component",
            component_id,
            {"robot_id": robot_id},
        )
        return _envelope(component_projection(row), ctx)

    return await with_idempotency(
        session, idempotency_key, (operation_scope, "createComponent", robot_id), command
    )


@router.get(
    "/projects/{projectId}/regions/{regionCode}/components/{componentId}",
    operation_id="getComponent",
)
async def get_component(
    project_id: ProjectId,
    region_code: RegionCode,
    component_id: Annotated[str, Path(alias="componentId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("robot.read"))],
) -> dict[str, Any]:
    _check_region(ctx, project_id, region_code)
    row = await RoboticsService(session).require_component(project_id, region_code, component_id)
    return _envelope(component_projection(row), ctx)


@router.patch(
    "/projects/{projectId}/regions/{regionCode}/components/{componentId}",
    operation_id="updateComponent",
)
async def update_component(
    project_id: ProjectId,
    region_code: RegionCode,
    component_id: Annotated[str, Path(alias="componentId")],
    body: dict[str, Any],
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("robot_component.update"))],
) -> dict[str, Any]:
    operation_scope = _check_region(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        row = await service.require_component(project_id, region_code, component_id)
        service.check_etag(row.etag, if_match)
        forbidden = set(body) - {"display_name", "serial_no", "lifecycle"}
        if forbidden:
            raise ValidationError(code="SERVER_OWNED_FIELD")
        for key, value in body.items():
            setattr(row, key, value)
        service.bump(row)
        await session.flush()
        await _record(
            session,
            ctx,
            "robot_component.updated",
            "robot_component",
            component_id,
            {"changed_fields": sorted(body)},
        )
        return _envelope(component_projection(row), ctx)

    return await with_idempotency(
        session, idempotency_key, (operation_scope, "updateComponent", component_id), command
    )


@router.post(
    "/projects/{projectId}/regions/{regionCode}/components/{componentId}/mount-changes:preflight",
    operation_id="preflightComponentMountChange",
)
async def preflight_component_mount_change(
    project_id: ProjectId,
    region_code: RegionCode,
    component_id: Annotated[str, Path(alias="componentId")],
    body: MountChangePreflightRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("robot_component.change_mount"))],
) -> dict[str, Any]:
    operation_scope = _check_region(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        component = await service.require_component(project_id, region_code, component_id)
        service.check_etag(component.etag, if_match)
        target_robot = await service.require_robot(project_id, region_code, body.new_robot_id)
        if body.topology_revision != target_robot.topology_revision:
            raise VersionConflictError(code="TOPOLOGY_REVISION_CHANGED")
        if body.new_parent_component_id:
            parent = await service.require_component(
                project_id, region_code, body.new_parent_component_id
            )
            if parent.robot_id != body.new_robot_id or parent.component_id == component_id:
                raise ValidationError(code="INVALID_COMPONENT_PARENT")
            cursor = parent
            while cursor.parent_component_id:
                if cursor.parent_component_id == component_id:
                    raise ValidationError(code="COMPONENT_TOPOLOGY_CYCLE")
                cursor = await service.require_component(
                    project_id, region_code, cursor.parent_component_id
                )
        data = await service.create_preflight(
            "createComponentMountChange",
            operation_scope,
            ctx.actor_id,
            component_id,
            component.etag,
            body.model_dump(mode="json"),
        )
        await _record(
            session, ctx, "robot_component.mount.preflighted", "robot_component", component_id
        )
        return _envelope(data, ctx)

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "preflightComponentMountChange", component_id),
        command,
    )


@router.post(
    "/projects/{projectId}/regions/{regionCode}/components/{componentId}/mount-changes",
    operation_id="createComponentMountChange",
)
async def create_component_mount_change(
    project_id: ProjectId,
    region_code: RegionCode,
    component_id: Annotated[str, Path(alias="componentId")],
    body: TokenCommand,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    preflight_header: PreflightTokenHeader,
    ctx: Annotated[RequestContext, Depends(require("robot_component.change_mount"))],
) -> dict[str, Any]:
    operation_scope = _check_region(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        component = await service.require_component(project_id, region_code, component_id)
        service.check_etag(component.etag, if_match)
        preflight = await service.consume_preflight(
            "createComponentMountChange",
            operation_scope,
            ctx.actor_id,
            component_id,
            body.preflight_token,
            preflight_header,
            component.etag,
        )
        payload = preflight.payload
        old_robot = await service.require_robot(project_id, region_code, component.robot_id)
        new_robot = await service.require_robot(project_id, region_code, payload["new_robot_id"])
        relations = list(component.bindings)
        for relation in relations:
            if relation.get("valid_to") is None:
                relation["valid_to"] = payload["valid_from"]
        relations.append(
            {
                "relation_id": new_id("event"),
                "type": "MOUNTED_TO",
                "robot_id": new_robot.robot_id,
                "parent_component_id": payload.get("new_parent_component_id"),
                "valid_from": payload["valid_from"],
                "valid_to": None,
                "reason": payload["reason"],
            }
        )
        component.bindings = relations
        component.robot_id = new_robot.robot_id
        component.parent_component_id = payload.get("new_parent_component_id")
        service.bump(component)
        affected_robots = [old_robot]
        if new_robot.robot_id != old_robot.robot_id:
            affected_robots.append(new_robot)
        for robot in affected_robots:
            service.bump(robot)
            robot.topology_revision = robot.resource_version
        await session.flush()
        await _record(
            session,
            ctx,
            "robot_component.mount.changed",
            "robot_component",
            component_id,
            {"old_robot_id": old_robot.robot_id, "new_robot_id": new_robot.robot_id},
        )
        return _envelope(component_projection(component), ctx)

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "createComponentMountChange", component_id),
        command,
    )


def _embedded_page(items: list[dict[str, Any]], ctx: RequestContext) -> dict[str, Any]:
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


@router.get(
    "/projects/{projectId}/regions/{regionCode}/components/{componentId}/bindings",
    operation_id="listComponentBindings",
)
async def list_component_bindings(
    project_id: ProjectId,
    region_code: RegionCode,
    component_id: Annotated[str, Path(alias="componentId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("robot.read"))],
) -> dict[str, Any]:
    _check_region(ctx, project_id, region_code)
    row = await RoboticsService(session).require_component(project_id, region_code, component_id)
    return _embedded_page(row.bindings, ctx)


@router.get(
    "/projects/{projectId}/regions/{regionCode}/components/{componentId}/frames",
    operation_id="listComponentFrames",
)
async def list_component_frames(
    project_id: ProjectId,
    region_code: RegionCode,
    component_id: Annotated[str, Path(alias="componentId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("calibration.read"))],
) -> dict[str, Any]:
    _check_region(ctx, project_id, region_code)
    row = await RoboticsService(session).require_component(project_id, region_code, component_id)
    return _embedded_page(row.frames, ctx)


@router.get(
    "/projects/{projectId}/regions/{regionCode}/components/{componentId}/channels",
    operation_id="listComponentChannels",
)
async def list_component_channels(
    project_id: ProjectId,
    region_code: RegionCode,
    component_id: Annotated[str, Path(alias="componentId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("data_schema.read"))],
) -> dict[str, Any]:
    _check_region(ctx, project_id, region_code)
    row = await RoboticsService(session).require_component(project_id, region_code, component_id)
    return _embedded_page(row.channels, ctx)


@router.get(
    "/projects/{projectId}/regions/{regionCode}/robots/{robotId}/route-resolutions/p15-to-p16",
    operation_id="resolveP15CalibrationRoute",
)
async def resolve_p15_calibration_route(
    project_id: ProjectId,
    region_code: RegionCode,
    robot_id: Annotated[str, Path(alias="robotId")],
    component_id: Annotated[str, Query()],
    set_id: Annotated[str, Query()],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("calibration.read"))],
) -> dict[str, Any]:
    _check_region(ctx, project_id, region_code)
    service = RoboticsService(session)
    await service.require_robot(project_id, region_code, robot_id)
    component = await service.require_component(project_id, region_code, component_id)
    calibration = await service.require_calibration_set(project_id, region_code, set_id)
    if component.robot_id != robot_id or calibration.component_id != component_id:
        raise VersionConflictError(code="CALIBRATION_ROUTE_RELATION_MISMATCH")
    return _envelope(
        {
            "route": f"/calibrations/{set_id}?robotId={robot_id}&componentId={component_id}",
            "robot_id": robot_id,
            "component_id": component_id,
            "set_id": set_id,
        },
        ctx,
    )


@router.get(
    "/projects/{projectId}/regions/{regionCode}/route-resolutions/p15-to-p17",
    operation_id="resolveP15DataSchemaRoute",
)
async def resolve_p15_data_schema_route(
    project_id: ProjectId,
    region_code: RegionCode,
    schema_id: Annotated[str, Query()],
    schema_version: Annotated[str, Query()],
    component_id: Annotated[str, Query()],
    detail_tab: Annotated[str, Query()],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("data_schema.read"))],
) -> dict[str, Any]:
    _check_region(ctx, project_id, region_code)
    service = RoboticsService(session)
    component = await service.require_component(project_id, region_code, component_id)
    await service.require_schema_version(ctx.organization_id, schema_id, schema_version)
    matched = any(
        item.get("schema_id") == schema_id and item.get("schema_version") == schema_version
        for item in component.channels
    )
    if not matched:
        raise VersionConflictError(code="SCHEMA_ROUTE_RELATION_MISMATCH")
    return _envelope(
        {
            "route": f"/schemas/{schema_id}/versions/{schema_version}?tab={detail_tab}",
            "schema_id": schema_id,
            "schema_version": schema_version,
            "component_id": component_id,
            "detail_tab": detail_tab,
        },
        ctx,
    )


def _sample_candidate_projection(candidate: dict[str, Any]) -> dict[str, Any]:
    required = {"candidate_id", "source_type", "revision_id", "start_ns", "end_ns"}
    if required.difference(candidate):
        raise VersionConflictError(code="SAMPLE_PROJECTION_INVALID")
    return {
        "candidate_id": candidate["candidate_id"],
        "source_type": candidate["source_type"],
        "revision_id": candidate["revision_id"],
        "start_ns": candidate["start_ns"],
        "end_ns": candidate["end_ns"],
        "joint_names": list(candidate.get("joint_names", [])),
        "allowed_actions": ["VIEW", "VALIDATE"],
        "blocked_reasons": [],
    }


def _require_sample_candidate(
    version: models.RobotModelVersion, candidate_id: str
) -> dict[str, Any]:
    candidate = next(
        (item for item in version.sample_candidates if item.get("candidate_id") == candidate_id),
        None,
    )
    if candidate is None:
        raise NotFoundError(code="ROBOT_MODEL_SAMPLE_CANDIDATE_NOT_FOUND")
    return candidate


@router.get(
    "/organizations/{organizationId}/robot-model-versions/{versionId}/sample-candidates",
    operation_id="listRobotModelSampleCandidates",
)
async def list_robot_model_sample_candidates(
    organization_id: OrganizationId,
    version_id: Annotated[str, Path(alias="versionId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("robot_model.validate"))],
) -> dict[str, Any]:
    _check_org(ctx, organization_id)
    version = await RoboticsService(session).require_model_version(organization_id, version_id)
    return _embedded_page(
        [_sample_candidate_projection(item) for item in version.sample_candidates], ctx
    )


@router.get(
    "/organizations/{organizationId}/robot-model-versions/{versionId}/sample-candidates/{candidateId}/joint-window",
    operation_id="getRobotModelSampleWindow",
)
async def get_robot_model_sample_window(
    organization_id: OrganizationId,
    version_id: Annotated[str, Path(alias="versionId")],
    candidate_id: Annotated[str, Path(alias="candidateId")],
    start_ns: Annotated[str, Query(alias="start_ns", pattern=r"^(0|[1-9][0-9]*)$")],
    end_ns: Annotated[str, Query(alias="end_ns", pattern=r"^[1-9][0-9]*$")],
    max_points: Annotated[int, Query(alias="max_points", ge=100, le=5000)],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("robot_model.validate"))],
) -> dict[str, Any]:
    _check_org(ctx, organization_id)
    version = await RoboticsService(session).require_model_version(organization_id, version_id)
    candidate = _require_sample_candidate(version, candidate_id)
    if int(end_ns) <= int(start_ns):
        raise ValidationError(code="INVALID_SAMPLE_WINDOW")
    if int(start_ns) < int(candidate["start_ns"]) or int(end_ns) > int(candidate["end_ns"]):
        raise ValidationError(code="SAMPLE_WINDOW_OUT_OF_RANGE")
    candidate_timestamps = candidate.get("timestamps_ns", [])
    candidate_positions = candidate.get("positions", [])
    if len(candidate_timestamps) != len(candidate_positions):
        raise VersionConflictError(code="SAMPLE_PROJECTION_INVALID")
    samples = [
        (str(timestamp), positions)
        for timestamp, positions in zip(candidate_timestamps, candidate_positions, strict=True)
        if int(start_ns) <= int(timestamp) < int(end_ns)
    ]
    if len(samples) > max_points:
        step = max(1, len(samples) // max_points)
        samples = samples[::step][:max_points]
    timestamps = [item[0] for item in samples]
    positions = [item[1] for item in samples]
    joint_names = list(candidate.get("joint_names", []))
    return _envelope(
        {
            "candidate_id": candidate_id,
            "start_ns": start_ns,
            "end_ns": end_ns,
            "timestamps_ns": timestamps,
            "joint_names": joint_names,
            "positions": positions,
            "allowed_actions": ["VALIDATE"],
            "blocked_reasons": [],
        },
        ctx,
    )


@router.post(
    "/organizations/{organizationId}/robot-model-versions/{versionId}/sample-validations",
    operation_id="createRobotModelSampleValidation",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_robot_model_sample_validation(
    organization_id: OrganizationId,
    version_id: Annotated[str, Path(alias="versionId")],
    body: CreateRobotModelSampleValidationRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    ctx: Annotated[RequestContext, Depends(require("robot_model.validate"))],
) -> dict[str, Any]:
    operation_scope = _check_org(ctx, organization_id)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        version = await service.require_model_version(organization_id, version_id)
        _require_sample_candidate(version, body.candidate_id)
        if body.validation_input_hash != version.validation_input_hash:
            raise VersionConflictError(code="ROBOT_MODEL_VALIDATION_INPUT_CHANGED")
        report_id = new_id("finding")
        job = await service.create_job(
            operation_scope, "ROBOT_MODEL_SAMPLE_VALIDATION", "ROBOT_MODEL_VERSION", version_id
        )
        report = models.RobotValidationReport(
            report_id=report_id,
            organization_id=organization_id,
            version_id=version_id,
            status="PASSED",
            input_hash=version.validation_input_hash,
            report={
                "id": report_id,
                "run_id": report_id,
                "candidate_id": body.candidate_id,
                "status": "SUCCEEDED",
                "validation_input_hash": body.validation_input_hash,
                "issues": [],
                "job_id": job.job_id,
            },
            job_id=job.job_id,
            created_at=utc_now(),
        )
        service.repo.add(report)
        version.validation_status = "PASSED"
        version.publish_readiness = "READY"
        await session.flush()
        await _record(
            session,
            ctx,
            "robot_model.validation.requested",
            "robot_model_version",
            version_id,
            {"job_id": job.job_id, "report_id": report_id},
        )
        await _record(
            session,
            ctx,
            "robot_model.validation.completed",
            "robot_model_version",
            version_id,
            {"job_id": job.job_id, "report_id": report_id, "outcome": "PASSED"},
        )
        response = _envelope({}, ctx)
        response.pop("data")
        response["job"] = job_projection(job)
        response["result"] = report.report
        return response

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "createRobotModelSampleValidation", version_id),
        command,
    )


@router.get(
    "/projects/{projectId}/regions/{regionCode}/robots/{robotId}/binding-history",
    operation_id="listRobotBindingHistory",
)
async def list_robot_binding_history(
    project_id: ProjectId,
    region_code: RegionCode,
    robot_id: Annotated[str, Path(alias="robotId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("robot.read"))],
) -> dict[str, Any]:
    _check_region(ctx, project_id, region_code)
    service = RoboticsService(session)
    await service.require_robot(project_id, region_code, robot_id)
    rows = await service.repo.bindings(project_id, 1000)
    items = [
        {
            "binding_id": row.binding_id,
            "binding_type": "ROBOT_MODEL",
            "target_id": row.robot_model_version_id,
            "valid_from": row.valid_from,
            "valid_to": row.valid_to,
            "allowed_actions": ["VIEW"],
            "blocked_reasons": [],
        }
        for row in rows
        if row.region_code in {None, region_code}
        and (
            (row.scope_type == "ROBOT_INSTANCE" and row.scope_id == robot_id)
            or row.scope_type == "ROBOT_MODEL_DEFAULT"
        )
    ]
    return _embedded_page(items, ctx)


@router.get(
    "/projects/{projectId}/regions/{regionCode}/robots/{robotId}/model-version-candidates",
    operation_id="listRobotModelVersionCandidates",
)
async def list_robot_model_version_candidates(
    project_id: ProjectId,
    region_code: RegionCode,
    robot_id: Annotated[str, Path(alias="robotId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("robot_model_binding.read"))],
) -> dict[str, Any]:
    _check_region(ctx, project_id, region_code)
    service = RoboticsService(session)
    robot = await service.require_robot(project_id, region_code, robot_id)
    versions = await service.repo.model_versions(ctx.organization_id, robot.robot_model_id, 1000)
    items = [
        {
            "robot_model_version_id": row.version_id,
            "compatibility": "COMPATIBLE",
            "status": row.lifecycle,
            "reason_codes": [],
            "allowed_actions": ["BIND"] if row.lifecycle == "PUBLISHED" else [],
            "blocked_reasons": []
            if row.lifecycle == "PUBLISHED"
            else [{"code": "VERSION_DISABLED"}],
        }
        for row in versions
        if row.lifecycle in {"PUBLISHED", "DISABLED"}
    ]
    return _embedded_page(items, ctx)


@router.post(
    "/projects/{projectId}/regions/{regionCode}/robots/{robotId}:preflight-disable",
    operation_id="preflightDisableRobot",
)
async def preflight_disable_robot(
    project_id: ProjectId,
    region_code: RegionCode,
    robot_id: Annotated[str, Path(alias="robotId")],
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("robot.disable"))],
) -> dict[str, Any]:
    operation_scope = _check_region(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        robot = await service.require_robot(project_id, region_code, robot_id)
        service.check_etag(robot.etag, if_match)
        if robot.lifecycle != "ACTIVE":
            raise VersionConflictError(code="ROBOT_NOT_ACTIVE")
        components = await service.repo.components(project_id, region_code, robot_id)
        data = await service.create_preflight(
            "disableRobot",
            operation_scope,
            ctx.actor_id,
            robot_id,
            robot.etag,
            {"component_count": len(components)},
        )
        data["blocked_reasons"] = []
        data["impact_summary"] = {"component_count": str(len(components))}
        return _envelope(data, ctx)

    return await with_idempotency(
        session, idempotency_key, (operation_scope, "preflightDisableRobot", robot_id), command
    )


@router.post(
    "/projects/{projectId}/regions/{regionCode}/robots/{robotId}:disable",
    operation_id="disableRobot",
)
async def disable_robot(
    project_id: ProjectId,
    region_code: RegionCode,
    robot_id: Annotated[str, Path(alias="robotId")],
    body: EmptyCommand,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    preflight_header: PreflightTokenHeader,
    ctx: Annotated[RequestContext, Depends(require("robot.disable"))],
) -> dict[str, Any]:
    operation_scope = _check_region(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        robot = await service.require_robot(project_id, region_code, robot_id)
        service.check_etag(robot.etag, if_match)
        await service.consume_preflight(
            "disableRobot",
            operation_scope,
            ctx.actor_id,
            robot_id,
            preflight_header,
            preflight_header,
            robot.etag,
        )
        robot.lifecycle = "DISABLED"
        service.bump(robot)
        await session.flush()
        await _record(session, ctx, "robot.disabled", "robot", robot_id)
        return _envelope(robot_projection(robot), ctx)

    return await with_idempotency(
        session, idempotency_key, (operation_scope, "disableRobot", robot_id), command
    )


@router.get(
    "/projects/{projectId}/regions/{regionCode}/components/{componentId}/maintenance-records",
    operation_id="listComponentMaintenance",
)
async def list_component_maintenance(
    project_id: ProjectId,
    region_code: RegionCode,
    component_id: Annotated[str, Path(alias="componentId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("robot.read"))],
) -> dict[str, Any]:
    _check_region(ctx, project_id, region_code)
    component = await RoboticsService(session).require_component(
        project_id, region_code, component_id
    )
    return _embedded_page(list(component.connectivity.get("maintenance_records", [])), ctx)


@router.post(
    "/projects/{projectId}/regions/{regionCode}/components/{componentId}:preflight-disable",
    operation_id="preflightDisableComponent",
)
async def preflight_disable_component(
    project_id: ProjectId,
    region_code: RegionCode,
    component_id: Annotated[str, Path(alias="componentId")],
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("robot_component.disable"))],
) -> dict[str, Any]:
    operation_scope = _check_region(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        component = await service.require_component(project_id, region_code, component_id)
        service.check_etag(component.etag, if_match)
        if component.lifecycle != "ACTIVE":
            raise VersionConflictError(code="COMPONENT_NOT_ACTIVE")
        data = await service.create_preflight(
            "disableComponent",
            operation_scope,
            ctx.actor_id,
            component_id,
            component.etag,
            {"robot_id": component.robot_id},
        )
        data["blocked_reasons"] = []
        data["impact_summary"] = {"robot_id": component.robot_id}
        return _envelope(data, ctx)

    return await with_idempotency(
        session,
        idempotency_key,
        (operation_scope, "preflightDisableComponent", component_id),
        command,
    )


@router.post(
    "/projects/{projectId}/regions/{regionCode}/components/{componentId}:disable",
    operation_id="disableComponent",
)
async def disable_component(
    project_id: ProjectId,
    region_code: RegionCode,
    component_id: Annotated[str, Path(alias="componentId")],
    body: EmptyCommand,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    preflight_header: PreflightTokenHeader,
    ctx: Annotated[RequestContext, Depends(require("robot_component.disable"))],
) -> dict[str, Any]:
    operation_scope = _check_region(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        service = RoboticsService(session)
        component = await service.require_component(project_id, region_code, component_id)
        service.check_etag(component.etag, if_match)
        await service.consume_preflight(
            "disableComponent",
            operation_scope,
            ctx.actor_id,
            component_id,
            preflight_header,
            preflight_header,
            component.etag,
        )
        component.lifecycle = "DISABLED"
        service.bump(component)
        robot = await service.require_robot(project_id, region_code, component.robot_id)
        service.bump(robot)
        robot.topology_revision = robot.resource_version
        await session.flush()
        await _record(session, ctx, "robot_component.disabled", "robot_component", component_id)
        return _envelope(component_projection(component), ctx)

    return await with_idempotency(
        session, idempotency_key, (operation_scope, "disableComponent", component_id), command
    )
