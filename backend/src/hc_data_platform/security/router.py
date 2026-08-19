"""HTTP boundary for direct accounts and project-scoped access requests."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response, status

from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import problem

from .access_models import (
    AccessAuditEventList,
    AccessDecisionCommand,
    AccessRequestStatus,
    CapabilityRequest,
    CapabilityRequestCreate,
    CapabilityRequestList,
    LoginCommand,
    MembershipRequest,
    MembershipRequestCreate,
    MembershipRequestList,
    RegistrationCommand,
    RegistrationResult,
    SessionBootstrap,
    SessionCreated,
)
from .access_repository import InMemoryAccessRepository
from .access_service import AccessService
from .http import VerifiedAuth

router = APIRouter(prefix="/api/v1", tags=["access"])

_default_service = AccessService(InMemoryAccessRepository())

PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {
        "description": "Authentication failed or is required.",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        },
    },
    403: {
        "description": "The exact project scope is not granted.",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        },
    },
    409: {
        "description": "State, concurrency, or idempotency conflict.",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        },
    },
    422: {
        "description": "The request violates the API contract.",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        },
    },
    429: {
        "description": "An externally configured abuse policy denied the request.",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        },
    },
}


def configure_access_service(service: AccessService) -> None:
    global _default_service
    _default_service = service


def get_access_service(request: Request) -> AccessService:
    configured = getattr(request.app.state, "access_service", None)
    return configured if isinstance(configured, AccessService) else _default_service


AccessServiceDependency = Annotated[AccessService, Depends(get_access_service)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=256)]


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


def _bearer_token(request: Request) -> str:
    authorization = request.headers.get("Authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not token.strip():
        raise problem(
            status=401,
            code="AUTHENTICATION_REQUIRED",
            title="Authentication required",
            detail="A current Bearer session is required.",
        )
    return token.strip()


@router.post(
    "/auth/registrations",
    operation_id="registerAccount",
    response_model=RegistrationResult,
    status_code=status.HTTP_201_CREATED,
    responses={
        409: PROBLEM_RESPONSES[409],
        422: PROBLEM_RESPONSES[422],
        429: PROBLEM_RESPONSES[429],
    },
)
def register_account(
    command: RegistrationCommand,
    response: Response,
    service: AccessServiceDependency,
) -> RegistrationResult:
    _no_store(response)
    return service.register(command, request_id=current_request_context().request_id)


@router.post(
    "/auth/sessions",
    operation_id="createSession",
    response_model=SessionCreated,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: PROBLEM_RESPONSES[401],
        422: PROBLEM_RESPONSES[422],
        429: PROBLEM_RESPONSES[429],
    },
)
def create_session(
    command: LoginCommand,
    response: Response,
    service: AccessServiceDependency,
) -> SessionCreated:
    _no_store(response)
    return service.login(command, request_id=current_request_context().request_id)


@router.get(
    "/auth/session/bootstrap",
    operation_id="getSessionBootstrap",
    response_model=SessionBootstrap,
    responses={401: PROBLEM_RESPONSES[401]},
)
def bootstrap_session(
    request: Request,
    response: Response,
    _: VerifiedAuth,
    service: AccessServiceDependency,
) -> SessionBootstrap:
    _no_store(response)
    return service.bootstrap(_bearer_token(request))


@router.post(
    "/auth/session:logout",
    operation_id="logoutSession",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={401: PROBLEM_RESPONSES[401]},
)
def logout_session(
    request: Request,
    response: Response,
    _: VerifiedAuth,
    service: AccessServiceDependency,
) -> None:
    _no_store(response)
    service.logout(
        _bearer_token(request),
        request_id=current_request_context().request_id,
    )


@router.post(
    "/projects/{project_id}/membership-requests",
    operation_id="createMembershipRequest",
    response_model=MembershipRequest,
    status_code=status.HTTP_201_CREATED,
    responses=PROBLEM_RESPONSES,
)
def create_membership_request(
    project_id: str,
    command: MembershipRequestCreate,
    response: Response,
    auth: VerifiedAuth,
    service: AccessServiceDependency,
    idempotency_key: IdempotencyKey,
) -> MembershipRequest:
    _no_store(response)
    return service.create_membership_request(
        auth=auth,
        project_id=project_id,
        command=command,
        idempotency_key=idempotency_key,
        request_id=current_request_context().request_id,
    )


@router.get(
    "/projects/{project_id}/membership-requests",
    operation_id="listMembershipRequests",
    response_model=MembershipRequestList,
    responses={
        401: PROBLEM_RESPONSES[401],
        403: PROBLEM_RESPONSES[403],
        422: PROBLEM_RESPONSES[422],
    },
)
def list_membership_requests(
    project_id: str,
    response: Response,
    auth: VerifiedAuth,
    service: AccessServiceDependency,
) -> MembershipRequestList:
    _no_store(response)
    return service.list_membership_requests(auth=auth, project_id=project_id)


@router.get(
    "/projects/{project_id}/membership-requests/{access_request_id}",
    operation_id="getMembershipRequest",
    response_model=MembershipRequest,
    responses={
        401: PROBLEM_RESPONSES[401],
        403: PROBLEM_RESPONSES[403],
        422: PROBLEM_RESPONSES[422],
    },
)
def get_membership_request(
    project_id: str,
    access_request_id: UUID,
    response: Response,
    auth: VerifiedAuth,
    service: AccessServiceDependency,
) -> MembershipRequest:
    _no_store(response)
    return service.get_membership_request(
        auth=auth,
        project_id=project_id,
        access_request_id=str(access_request_id),
    )


def _membership_decision(
    *,
    project_id: str,
    access_request_id: str,
    command: AccessDecisionCommand,
    auth: VerifiedAuth,
    service: AccessService,
    idempotency_key: str,
    target_status: AccessRequestStatus,
) -> MembershipRequest:
    return service.decide_membership_request(
        auth=auth,
        project_id=project_id,
        access_request_id=access_request_id,
        target_status=target_status,
        command=command,
        idempotency_key=idempotency_key,
        request_id=current_request_context().request_id,
    )


@router.post(
    "/projects/{project_id}/membership-requests/{access_request_id}:approve",
    operation_id="approveMembershipRequest",
    response_model=MembershipRequest,
    responses=PROBLEM_RESPONSES,
)
def approve_membership_request(
    project_id: str,
    access_request_id: UUID,
    command: AccessDecisionCommand,
    response: Response,
    auth: VerifiedAuth,
    service: AccessServiceDependency,
    idempotency_key: IdempotencyKey,
) -> MembershipRequest:
    _no_store(response)
    return _membership_decision(
        project_id=project_id,
        access_request_id=str(access_request_id),
        command=command,
        auth=auth,
        service=service,
        idempotency_key=idempotency_key,
        target_status=AccessRequestStatus.APPROVED,
    )


@router.post(
    "/projects/{project_id}/membership-requests/{access_request_id}:reject",
    operation_id="rejectMembershipRequest",
    response_model=MembershipRequest,
    responses=PROBLEM_RESPONSES,
)
def reject_membership_request(
    project_id: str,
    access_request_id: UUID,
    command: AccessDecisionCommand,
    response: Response,
    auth: VerifiedAuth,
    service: AccessServiceDependency,
    idempotency_key: IdempotencyKey,
) -> MembershipRequest:
    _no_store(response)
    return _membership_decision(
        project_id=project_id,
        access_request_id=str(access_request_id),
        command=command,
        auth=auth,
        service=service,
        idempotency_key=idempotency_key,
        target_status=AccessRequestStatus.REJECTED,
    )


@router.post(
    "/projects/{project_id}/membership-requests/{access_request_id}:revoke",
    operation_id="revokeMembershipRequest",
    response_model=MembershipRequest,
    responses=PROBLEM_RESPONSES,
)
def revoke_membership_request(
    project_id: str,
    access_request_id: UUID,
    command: AccessDecisionCommand,
    response: Response,
    auth: VerifiedAuth,
    service: AccessServiceDependency,
    idempotency_key: IdempotencyKey,
) -> MembershipRequest:
    _no_store(response)
    return _membership_decision(
        project_id=project_id,
        access_request_id=str(access_request_id),
        command=command,
        auth=auth,
        service=service,
        idempotency_key=idempotency_key,
        target_status=AccessRequestStatus.REVOKED,
    )


@router.post(
    "/projects/{project_id}/membership-requests/{access_request_id}:withdraw",
    operation_id="withdrawMembershipRequest",
    response_model=MembershipRequest,
    responses=PROBLEM_RESPONSES,
)
def withdraw_membership_request(
    project_id: str,
    access_request_id: UUID,
    command: AccessDecisionCommand,
    response: Response,
    auth: VerifiedAuth,
    service: AccessServiceDependency,
    idempotency_key: IdempotencyKey,
) -> MembershipRequest:
    _no_store(response)
    return service.withdraw_membership_request(
        auth=auth,
        project_id=project_id,
        access_request_id=str(access_request_id),
        command=command,
        idempotency_key=idempotency_key,
        request_id=current_request_context().request_id,
    )


@router.post(
    "/projects/{project_id}/capability-requests",
    operation_id="createCapabilityRequest",
    response_model=CapabilityRequest,
    status_code=status.HTTP_201_CREATED,
    responses=PROBLEM_RESPONSES,
)
def create_capability_request(
    project_id: str,
    command: CapabilityRequestCreate,
    response: Response,
    auth: VerifiedAuth,
    service: AccessServiceDependency,
    idempotency_key: IdempotencyKey,
) -> CapabilityRequest:
    _no_store(response)
    return service.create_capability_request(
        auth=auth,
        project_id=project_id,
        command=command,
        idempotency_key=idempotency_key,
        request_id=current_request_context().request_id,
    )


@router.get(
    "/projects/{project_id}/capability-requests",
    operation_id="listCapabilityRequests",
    response_model=CapabilityRequestList,
    responses={
        401: PROBLEM_RESPONSES[401],
        403: PROBLEM_RESPONSES[403],
        422: PROBLEM_RESPONSES[422],
    },
)
def list_capability_requests(
    project_id: str,
    response: Response,
    auth: VerifiedAuth,
    service: AccessServiceDependency,
) -> CapabilityRequestList:
    _no_store(response)
    return service.list_capability_requests(auth=auth, project_id=project_id)


@router.get(
    "/projects/{project_id}/capability-requests/{access_request_id}",
    operation_id="getCapabilityRequest",
    response_model=CapabilityRequest,
    responses={
        401: PROBLEM_RESPONSES[401],
        403: PROBLEM_RESPONSES[403],
        422: PROBLEM_RESPONSES[422],
    },
)
def get_capability_request(
    project_id: str,
    access_request_id: UUID,
    response: Response,
    auth: VerifiedAuth,
    service: AccessServiceDependency,
) -> CapabilityRequest:
    _no_store(response)
    return service.get_capability_request(
        auth=auth,
        project_id=project_id,
        access_request_id=str(access_request_id),
    )


def _capability_decision(
    *,
    project_id: str,
    access_request_id: str,
    command: AccessDecisionCommand,
    auth: VerifiedAuth,
    service: AccessService,
    idempotency_key: str,
    target_status: AccessRequestStatus,
) -> CapabilityRequest:
    return service.decide_capability_request(
        auth=auth,
        project_id=project_id,
        access_request_id=access_request_id,
        target_status=target_status,
        command=command,
        idempotency_key=idempotency_key,
        request_id=current_request_context().request_id,
    )


@router.post(
    "/projects/{project_id}/capability-requests/{access_request_id}:approve",
    operation_id="approveCapabilityRequest",
    response_model=CapabilityRequest,
    responses=PROBLEM_RESPONSES,
)
def approve_capability_request(
    project_id: str,
    access_request_id: UUID,
    command: AccessDecisionCommand,
    response: Response,
    auth: VerifiedAuth,
    service: AccessServiceDependency,
    idempotency_key: IdempotencyKey,
) -> CapabilityRequest:
    _no_store(response)
    return _capability_decision(
        project_id=project_id,
        access_request_id=str(access_request_id),
        command=command,
        auth=auth,
        service=service,
        idempotency_key=idempotency_key,
        target_status=AccessRequestStatus.APPROVED,
    )


@router.post(
    "/projects/{project_id}/capability-requests/{access_request_id}:reject",
    operation_id="rejectCapabilityRequest",
    response_model=CapabilityRequest,
    responses=PROBLEM_RESPONSES,
)
def reject_capability_request(
    project_id: str,
    access_request_id: UUID,
    command: AccessDecisionCommand,
    response: Response,
    auth: VerifiedAuth,
    service: AccessServiceDependency,
    idempotency_key: IdempotencyKey,
) -> CapabilityRequest:
    _no_store(response)
    return _capability_decision(
        project_id=project_id,
        access_request_id=str(access_request_id),
        command=command,
        auth=auth,
        service=service,
        idempotency_key=idempotency_key,
        target_status=AccessRequestStatus.REJECTED,
    )


@router.post(
    "/projects/{project_id}/capability-requests/{access_request_id}:revoke",
    operation_id="revokeCapabilityRequest",
    response_model=CapabilityRequest,
    responses=PROBLEM_RESPONSES,
)
def revoke_capability_request(
    project_id: str,
    access_request_id: UUID,
    command: AccessDecisionCommand,
    response: Response,
    auth: VerifiedAuth,
    service: AccessServiceDependency,
    idempotency_key: IdempotencyKey,
) -> CapabilityRequest:
    _no_store(response)
    return _capability_decision(
        project_id=project_id,
        access_request_id=str(access_request_id),
        command=command,
        auth=auth,
        service=service,
        idempotency_key=idempotency_key,
        target_status=AccessRequestStatus.REVOKED,
    )


@router.post(
    "/projects/{project_id}/capability-requests/{access_request_id}:withdraw",
    operation_id="withdrawCapabilityRequest",
    response_model=CapabilityRequest,
    responses=PROBLEM_RESPONSES,
)
def withdraw_capability_request(
    project_id: str,
    access_request_id: UUID,
    command: AccessDecisionCommand,
    response: Response,
    auth: VerifiedAuth,
    service: AccessServiceDependency,
    idempotency_key: IdempotencyKey,
) -> CapabilityRequest:
    _no_store(response)
    return service.withdraw_capability_request(
        auth=auth,
        project_id=project_id,
        access_request_id=str(access_request_id),
        command=command,
        idempotency_key=idempotency_key,
        request_id=current_request_context().request_id,
    )


@router.get(
    "/projects/{project_id}/access-audit-events",
    operation_id="listAccessAuditEvents",
    response_model=AccessAuditEventList,
    responses={
        401: PROBLEM_RESPONSES[401],
        403: PROBLEM_RESPONSES[403],
        422: PROBLEM_RESPONSES[422],
    },
)
def list_access_audit_events(
    project_id: str,
    response: Response,
    auth: VerifiedAuth,
    service: AccessServiceDependency,
) -> AccessAuditEventList:
    _no_store(response)
    return service.list_audit_events(auth=auth, project_id=project_id)
