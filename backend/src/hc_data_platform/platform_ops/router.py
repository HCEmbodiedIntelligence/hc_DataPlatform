"""Authenticated platform instance and maintenance-operation boundaries."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, TypeVar, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from hc_data_platform.backup.catalog import (
    BackupCatalogError,
    BackupCatalogPage,
    BackupCatalogRepository,
    CatalogStatus,
)
from hc_data_platform.core.config import Settings
from hc_data_platform.core.errors import ProblemException, problem
from hc_data_platform.platform_control.maintenance_contract import (
    MaintenanceCommandV1,
    MaintenanceContractError,
    MaintenanceState,
)
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.capabilities import (
    CAPABILITY_PLATFORM_ADMIN,
    CAPABILITY_PLATFORM_BREAK_GLASS,
    CAPABILITY_PLATFORM_MAINTENANCE_OPERATE,
    CAPABILITY_PLATFORM_MAINTENANCE_VERIFY,
    CAPABILITY_PLATFORM_OPERATIONS_READ,
    CAPABILITY_PLATFORM_RELEASE_OPERATE,
)
from hc_data_platform.security.http import VerifiedAuth

from .audit import PlatformAuditIntegrity, PlatformAuditPage, PlatformAuditService
from .instances import InstanceRole, PlatformInstancePage, PlatformInstanceService
from .logs import (
    PlatformLogPage,
    PlatformLogQueryError,
    PlatformLogService,
    RuntimeLogService,
    RuntimeLogSeverity,
    SafeCorrelation,
    SafeEventCode,
)
from .maintenance import (
    MaintenanceOperation,
    MaintenanceWriteGate,
    OperationKind,
    PlanDigest,
    PlatformAuditContext,
    PlatformAuditEvent,
    PlatformAuditOutcome,
    PostgresMaintenanceRepository,
)
from .object_store_config import (
    ObjectStoreConfigurationError,
    ObjectStoreConfigurationService,
    ObjectStoreConfigurationStatus,
    ObjectStoreConfigurationUpdate,
    ObjectStoreLocationStatus,
)
from .overview import PlatformOperationsOverview, PlatformOperationsOverviewService
from .projects import (
    PlatformProject,
    PlatformProjectCreate,
    PlatformProjectError,
    PlatformProjectPage,
    PlatformProjectService,
)
from .releases import (
    ReleaseApprovalRequest,
    ReleaseHistoryPage,
    ReleaseOperationError,
    ReleasePreflightRequest,
    ReleaseRun,
    ReleaseService,
    ReleaseTransitionRequest,
)
from .runtime_config import (
    RuntimeConfigError,
    RuntimeConfigHistoryPage,
    RuntimeConfigPublishRequest,
    RuntimeConfigRollbackRequest,
    RuntimeConfigService,
    RuntimeConfigSnapshot,
)

router = APIRouter(prefix="/api/v1/platform", tags=["platform-operations"])


def _instance_service(request: Request) -> PlatformInstanceService:
    service = getattr(request.app.state, "platform_instance_service", None)
    if not isinstance(service, PlatformInstanceService):
        raise RuntimeError("platform instance service is not initialized")
    return service


InstanceServiceDependency = Annotated[PlatformInstanceService, Depends(_instance_service)]
_T = TypeVar("_T")


def _runtime_config_service(request: Request) -> RuntimeConfigService:
    service = getattr(request.app.state, "runtime_config_service", None)
    if not isinstance(service, RuntimeConfigService):
        raise RuntimeError("runtime configuration service is not initialized")
    return service


RuntimeConfigServiceDependency = Annotated[RuntimeConfigService, Depends(_runtime_config_service)]


def _object_store_config_service(request: Request) -> ObjectStoreConfigurationService:
    service = getattr(request.app.state, "object_store_config_service", None)
    if not isinstance(service, ObjectStoreConfigurationService):
        raise RuntimeError("object-store configuration service is not initialized")
    return service


ObjectStoreConfigServiceDependency = Annotated[
    ObjectStoreConfigurationService, Depends(_object_store_config_service)
]


def _platform_project_service(request: Request) -> PlatformProjectService:
    service = getattr(request.app.state, "platform_project_service", None)
    if not isinstance(service, PlatformProjectService):
        raise RuntimeError("platform project service is not initialized")
    return service


PlatformProjectServiceDependency = Annotated[
    PlatformProjectService, Depends(_platform_project_service)
]


def _platform_audit_service(request: Request) -> PlatformAuditService:
    service = getattr(request.app.state, "platform_audit_service", None)
    if not isinstance(service, PlatformAuditService):
        raise RuntimeError("platform audit service is not initialized")
    return service


PlatformAuditServiceDependency = Annotated[PlatformAuditService, Depends(_platform_audit_service)]


def _platform_log_service(request: Request) -> PlatformLogService:
    service = getattr(request.app.state, "platform_log_service", None)
    if not isinstance(service, PlatformLogService):
        raise RuntimeError("platform log service is not initialized")
    return service


PlatformLogServiceDependency = Annotated[PlatformLogService, Depends(_platform_log_service)]


def _platform_operations_overview_service(
    request: Request,
) -> PlatformOperationsOverviewService:
    service = getattr(request.app.state, "platform_operations_overview_service", None)
    if not isinstance(service, PlatformOperationsOverviewService):
        raise RuntimeError("platform operations overview service is not initialized")
    return service


PlatformOperationsOverviewDependency = Annotated[
    PlatformOperationsOverviewService, Depends(_platform_operations_overview_service)
]


def _release_service(request: Request) -> ReleaseService:
    service = getattr(request.app.state, "release_service", None)
    if not isinstance(service, ReleaseService):
        raise RuntimeError("platform release service is not initialized")
    return service


ReleaseServiceDependency = Annotated[ReleaseService, Depends(_release_service)]


def _backup_catalog_repository(request: Request) -> BackupCatalogRepository:
    repository = getattr(request.app.state, "backup_catalog_repository", None)
    if repository is None or not callable(getattr(repository, "list_page", None)):
        raise RuntimeError("backup catalog repository is not initialized")
    return cast(BackupCatalogRepository, repository)


BackupCatalogRepositoryDependency = Annotated[
    BackupCatalogRepository, Depends(_backup_catalog_repository)
]


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MaintenanceOperationRequest(_StrictRequest):
    operation_id: str = Field(min_length=1, max_length=128)
    operation_kind: OperationKind
    plan_digest: PlanDigest


class MaintenanceOwnerRequest(_StrictRequest):
    owner_instance_id: UUID


class MaintenanceLeaseRequest(MaintenanceOwnerRequest):
    fencing_token: int = Field(gt=0)


class MaintenanceTransitionRequest(MaintenanceLeaseRequest):
    expected_state: MaintenanceState
    expected_state_version: int = Field(ge=0)
    next_state: MaintenanceState
    manual_approval_id: str | None = Field(default=None, min_length=1, max_length=255)


class MaintenanceReconciliationRequest(MaintenanceLeaseRequest):
    expected_state_version: int = Field(ge=0)


def _maintenance_repository(request: Request) -> PostgresMaintenanceRepository:
    repository = getattr(request.app.state, "maintenance_write_gate", None)
    if not isinstance(repository, PostgresMaintenanceRepository):
        raise RuntimeError("PostgreSQL maintenance repository is not initialized")
    return repository


def _settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    if not isinstance(settings, Settings):
        raise RuntimeError("application settings are not initialized")
    return settings


MaintenanceRepositoryDependency = Annotated[
    PostgresMaintenanceRepository, Depends(_maintenance_repository)
]
SettingsDependency = Annotated[Settings, Depends(_settings)]


def _maintenance_audit_sink(request: Request) -> MaintenanceWriteGate:
    sink = getattr(request.app.state, "maintenance_write_gate", None)
    if sink is None or not callable(getattr(sink, "append_platform_audit", None)):
        raise RuntimeError("platform operations audit sink is not initialized")
    return cast(MaintenanceWriteGate, sink)


MaintenanceAuditDependency = Annotated[MaintenanceWriteGate, Depends(_maintenance_audit_sink)]

_COMMAND_CAPABILITIES = (
    CAPABILITY_PLATFORM_MAINTENANCE_OPERATE,
    CAPABILITY_PLATFORM_RELEASE_OPERATE,
    CAPABILITY_PLATFORM_MAINTENANCE_VERIFY,
    CAPABILITY_PLATFORM_BREAK_GLASS,
)
_READ_CAPABILITY_POLICY = {
    "mode": "any-exact",
    "capabilities": [
        CAPABILITY_PLATFORM_ADMIN,
        CAPABILITY_PLATFORM_OPERATIONS_READ,
    ],
}
_OPERATION_CAPABILITY_POLICY = {
    "mode": "operation-kind-exact",
    "mapping": {
        "BACKUP": CAPABILITY_PLATFORM_MAINTENANCE_OPERATE,
        "OTHER": CAPABILITY_PLATFORM_MAINTENANCE_OPERATE,
        "MIGRATION": CAPABILITY_PLATFORM_RELEASE_OPERATE,
        "RELEASE": CAPABILITY_PLATFORM_RELEASE_OPERATE,
        "RESTORE": CAPABILITY_PLATFORM_BREAK_GLASS,
    },
}
_TRANSITION_CAPABILITY_POLICY = {
    **_OPERATION_CAPABILITY_POLICY,
    "break_glass_when": [
        "current_state=FAILED_READ_ONLY",
        "next_state=SUCCEEDED",
    ],
}


def _problem_response(description: str) -> dict[str, object]:
    return {
        "description": description,
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        },
    }


_AUTHENTICATION_RESPONSE = _problem_response("A verified Bearer session is required.")
_CAPABILITY_RESPONSE = _problem_response("An exact platform capability is required.")
_NOT_FOUND_RESPONSE = _problem_response(
    "The operation was not found in the configured environment."
)
_CONFLICT_RESPONSE = _problem_response(
    "The command conflicts with the current owner, lease, token, or state."
)
_RUNTIME_CONFIG_CONFLICT_RESPONSE = _problem_response(
    "The expected runtime configuration revision is stale."
)


def _release_action(action: Callable[[], _T]) -> _T:
    try:
        return action()
    except ReleaseOperationError as exc:
        status = (
            404
            if exc.code == "RELEASE_NOT_FOUND"
            else 503
            if exc.code == ("RELEASE_FEED_TRUST_NOT_CONFIGURED")
            else 409
        )
        raise problem(
            status=status,
            code=exc.code,
            title="Release operation rejected",
            detail="The release operation did not pass its fail-closed contract.",
            retryable=False,
        ) from exc


@router.get(
    "/releases",
    operation_id="listPlatformReleaseHistory",
    response_model=ReleaseHistoryPage,
    responses={401: _AUTHENTICATION_RESPONSE, 403: _CAPABILITY_RESPONSE},
    openapi_extra={"x-hc-platform-capability-policy": _READ_CAPABILITY_POLICY},
)
def list_platform_release_history(
    request: Request,
    response: Response,
    auth: VerifiedAuth,
    service: ReleaseServiceDependency,
    audit_sink: MaintenanceAuditDependency,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ReleaseHistoryPage:
    _require_exact_with_audit(
        auth,
        CAPABILITY_PLATFORM_ADMIN,
        CAPABILITY_PLATFORM_OPERATIONS_READ,
        request=request,
        audit_sink=audit_sink,
        action="platform.release.history.list",
        resource_id="release-history",
    )
    response.headers["Cache-Control"] = "private, no-store"
    return service.history(limit=limit)


@router.post(
    "/releases:preflight",
    operation_id="preflightPlatformRelease",
    response_model=ReleaseRun,
    responses={
        401: _AUTHENTICATION_RESPONSE,
        403: _CAPABILITY_RESPONSE,
        409: _problem_response(
            "The release failed signature, compatibility, or platform preflight."
        ),
        503: _problem_response("Release-feed trust is not configured."),
    },
    openapi_extra={
        "x-hc-platform-capability-policy": {
            "mode": "one-exact",
            "capabilities": [CAPABILITY_PLATFORM_RELEASE_OPERATE],
        }
    },
)
def preflight_platform_release(
    body: ReleasePreflightRequest,
    request: Request,
    response: Response,
    auth: VerifiedAuth,
    service: ReleaseServiceDependency,
    audit_sink: MaintenanceAuditDependency,
) -> ReleaseRun:
    _require_exact_with_audit(
        auth,
        CAPABILITY_PLATFORM_RELEASE_OPERATE,
        request=request,
        audit_sink=audit_sink,
        action="platform.release.preflight",
        resource_id=body.target_release_id,
    )
    result = _release_action(
        lambda: service.preflight(
            body,
            actor_id=auth.subject_id,
            request_id=_request_id(request),
        )
    )
    response.headers["Cache-Control"] = "private, no-store"
    _append_platform_audit(
        audit_sink,
        request=request,
        auth=auth,
        action="platform.release.preflight.passed",
        resource_type="platform_release",
        resource_id=result.release_id,
        outcome="SUCCEEDED",
        safe_details={"status": result.state, "state_version": result.state_version},
    )
    return result


@router.post(
    "/releases/{release_id}:approve",
    operation_id="approvePlatformRelease",
    response_model=ReleaseRun,
    responses={
        401: _AUTHENTICATION_RESPONSE,
        403: _CAPABILITY_RESPONSE,
        404: _problem_response("The release was not found."),
        409: _problem_response("The approval is stale, self-approved, or in the wrong state."),
    },
    openapi_extra={
        "x-hc-platform-capability-policy": {
            "mode": "one-exact",
            "capabilities": [CAPABILITY_PLATFORM_RELEASE_OPERATE],
        }
    },
)
def approve_platform_release(
    body: ReleaseApprovalRequest,
    request: Request,
    response: Response,
    auth: VerifiedAuth,
    service: ReleaseServiceDependency,
    audit_sink: MaintenanceAuditDependency,
    release_id: Annotated[
        str,
        Path(pattern=r"^platform-v[A-Za-z0-9][A-Za-z0-9._-]{0,119}$"),
    ],
) -> ReleaseRun:
    _require_exact_with_audit(
        auth,
        CAPABILITY_PLATFORM_RELEASE_OPERATE,
        request=request,
        audit_sink=audit_sink,
        action="platform.release.approve",
        resource_id=release_id,
    )
    result = _release_action(
        lambda: service.approve(
            release_id,
            body,
            actor_id=auth.subject_id,
            request_id=_request_id(request),
        )
    )
    response.headers["Cache-Control"] = "private, no-store"
    _append_platform_audit(
        audit_sink,
        request=request,
        auth=auth,
        action="platform.release.approved",
        resource_type="platform_release",
        resource_id=result.release_id,
        outcome="SUCCEEDED",
        safe_details={"status": result.state, "state_version": result.state_version},
    )
    return result


@router.post(
    "/releases/{release_id}:transition",
    operation_id="transitionPlatformRelease",
    response_model=ReleaseRun,
    responses={
        401: _AUTHENTICATION_RESPONSE,
        403: _CAPABILITY_RESPONSE,
        404: _problem_response("The release was not found."),
        409: _problem_response("The controller transition is stale or invalid."),
    },
    openapi_extra={
        "x-hc-platform-capability-policy": {
            "mode": "one-exact",
            "capabilities": [CAPABILITY_PLATFORM_RELEASE_OPERATE],
        }
    },
)
def transition_platform_release(
    body: ReleaseTransitionRequest,
    request: Request,
    response: Response,
    auth: VerifiedAuth,
    service: ReleaseServiceDependency,
    audit_sink: MaintenanceAuditDependency,
    release_id: Annotated[
        str,
        Path(pattern=r"^platform-v[A-Za-z0-9][A-Za-z0-9._-]{0,119}$"),
    ],
) -> ReleaseRun:
    _require_exact_with_audit(
        auth,
        CAPABILITY_PLATFORM_RELEASE_OPERATE,
        request=request,
        audit_sink=audit_sink,
        action="platform.release.transition",
        resource_id=release_id,
    )
    result = _release_action(
        lambda: service.transition(
            release_id,
            body,
            actor_id=auth.subject_id,
            request_id=_request_id(request),
        )
    )
    response.headers["Cache-Control"] = "private, no-store"
    _append_platform_audit(
        audit_sink,
        request=request,
        auth=auth,
        action="platform.release.transitioned",
        resource_type="platform_release",
        resource_id=result.release_id,
        outcome="SUCCEEDED",
        safe_details={"status": result.state, "state_version": result.state_version},
    )
    return result


@router.get(
    "/audit/events",
    operation_id="listPlatformAuditEvents",
    response_model=PlatformAuditPage,
    responses={401: _AUTHENTICATION_RESPONSE, 403: _CAPABILITY_RESPONSE},
    openapi_extra={"x-hc-platform-capability-policy": _READ_CAPABILITY_POLICY},
)
def list_platform_audit_events(
    request: Request,
    response: Response,
    auth: VerifiedAuth,
    service: PlatformAuditServiceDependency,
    audit_sink: MaintenanceAuditDependency,
    occurred_from: Annotated[datetime | None, Query()] = None,
    occurred_to: Annotated[datetime | None, Query()] = None,
    cursor: Annotated[str | None, Query(min_length=1, max_length=16_384)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> PlatformAuditPage:
    _require_exact_with_audit(
        auth,
        CAPABILITY_PLATFORM_ADMIN,
        CAPABILITY_PLATFORM_OPERATIONS_READ,
        request=request,
        audit_sink=audit_sink,
        action="platform.audit.events.list",
        resource_id="platform-audit",
    )
    response.headers["Cache-Control"] = "private, no-store"
    return service.list_events(
        auth=auth,
        request_id=_request_id(request),
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/audit/integrity",
    operation_id="verifyPlatformAuditIntegrity",
    response_model=PlatformAuditIntegrity,
    responses={401: _AUTHENTICATION_RESPONSE, 403: _CAPABILITY_RESPONSE},
    openapi_extra={
        "x-hc-platform-capability-policy": {
            "mode": "one-exact",
            "capabilities": [CAPABILITY_PLATFORM_MAINTENANCE_VERIFY],
        }
    },
)
def verify_platform_audit_integrity(
    request: Request,
    response: Response,
    auth: VerifiedAuth,
    service: PlatformAuditServiceDependency,
    audit_sink: MaintenanceAuditDependency,
) -> PlatformAuditIntegrity:
    _require_exact_with_audit(
        auth,
        CAPABILITY_PLATFORM_MAINTENANCE_VERIFY,
        request=request,
        audit_sink=audit_sink,
        action="platform.audit.integrity.verify",
        resource_id="platform-audit",
    )
    response.headers["Cache-Control"] = "private, no-store"
    return service.verify_integrity(auth=auth, request_id=_request_id(request))


@router.get(
    "/audit/events:export",
    operation_id="exportPlatformAuditEvents",
    response_class=Response,
    responses={
        200: {
            "description": "Capability-controlled redacted NDJSON export.",
            "content": {"application/x-ndjson": {"schema": {"type": "string"}}},
        },
        401: _AUTHENTICATION_RESPONSE,
        403: _CAPABILITY_RESPONSE,
        409: _problem_response("The platform audit integrity chain failed verification."),
        422: _problem_response("The requested export window is too large."),
    },
    openapi_extra={
        "x-hc-platform-capability-policy": {
            "mode": "one-exact",
            "capabilities": [CAPABILITY_PLATFORM_MAINTENANCE_VERIFY],
        }
    },
)
def export_platform_audit_events(
    request: Request,
    auth: VerifiedAuth,
    service: PlatformAuditServiceDependency,
    audit_sink: MaintenanceAuditDependency,
    occurred_from: Annotated[datetime | None, Query()] = None,
    occurred_to: Annotated[datetime | None, Query()] = None,
) -> Response:
    _require_exact_with_audit(
        auth,
        CAPABILITY_PLATFORM_MAINTENANCE_VERIFY,
        request=request,
        audit_sink=audit_sink,
        action="platform.audit.events.export",
        resource_id="platform-audit",
    )
    exported = service.export(
        auth=auth,
        request_id=_request_id(request),
        occurred_from=occurred_from,
        occurred_to=occurred_to,
    )
    return Response(
        content=exported.payload,
        media_type=exported.media_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="{exported.filename}"',
            "X-Audit-Event-Count": str(exported.event_count),
        },
    )


@router.get(
    "/overview",
    operation_id="getPlatformOperationsOverview",
    response_model=PlatformOperationsOverview,
    responses={
        401: _AUTHENTICATION_RESPONSE,
        403: _CAPABILITY_RESPONSE,
    },
    openapi_extra={"x-hc-platform-capability-policy": _READ_CAPABILITY_POLICY},
)
def get_platform_operations_overview(
    request: Request,
    response: Response,
    auth: VerifiedAuth,
    service: PlatformOperationsOverviewDependency,
    audit_sink: MaintenanceAuditDependency,
) -> PlatformOperationsOverview:
    _require_exact_with_audit(
        auth,
        CAPABILITY_PLATFORM_ADMIN,
        CAPABILITY_PLATFORM_OPERATIONS_READ,
        request=request,
        audit_sink=audit_sink,
        action="platform.operations.overview",
        resource_id="current-environment",
    )
    result = service.overview()
    response.headers["Cache-Control"] = "private, no-store"
    capability = (
        CAPABILITY_PLATFORM_ADMIN
        if auth.has_exact_platform_capability(CAPABILITY_PLATFORM_ADMIN)
        else CAPABILITY_PLATFORM_OPERATIONS_READ
    )
    _append_platform_audit(
        audit_sink,
        request=request,
        auth=auth,
        action="platform.operations.overview.read",
        resource_type="platform_operations_overview",
        resource_id="current-environment",
        outcome="SUCCEEDED",
        safe_details={
            "capability_key": capability,
            "status": result.upgrade_preflight.status,
        },
    )
    return result


@router.get(
    "/logs",
    operation_id="queryPlatformRuntimeLogs",
    response_model=PlatformLogPage,
    responses={
        401: _AUTHENTICATION_RESPONSE,
        403: _CAPABILITY_RESPONSE,
        422: _problem_response("The runtime-log query is invalid."),
        502: _problem_response("The central runtime-log backend violated its fixed contract."),
        503: _problem_response("Central runtime-log search is temporarily unavailable."),
    },
    openapi_extra={"x-hc-platform-capability-policy": _READ_CAPABILITY_POLICY},
)
def query_platform_runtime_logs(
    request: Request,
    response: Response,
    auth: VerifiedAuth,
    service_dependency: PlatformLogServiceDependency,
    audit_sink: MaintenanceAuditDependency,
    occurred_from: Annotated[datetime, Query()],
    occurred_to: Annotated[datetime, Query()],
    service: Annotated[RuntimeLogService | None, Query()] = None,
    severity: Annotated[RuntimeLogSeverity | None, Query()] = None,
    event_code: Annotated[SafeEventCode | None, Query()] = None,
    request_id: Annotated[SafeCorrelation | None, Query()] = None,
    operation_id: Annotated[SafeCorrelation | None, Query()] = None,
    workflow_id: Annotated[SafeCorrelation | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> PlatformLogPage:
    _require_exact_with_audit(
        auth,
        CAPABILITY_PLATFORM_ADMIN,
        CAPABILITY_PLATFORM_OPERATIONS_READ,
        request=request,
        audit_sink=audit_sink,
        action="platform.logs.query",
        resource_id="central-runtime-logs",
    )
    try:
        result = service_dependency.query(
            occurred_from=occurred_from,
            occurred_to=occurred_to,
            service=service,
            severity=severity,
            event_code=event_code,
            request_id=request_id,
            operation_id=operation_id,
            workflow_id=workflow_id,
            limit=limit,
        )
    except PlatformLogQueryError as exc:
        _append_platform_audit(
            audit_sink,
            request=request,
            auth=auth,
            action="platform.logs.query",
            resource_type="platform_runtime_logs",
            resource_id="central-runtime-logs",
            outcome="FAILED",
            safe_details={"reason_code": exc.code},
        )
        status = 502 if exc.code == "PLATFORM_LOG_BACKEND_CONTRACT_INVALID" else 503
        if exc.code in {"PLATFORM_LOG_TIME_WINDOW_INVALID", "PLATFORM_LOG_LIMIT_INVALID"}:
            status = 422
        raise problem(
            status=status,
            code=exc.code,
            title="Platform runtime-log query failed",
            detail=(
                "The requested runtime-log window is invalid."
                if status == 422
                else "Central runtime-log search could not return a safe result."
            ),
            retryable=exc.retryable,
        ) from exc
    response.headers["Cache-Control"] = "private, no-store"
    capability = (
        CAPABILITY_PLATFORM_ADMIN
        if auth.has_exact_platform_capability(CAPABILITY_PLATFORM_ADMIN)
        else CAPABILITY_PLATFORM_OPERATIONS_READ
    )
    _append_platform_audit(
        audit_sink,
        request=request,
        auth=auth,
        action="platform.logs.queried",
        resource_type="platform_runtime_logs",
        resource_id="central-runtime-logs",
        outcome="SUCCEEDED",
        safe_details={"capability_key": capability, "event_count": result.count},
    )
    return result


def _operation_capability(operation_kind: OperationKind) -> str:
    if operation_kind in {"RELEASE", "MIGRATION"}:
        return CAPABILITY_PLATFORM_RELEASE_OPERATE
    if operation_kind == "RESTORE":
        return CAPABILITY_PLATFORM_BREAK_GLASS
    return CAPABILITY_PLATFORM_MAINTENANCE_OPERATE


def _authorized_operation_action(
    auth: AuthContext,
    operation: MaintenanceOperation,
    *,
    request: Request,
    audit_sink: MaintenanceWriteGate,
    action_name: str,
    action: Callable[[str], _T],
) -> _T:
    capability = _operation_capability(operation.operation_kind)
    _require_exact_with_audit(
        auth,
        capability,
        request=request,
        audit_sink=audit_sink,
        action=action_name,
        resource_id=operation.operation_id,
    )
    return action(capability)


def _authorized_transition(
    auth: AuthContext,
    operation: MaintenanceOperation,
    body: MaintenanceTransitionRequest,
    *,
    request: Request,
    audit_sink: MaintenanceWriteGate,
    action: Callable[[str], _T],
) -> _T:
    if (
        operation.state is MaintenanceState.FAILED_READ_ONLY
        or body.next_state is MaintenanceState.SUCCEEDED
    ):
        capability = CAPABILITY_PLATFORM_BREAK_GLASS
    else:
        capability = _operation_capability(operation.operation_kind)
    _require_exact_with_audit(
        auth,
        capability,
        request=request,
        audit_sink=audit_sink,
        action="platform.maintenance.transition",
        resource_id=operation.operation_id,
    )
    return action(capability)


def _request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    return request_id if isinstance(request_id, str) and request_id else "request-unavailable"


def _audit_context(
    request: Request,
    auth: AuthContext,
    capability_key: str,
) -> PlatformAuditContext:
    return PlatformAuditContext(
        actor_id=auth.subject_id,
        request_id=_request_id(request),
        capability_key=capability_key,
    )


def _append_platform_audit(
    sink: MaintenanceWriteGate,
    *,
    request: Request,
    auth: AuthContext,
    action: str,
    resource_type: str,
    resource_id: str,
    outcome: PlatformAuditOutcome,
    safe_details: dict[str, object],
) -> None:
    sink.append_platform_audit(
        PlatformAuditEvent(
            actor_id=auth.subject_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=_request_id(request),
            outcome=outcome,
            safe_details=safe_details,
        )
    )


def _require_exact_with_audit(
    auth: AuthContext,
    *capabilities: str,
    request: Request,
    audit_sink: MaintenanceWriteGate,
    action: str,
    resource_id: str,
) -> None:
    try:
        auth.require_exact_platform_capability(*capabilities)
    except ProblemException:
        _append_platform_audit(
            audit_sink,
            request=request,
            auth=auth,
            action=action,
            resource_type="platform_operation_authorization",
            resource_id=resource_id,
            outcome="DENIED",
            safe_details={"reason_code": "PLATFORM_CAPABILITY_REQUIRED"},
        )
        raise


def _maintenance_action(
    action: Callable[[], _T],
    *,
    on_failure: Callable[[str], None] | None = None,
) -> _T:
    try:
        return action()
    except MaintenanceContractError as exc:
        if on_failure is not None:
            on_failure(exc.code)
        status = (
            404
            if exc.code
            in {
                "PLATFORM_MAINTENANCE_NOT_FOUND",
                "PLATFORM_MAINTENANCE_ENVIRONMENT_NOT_FOUND",
            }
            else 503
            if exc.code == "PLATFORM_MAINTENANCE"
            else 409
        )
        if status == 404:
            title = "Maintenance operation not found"
            detail = "The maintenance operation was not found in this environment."
        elif status == 503:
            title = "Platform maintenance"
            detail = "The environment is read-only for maintenance."
        else:
            title = "Maintenance conflict"
            detail = "The maintenance command does not match the current operation state."
        raise problem(
            status=status,
            code=exc.code,
            title=title,
            detail=detail,
            retryable=status in {409, 503},
            retry_after_seconds=10 if status == 503 else None,
        ) from exc


def _configured_maintenance_action(
    repository: PostgresMaintenanceRepository,
    settings: Settings,
    operation_id: str,
    action: Callable[[MaintenanceOperation], _T],
    *,
    on_failure: Callable[[str], None] | None = None,
) -> _T:
    def run() -> _T:
        operation = repository.get_operation(operation_id)
        if operation.environment_id != settings.platform_environment_id:
            raise MaintenanceContractError(
                "PLATFORM_MAINTENANCE_NOT_FOUND",
                "the maintenance operation does not exist in this environment",
            )
        return action(operation)

    return _maintenance_action(run, on_failure=on_failure)


def _append_failed_audit(
    sink: MaintenanceWriteGate,
    *,
    request: Request,
    auth: AuthContext,
    action: str,
    operation_id: str,
    error_code: str,
) -> None:
    _append_platform_audit(
        sink,
        request=request,
        auth=auth,
        action=action,
        resource_type="maintenance_operation",
        resource_id=operation_id,
        outcome="FAILED",
        safe_details={"error_code": error_code},
    )


def _runtime_config_action(action: Callable[[], _T]) -> _T:
    try:
        return action()
    except RuntimeConfigError as exc:
        status = (
            404
            if exc.code == "PLATFORM_RUNTIME_CONFIG_REVISION_NOT_FOUND"
            else 409
            if exc.code == "PLATFORM_RUNTIME_CONFIG_REVISION_CONFLICT"
            else 422
        )
        title = (
            "Runtime configuration revision not found"
            if status == 404
            else "Runtime configuration conflict"
            if status == 409
            else "Runtime configuration rejected"
        )
        detail = (
            "The target runtime configuration revision does not exist."
            if status == 404
            else "The expected runtime configuration revision is stale."
            if status == 409
            else "The runtime configuration command violates the allowlisted schema."
        )
        raise problem(
            status=status,
            code=exc.code,
            title=title,
            detail=detail,
            retryable=status == 409,
        ) from exc


def _platform_project_action(action: Callable[[], _T]) -> _T:
    try:
        return action()
    except PlatformProjectError as exc:
        raise problem(
            status=409,
            code=exc.code,
            title="Platform project already exists",
            detail="The project already exists in the selected organization.",
            retryable=False,
        ) from exc


@router.get(
    "/projects",
    operation_id="listPlatformProjects",
    response_model=PlatformProjectPage,
    responses={401: _AUTHENTICATION_RESPONSE, 403: _CAPABILITY_RESPONSE},
    openapi_extra={
        "x-hc-platform-capability-policy": {
            "mode": "one-exact",
            "capabilities": [CAPABILITY_PLATFORM_ADMIN],
        }
    },
)
def list_platform_projects(
    request: Request,
    response: Response,
    auth: VerifiedAuth,
    service: PlatformProjectServiceDependency,
    audit_sink: MaintenanceAuditDependency,
) -> PlatformProjectPage:
    _require_exact_with_audit(
        auth,
        CAPABILITY_PLATFORM_ADMIN,
        request=request,
        audit_sink=audit_sink,
        action="platform.projects.list",
        resource_id="project-directory",
    )
    result = service.list_projects()
    response.headers["Cache-Control"] = "private, no-store"
    _append_platform_audit(
        audit_sink,
        request=request,
        auth=auth,
        action="platform.projects.listed",
        resource_type="platform_project_directory",
        resource_id="project-directory",
        outcome="SUCCEEDED",
        safe_details={"count": result.count},
    )
    return result


@router.post(
    "/projects",
    operation_id="createPlatformProject",
    response_model=PlatformProject,
    status_code=201,
    responses={
        401: _AUTHENTICATION_RESPONSE,
        403: _CAPABILITY_RESPONSE,
        409: _problem_response("The organization project already exists."),
    },
    openapi_extra={
        "x-hc-platform-capability-policy": {
            "mode": "one-exact",
            "capabilities": [CAPABILITY_PLATFORM_ADMIN],
        }
    },
)
def create_platform_project(
    request: Request,
    body: PlatformProjectCreate,
    response: Response,
    auth: VerifiedAuth,
    service: PlatformProjectServiceDependency,
    audit_sink: MaintenanceAuditDependency,
) -> PlatformProject:
    resource_id = f"{body.organization_id}:{body.project_id}"
    _require_exact_with_audit(
        auth,
        CAPABILITY_PLATFORM_ADMIN,
        request=request,
        audit_sink=audit_sink,
        action="platform.project.create",
        resource_id=resource_id,
    )
    try:
        result = _platform_project_action(lambda: service.create_project(body))
    except ProblemException as exc:
        _append_platform_audit(
            audit_sink,
            request=request,
            auth=auth,
            action="platform.project.create",
            resource_type="platform_project",
            resource_id=resource_id,
            outcome="FAILED",
            safe_details={"error_code": exc.problem.code},
        )
        raise
    response.headers["Cache-Control"] = "private, no-store"
    _append_platform_audit(
        audit_sink,
        request=request,
        auth=auth,
        action="platform.project.created",
        resource_type="platform_project",
        resource_id=resource_id,
        outcome="SUCCEEDED",
        safe_details={
            "organization_id": result.organization_id,
            "project_id": result.project_id,
        },
    )
    return result


def _object_store_config_action(action: Callable[[], _T]) -> _T:
    try:
        return action()
    except ObjectStoreConfigurationError as exc:
        status = 409 if exc.code == "PLATFORM_OBJECT_STORE_CONFIG_REVISION_CONFLICT" else 422
        raise problem(
            status=status,
            code=exc.code,
            title=(
                "Object-store configuration conflict"
                if status == 409
                else "Object-store configuration rejected"
            ),
            detail=(
                "The expected object-store configuration revision is stale."
                if status == 409
                else "Complete credentials are required for the first configuration."
            ),
            retryable=status == 409,
        ) from exc


@router.get(
    "/object-store-location",
    operation_id="getPlatformObjectStoreLocation",
    response_model=ObjectStoreLocationStatus,
    responses={401: _AUTHENTICATION_RESPONSE},
)
def get_object_store_location(
    response: Response,
    auth: VerifiedAuth,
    service: ObjectStoreConfigServiceDependency,
) -> ObjectStoreLocationStatus:
    del auth
    response.headers["Cache-Control"] = "private, no-store"
    return service.location()


@router.get(
    "/object-store-config",
    operation_id="getPlatformObjectStoreConfig",
    response_model=ObjectStoreConfigurationStatus,
    responses={401: _AUTHENTICATION_RESPONSE, 403: _CAPABILITY_RESPONSE},
    openapi_extra={
        "x-hc-platform-capability-policy": {
            "mode": "one-exact",
            "capabilities": [CAPABILITY_PLATFORM_ADMIN],
        }
    },
)
def get_object_store_config(
    request: Request,
    response: Response,
    auth: VerifiedAuth,
    service: ObjectStoreConfigServiceDependency,
    audit_sink: MaintenanceAuditDependency,
) -> ObjectStoreConfigurationStatus:
    _require_exact_with_audit(
        auth,
        CAPABILITY_PLATFORM_ADMIN,
        request=request,
        audit_sink=audit_sink,
        action="platform.object_store_config.read",
        resource_id="current-environment",
    )
    result = service.current()
    response.headers["Cache-Control"] = "private, no-store"
    _append_platform_audit(
        audit_sink,
        request=request,
        auth=auth,
        action="platform.object_store_config.read",
        resource_type="platform_object_store_config",
        resource_id=str(result.revision),
        outcome="SUCCEEDED",
        safe_details={
            "revision": result.revision,
            "configured": result.configured,
            "source": result.source,
        },
    )
    return result


@router.put(
    "/object-store-config",
    operation_id="updatePlatformObjectStoreConfig",
    response_model=ObjectStoreConfigurationStatus,
    responses={
        401: _AUTHENTICATION_RESPONSE,
        403: _CAPABILITY_RESPONSE,
        409: _problem_response("The expected object-store configuration revision is stale."),
        422: _problem_response("The object-store configuration is incomplete or invalid."),
    },
    openapi_extra={
        "x-hc-platform-capability-policy": {
            "mode": "one-exact",
            "capabilities": [CAPABILITY_PLATFORM_ADMIN],
        }
    },
)
def update_object_store_config(
    request: Request,
    body: ObjectStoreConfigurationUpdate,
    response: Response,
    auth: VerifiedAuth,
    service: ObjectStoreConfigServiceDependency,
    audit_sink: MaintenanceAuditDependency,
) -> ObjectStoreConfigurationStatus:
    _require_exact_with_audit(
        auth,
        CAPABILITY_PLATFORM_ADMIN,
        request=request,
        audit_sink=audit_sink,
        action="platform.object_store_config.update",
        resource_id=str(body.expected_revision + 1),
    )
    try:
        result = _object_store_config_action(
            lambda: service.save(
                body,
                actor_id=auth.subject_id,
                request_id=_request_id(request),
            )
        )
    except ProblemException as exc:
        _append_platform_audit(
            audit_sink,
            request=request,
            auth=auth,
            action="platform.object_store_config.update",
            resource_type="platform_object_store_config",
            resource_id=str(body.expected_revision + 1),
            outcome="FAILED",
            safe_details={"error_code": exc.problem.code},
        )
        raise
    response.headers["Cache-Control"] = "private, no-store"
    _append_platform_audit(
        audit_sink,
        request=request,
        auth=auth,
        action="platform.object_store_config.updated",
        resource_type="platform_object_store_config",
        resource_id=str(result.revision),
        outcome="SUCCEEDED",
        safe_details={
            "revision": result.revision,
            "provider": result.provider,
            "activation_required": result.activation_required,
        },
    )
    return result


def _runtime_config_read_capability(auth: AuthContext) -> str:
    return (
        CAPABILITY_PLATFORM_ADMIN
        if auth.has_exact_platform_capability(CAPABILITY_PLATFORM_ADMIN)
        else CAPABILITY_PLATFORM_OPERATIONS_READ
    )


@router.get(
    "/runtime-config",
    operation_id="getPlatformRuntimeConfig",
    response_model=RuntimeConfigSnapshot,
    responses={
        401: _AUTHENTICATION_RESPONSE,
        403: _CAPABILITY_RESPONSE,
    },
    openapi_extra={"x-hc-platform-capability-policy": _READ_CAPABILITY_POLICY},
)
def get_runtime_config(
    request: Request,
    response: Response,
    auth: VerifiedAuth,
    service: RuntimeConfigServiceDependency,
    audit_sink: MaintenanceAuditDependency,
) -> RuntimeConfigSnapshot:
    _require_exact_with_audit(
        auth,
        CAPABILITY_PLATFORM_ADMIN,
        CAPABILITY_PLATFORM_OPERATIONS_READ,
        request=request,
        audit_sink=audit_sink,
        action="platform.runtime_config.read",
        resource_id="current-environment",
    )
    result = service.current()
    response.headers["Cache-Control"] = "private, no-store"
    _append_platform_audit(
        audit_sink,
        request=request,
        auth=auth,
        action="platform.runtime_config.read",
        resource_type="platform_runtime_config_revision",
        resource_id=str(result.revision),
        outcome="SUCCEEDED",
        safe_details={
            "capability_key": _runtime_config_read_capability(auth),
            "revision": result.revision,
            "schema_version": result.schema_version,
        },
    )
    return result


@router.get(
    "/runtime-config/revisions",
    operation_id="listPlatformRuntimeConfigRevisions",
    response_model=RuntimeConfigHistoryPage,
    responses={
        401: _AUTHENTICATION_RESPONSE,
        403: _CAPABILITY_RESPONSE,
    },
    openapi_extra={"x-hc-platform-capability-policy": _READ_CAPABILITY_POLICY},
)
def list_runtime_config_revisions(
    request: Request,
    response: Response,
    auth: VerifiedAuth,
    service: RuntimeConfigServiceDependency,
    audit_sink: MaintenanceAuditDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> RuntimeConfigHistoryPage:
    _require_exact_with_audit(
        auth,
        CAPABILITY_PLATFORM_ADMIN,
        CAPABILITY_PLATFORM_OPERATIONS_READ,
        request=request,
        audit_sink=audit_sink,
        action="platform.runtime_config.history",
        resource_id="current-environment",
    )
    result = service.history(limit=limit)
    response.headers["Cache-Control"] = "private, no-store"
    _append_platform_audit(
        audit_sink,
        request=request,
        auth=auth,
        action="platform.runtime_config.history",
        resource_type="platform_runtime_config_revision",
        resource_id="current-environment",
        outcome="SUCCEEDED",
        safe_details={
            "capability_key": _runtime_config_read_capability(auth),
            "count": result.count,
        },
    )
    return result


@router.post(
    "/runtime-config/revisions",
    operation_id="publishPlatformRuntimeConfigRevision",
    response_model=RuntimeConfigSnapshot,
    status_code=201,
    responses={
        401: _AUTHENTICATION_RESPONSE,
        403: _CAPABILITY_RESPONSE,
        409: _RUNTIME_CONFIG_CONFLICT_RESPONSE,
        422: _problem_response("The runtime configuration command is not allowlisted."),
    },
    openapi_extra={
        "x-hc-platform-capability-policy": {
            "mode": "one-exact",
            "capabilities": [CAPABILITY_PLATFORM_RELEASE_OPERATE],
        }
    },
)
def publish_runtime_config_revision(
    request: Request,
    body: RuntimeConfigPublishRequest,
    response: Response,
    auth: VerifiedAuth,
    service: RuntimeConfigServiceDependency,
    audit_sink: MaintenanceAuditDependency,
) -> RuntimeConfigSnapshot:
    _require_exact_with_audit(
        auth,
        CAPABILITY_PLATFORM_RELEASE_OPERATE,
        request=request,
        audit_sink=audit_sink,
        action="platform.runtime_config.publish",
        resource_id=str(body.expected_revision + 1),
    )
    try:
        result = _runtime_config_action(
            lambda: service.publish(
                expected_revision=body.expected_revision,
                schema_version=body.schema_version,
                patch=body.values,
                actor_id=auth.subject_id,
                reason=body.reason,
                request_id=_request_id(request),
            )
        )
    except ProblemException as exc:
        _append_platform_audit(
            audit_sink,
            request=request,
            auth=auth,
            action="platform.runtime_config.publish",
            resource_type="platform_runtime_config_revision",
            resource_id=str(body.expected_revision + 1),
            outcome="FAILED",
            safe_details={"error_code": exc.problem.code},
        )
        raise
    response.headers["Cache-Control"] = "private, no-store"
    _append_platform_audit(
        audit_sink,
        request=request,
        auth=auth,
        action="platform.runtime_config.published",
        resource_type="platform_runtime_config_revision",
        resource_id=str(result.revision),
        outcome="SUCCEEDED",
        safe_details={
            "capability_key": CAPABILITY_PLATFORM_RELEASE_OPERATE,
            "previous_revision": body.expected_revision,
            "revision": result.revision,
            "schema_version": result.schema_version,
            "environment_id": service.environment_id,
            "reason_recorded": True,
            "changed_keys": sorted(body.values),
        },
    )
    return result


@router.post(
    "/runtime-config/revisions/{target_revision}:rollback",
    operation_id="rollbackPlatformRuntimeConfigRevision",
    response_model=RuntimeConfigSnapshot,
    status_code=201,
    responses={
        401: _AUTHENTICATION_RESPONSE,
        403: _CAPABILITY_RESPONSE,
        404: _problem_response("The target runtime configuration revision was not found."),
        409: _RUNTIME_CONFIG_CONFLICT_RESPONSE,
        422: _problem_response("The runtime configuration command is invalid."),
    },
    openapi_extra={
        "x-hc-platform-capability-policy": {
            "mode": "one-exact",
            "capabilities": [CAPABILITY_PLATFORM_RELEASE_OPERATE],
        }
    },
)
def rollback_runtime_config_revision(
    request: Request,
    body: RuntimeConfigRollbackRequest,
    response: Response,
    auth: VerifiedAuth,
    service: RuntimeConfigServiceDependency,
    audit_sink: MaintenanceAuditDependency,
    target_revision: Annotated[int, Path(ge=0)],
) -> RuntimeConfigSnapshot:
    _require_exact_with_audit(
        auth,
        CAPABILITY_PLATFORM_RELEASE_OPERATE,
        request=request,
        audit_sink=audit_sink,
        action="platform.runtime_config.rollback",
        resource_id=str(target_revision),
    )
    try:
        result = _runtime_config_action(
            lambda: service.rollback(
                target_revision=target_revision,
                expected_revision=body.expected_revision,
                actor_id=auth.subject_id,
                reason=body.reason,
                request_id=_request_id(request),
            )
        )
    except ProblemException as exc:
        _append_platform_audit(
            audit_sink,
            request=request,
            auth=auth,
            action="platform.runtime_config.rollback",
            resource_type="platform_runtime_config_revision",
            resource_id=str(target_revision),
            outcome="FAILED",
            safe_details={"error_code": exc.problem.code},
        )
        raise
    response.headers["Cache-Control"] = "private, no-store"
    _append_platform_audit(
        audit_sink,
        request=request,
        auth=auth,
        action="platform.runtime_config.rolled_back",
        resource_type="platform_runtime_config_revision",
        resource_id=str(result.revision),
        outcome="SUCCEEDED",
        safe_details={
            "capability_key": CAPABILITY_PLATFORM_RELEASE_OPERATE,
            "previous_revision": body.expected_revision,
            "revision": result.revision,
            "target_revision": target_revision,
            "environment_id": service.environment_id,
            "reason_recorded": True,
        },
    )
    return result


@router.get(
    "/instances",
    operation_id="listPlatformInstances",
    response_model=PlatformInstancePage,
    responses={
        401: _AUTHENTICATION_RESPONSE,
        403: _CAPABILITY_RESPONSE,
    },
    openapi_extra={"x-hc-platform-capability-policy": _READ_CAPABILITY_POLICY},
)
def list_platform_instances(
    request: Request,
    response: Response,
    auth: VerifiedAuth,
    service: InstanceServiceDependency,
    audit_sink: MaintenanceAuditDependency,
    stale: bool | None = None,
    role: Annotated[InstanceRole | None, Query()] = None,
) -> PlatformInstancePage:
    _require_exact_with_audit(
        auth,
        CAPABILITY_PLATFORM_ADMIN,
        CAPABILITY_PLATFORM_OPERATIONS_READ,
        request=request,
        audit_sink=audit_sink,
        action="platform.instances.list",
        resource_id="current-environment",
    )
    response.headers["Cache-Control"] = "private, no-store"
    result = service.list_instances(stale=stale, role=role)
    capability = (
        CAPABILITY_PLATFORM_ADMIN
        if auth.has_exact_platform_capability(CAPABILITY_PLATFORM_ADMIN)
        else CAPABILITY_PLATFORM_OPERATIONS_READ
    )
    _append_platform_audit(
        audit_sink,
        request=request,
        auth=auth,
        action="platform.instances.listed",
        resource_type="platform_instance_directory",
        resource_id="current-environment",
        outcome="SUCCEEDED",
        safe_details={"capability_key": capability},
    )
    return result


@router.get(
    "/backups",
    operation_id="listPlatformBackups",
    response_model=BackupCatalogPage,
    responses={
        401: _AUTHENTICATION_RESPONSE,
        403: _CAPABILITY_RESPONSE,
        422: _problem_response("The backup catalog query is invalid."),
    },
    openapi_extra={"x-hc-platform-capability-policy": _READ_CAPABILITY_POLICY},
)
def list_platform_backups(
    request: Request,
    response: Response,
    auth: VerifiedAuth,
    repository: BackupCatalogRepositoryDependency,
    settings: SettingsDependency,
    audit_sink: MaintenanceAuditDependency,
    status: Annotated[CatalogStatus | None, Query()] = None,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> BackupCatalogPage:
    _require_exact_with_audit(
        auth,
        CAPABILITY_PLATFORM_ADMIN,
        CAPABILITY_PLATFORM_OPERATIONS_READ,
        request=request,
        audit_sink=audit_sink,
        action="platform.backups.list",
        resource_id="current-environment",
    )
    try:
        result = repository.list_page(
            source_environment_id=settings.platform_environment_id,
            status=status,
            cursor=cursor,
            limit=limit,
        )
    except BackupCatalogError as exc:
        raise problem(
            status=422,
            code=exc.code,
            title="Backup catalog query invalid",
            detail="The backup catalog cursor or filter is invalid.",
            retryable=False,
        ) from exc
    response.headers["Cache-Control"] = "private, no-store"
    capability = (
        CAPABILITY_PLATFORM_ADMIN
        if auth.has_exact_platform_capability(CAPABILITY_PLATFORM_ADMIN)
        else CAPABILITY_PLATFORM_OPERATIONS_READ
    )
    _append_platform_audit(
        audit_sink,
        request=request,
        auth=auth,
        action="platform.backups.listed",
        resource_type="platform_backup_catalog",
        resource_id="current-environment",
        outcome="SUCCEEDED",
        safe_details={
            "capability_key": capability,
            "count": result.count,
            "status_filter": status,
        },
    )
    return result


@router.post(
    "/maintenance-operations",
    operation_id="requestPlatformMaintenanceOperation",
    response_model=MaintenanceOperation,
    status_code=201,
    responses={
        401: _AUTHENTICATION_RESPONSE,
        403: _CAPABILITY_RESPONSE,
        409: _CONFLICT_RESPONSE,
    },
    openapi_extra={"x-hc-platform-capability-policy": _OPERATION_CAPABILITY_POLICY},
)
def request_maintenance_operation(
    request: Request,
    body: MaintenanceOperationRequest,
    response: Response,
    auth: VerifiedAuth,
    repository: MaintenanceRepositoryDependency,
    settings: SettingsDependency,
) -> MaintenanceOperation:
    capability = _operation_capability(body.operation_kind)
    _require_exact_with_audit(
        auth,
        capability,
        request=request,
        audit_sink=repository,
        action="platform.maintenance.request",
        resource_id=body.operation_id,
    )
    response.headers["Cache-Control"] = "private, no-store"
    return _maintenance_action(
        lambda: repository.request_operation(
            operation_id=body.operation_id,
            environment_id=settings.platform_environment_id,
            operation_kind=body.operation_kind,
            plan_digest=body.plan_digest,
            requested_by=auth.subject_id,
            audit=_audit_context(
                request,
                auth,
                capability,
            ),
        ),
        on_failure=lambda error_code: _append_failed_audit(
            repository,
            request=request,
            auth=auth,
            action="platform.maintenance.request",
            operation_id=body.operation_id,
            error_code=error_code,
        ),
    )


@router.get(
    "/maintenance-operations/{operation_id}",
    operation_id="getPlatformMaintenanceOperation",
    response_model=MaintenanceOperation,
    responses={
        401: _AUTHENTICATION_RESPONSE,
        403: _CAPABILITY_RESPONSE,
        404: _NOT_FOUND_RESPONSE,
    },
    openapi_extra={"x-hc-platform-capability-policy": _READ_CAPABILITY_POLICY},
)
def get_maintenance_operation(
    request: Request,
    operation_id: str,
    response: Response,
    auth: VerifiedAuth,
    repository: MaintenanceRepositoryDependency,
    settings: SettingsDependency,
    audit_sink: MaintenanceAuditDependency,
) -> MaintenanceOperation:
    _require_exact_with_audit(
        auth,
        CAPABILITY_PLATFORM_ADMIN,
        CAPABILITY_PLATFORM_OPERATIONS_READ,
        request=request,
        audit_sink=audit_sink,
        action="platform.maintenance.read",
        resource_id=operation_id,
    )
    response.headers["Cache-Control"] = "private, no-store"
    operation = _configured_maintenance_action(
        repository,
        settings,
        operation_id,
        lambda operation: operation,
        on_failure=lambda error_code: _append_failed_audit(
            audit_sink,
            request=request,
            auth=auth,
            action="platform.maintenance.read",
            operation_id=operation_id,
            error_code=error_code,
        ),
    )
    capability = (
        CAPABILITY_PLATFORM_ADMIN
        if auth.has_exact_platform_capability(CAPABILITY_PLATFORM_ADMIN)
        else CAPABILITY_PLATFORM_OPERATIONS_READ
    )
    _append_platform_audit(
        audit_sink,
        request=request,
        auth=auth,
        action="platform.maintenance.read",
        resource_type="maintenance_operation",
        resource_id=operation.operation_id,
        outcome="SUCCEEDED",
        safe_details={"capability_key": capability},
    )
    return operation


@router.post(
    "/maintenance-operations/{operation_id}:acquire",
    operation_id="acquirePlatformMaintenanceOperation",
    response_model=MaintenanceOperation,
    responses={
        401: _AUTHENTICATION_RESPONSE,
        403: _CAPABILITY_RESPONSE,
        404: _NOT_FOUND_RESPONSE,
        409: _CONFLICT_RESPONSE,
    },
    openapi_extra={"x-hc-platform-capability-policy": _OPERATION_CAPABILITY_POLICY},
)
def acquire_maintenance_operation(
    request: Request,
    operation_id: str,
    body: MaintenanceOwnerRequest,
    response: Response,
    auth: VerifiedAuth,
    repository: MaintenanceRepositoryDependency,
    settings: SettingsDependency,
) -> MaintenanceOperation:
    _require_exact_with_audit(
        auth,
        *_COMMAND_CAPABILITIES,
        request=request,
        audit_sink=repository,
        action="platform.maintenance.acquire",
        resource_id=operation_id,
    )
    response.headers["Cache-Control"] = "private, no-store"
    return _configured_maintenance_action(
        repository,
        settings,
        operation_id,
        lambda operation: _authorized_operation_action(
            auth,
            operation,
            request=request,
            audit_sink=repository,
            action_name="platform.maintenance.acquire",
            action=lambda capability: repository.acquire(
                operation_id,
                owner_instance_id=body.owner_instance_id,
                audit=_audit_context(request, auth, capability),
            ),
        ),
        on_failure=lambda error_code: _append_failed_audit(
            repository,
            request=request,
            auth=auth,
            action="platform.maintenance.acquire",
            operation_id=operation_id,
            error_code=error_code,
        ),
    )


@router.post(
    "/maintenance-operations/{operation_id}:renew",
    operation_id="renewPlatformMaintenanceOperation",
    response_model=MaintenanceOperation,
    responses={
        401: _AUTHENTICATION_RESPONSE,
        403: _CAPABILITY_RESPONSE,
        404: _NOT_FOUND_RESPONSE,
        409: _CONFLICT_RESPONSE,
    },
    openapi_extra={"x-hc-platform-capability-policy": _OPERATION_CAPABILITY_POLICY},
)
def renew_maintenance_operation(
    request: Request,
    operation_id: str,
    body: MaintenanceLeaseRequest,
    response: Response,
    auth: VerifiedAuth,
    repository: MaintenanceRepositoryDependency,
    settings: SettingsDependency,
) -> MaintenanceOperation:
    _require_exact_with_audit(
        auth,
        *_COMMAND_CAPABILITIES,
        request=request,
        audit_sink=repository,
        action="platform.maintenance.renew",
        resource_id=operation_id,
    )
    response.headers["Cache-Control"] = "private, no-store"
    return _configured_maintenance_action(
        repository,
        settings,
        operation_id,
        lambda operation: _authorized_operation_action(
            auth,
            operation,
            request=request,
            audit_sink=repository,
            action_name="platform.maintenance.renew",
            action=lambda capability: repository.renew(
                operation_id,
                owner_instance_id=body.owner_instance_id,
                fencing_token=body.fencing_token,
                audit=_audit_context(request, auth, capability),
            ),
        ),
        on_failure=lambda error_code: _append_failed_audit(
            repository,
            request=request,
            auth=auth,
            action="platform.maintenance.renew",
            operation_id=operation_id,
            error_code=error_code,
        ),
    )


@router.post(
    "/maintenance-operations/{operation_id}:takeover",
    operation_id="takeoverPlatformMaintenanceOperation",
    response_model=MaintenanceOperation,
    responses={
        401: _AUTHENTICATION_RESPONSE,
        403: _CAPABILITY_RESPONSE,
        404: _NOT_FOUND_RESPONSE,
        409: _CONFLICT_RESPONSE,
    },
    openapi_extra={
        "x-hc-platform-capability-policy": {
            "mode": "one-exact",
            "capabilities": [CAPABILITY_PLATFORM_BREAK_GLASS],
        }
    },
)
def takeover_maintenance_operation(
    request: Request,
    operation_id: str,
    body: MaintenanceOwnerRequest,
    response: Response,
    auth: VerifiedAuth,
    repository: MaintenanceRepositoryDependency,
    settings: SettingsDependency,
) -> MaintenanceOperation:
    _require_exact_with_audit(
        auth,
        CAPABILITY_PLATFORM_BREAK_GLASS,
        request=request,
        audit_sink=repository,
        action="platform.maintenance.takeover",
        resource_id=operation_id,
    )
    response.headers["Cache-Control"] = "private, no-store"
    return _configured_maintenance_action(
        repository,
        settings,
        operation_id,
        lambda _: repository.takeover(
            operation_id,
            new_owner_instance_id=body.owner_instance_id,
            audit=_audit_context(request, auth, CAPABILITY_PLATFORM_BREAK_GLASS),
        ),
        on_failure=lambda error_code: _append_failed_audit(
            repository,
            request=request,
            auth=auth,
            action="platform.maintenance.takeover",
            operation_id=operation_id,
            error_code=error_code,
        ),
    )


@router.post(
    "/maintenance-operations/{operation_id}:transition",
    operation_id="transitionPlatformMaintenanceOperation",
    response_model=MaintenanceOperation,
    responses={
        401: _AUTHENTICATION_RESPONSE,
        403: _CAPABILITY_RESPONSE,
        404: _NOT_FOUND_RESPONSE,
        409: _CONFLICT_RESPONSE,
    },
    openapi_extra={"x-hc-platform-capability-policy": _TRANSITION_CAPABILITY_POLICY},
)
def transition_maintenance_operation(
    request: Request,
    operation_id: str,
    body: MaintenanceTransitionRequest,
    response: Response,
    auth: VerifiedAuth,
    repository: MaintenanceRepositoryDependency,
    settings: SettingsDependency,
) -> MaintenanceOperation:
    _require_exact_with_audit(
        auth,
        *_COMMAND_CAPABILITIES,
        request=request,
        audit_sink=repository,
        action="platform.maintenance.transition",
        resource_id=operation_id,
    )
    response.headers["Cache-Control"] = "private, no-store"
    command = MaintenanceCommandV1(
        operation_id=operation_id,
        environment_id=settings.platform_environment_id,
        owner_instance_id=str(body.owner_instance_id),
        fencing_token=body.fencing_token,
        expected_state=body.expected_state,
        expected_state_version=body.expected_state_version,
        next_state=body.next_state,
        manual_approval_id=body.manual_approval_id,
    )
    return _configured_maintenance_action(
        repository,
        settings,
        operation_id,
        lambda operation: _authorized_transition(
            auth,
            operation,
            body,
            request=request,
            audit_sink=repository,
            action=lambda capability: repository.transition(
                command,
                audit=_audit_context(request, auth, capability),
            ),
        ),
        on_failure=lambda error_code: _append_failed_audit(
            repository,
            request=request,
            auth=auth,
            action="platform.maintenance.transition",
            operation_id=operation_id,
            error_code=error_code,
        ),
    )


@router.post(
    "/maintenance-operations/{operation_id}:reconcile",
    operation_id="reconcilePlatformMaintenanceOperation",
    response_model=MaintenanceOperation,
    responses={
        401: _AUTHENTICATION_RESPONSE,
        403: _CAPABILITY_RESPONSE,
        404: _NOT_FOUND_RESPONSE,
        409: _CONFLICT_RESPONSE,
    },
    openapi_extra={
        "x-hc-platform-capability-policy": {
            "mode": "one-exact",
            "capabilities": [CAPABILITY_PLATFORM_MAINTENANCE_VERIFY],
        }
    },
)
def reconcile_maintenance_operation(
    request: Request,
    operation_id: str,
    body: MaintenanceReconciliationRequest,
    response: Response,
    auth: VerifiedAuth,
    repository: MaintenanceRepositoryDependency,
    settings: SettingsDependency,
) -> MaintenanceOperation:
    _require_exact_with_audit(
        auth,
        CAPABILITY_PLATFORM_MAINTENANCE_VERIFY,
        request=request,
        audit_sink=repository,
        action="platform.maintenance.reconcile",
        resource_id=operation_id,
    )
    response.headers["Cache-Control"] = "private, no-store"
    return _configured_maintenance_action(
        repository,
        settings,
        operation_id,
        lambda _: repository.mark_reconciliation_passed(
            operation_id,
            owner_instance_id=body.owner_instance_id,
            fencing_token=body.fencing_token,
            expected_state_version=body.expected_state_version,
            audit=_audit_context(
                request,
                auth,
                CAPABILITY_PLATFORM_MAINTENANCE_VERIFY,
            ),
        ),
        on_failure=lambda error_code: _append_failed_audit(
            repository,
            request=request,
            auth=auth,
            action="platform.maintenance.reconcile",
            operation_id=operation_id,
            error_code=error_code,
        ),
    )
