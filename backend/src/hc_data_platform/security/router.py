"""HTTP boundary for direct accounts and project-scoped access requests."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from hc_data_platform.core.config import Settings
from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import problem

from .abuse import client_network_from_request
from .access_models import (
    AccessAuditEventList,
    AccessDecisionCommand,
    AccessRequestStatus,
    AccountNotificationPage,
    AccountNotificationState,
    AccountNotificationUnreadCount,
    AccountProfileUpdate,
    AccountSettings,
    AccountStatus,
    CapabilityRequest,
    CapabilityRequestCreate,
    CapabilityRequestList,
    LoginCommand,
    MembershipRequest,
    MembershipRequestCreate,
    MembershipRequestList,
    PasswordChangeCommand,
    PasswordChangeResult,
    PublicAuthConfiguration,
    RegistrationCommand,
    RegistrationResult,
    SessionBootstrap,
    SessionCreated,
)
from .access_repository import InMemoryAccessRepository
from .access_service import AccessService
from .admin_accounts import (
    AdminAccountService,
    ManagedAccount,
    ManagedAccountCreate,
    ManagedAccountPage,
    ManagedAccountPasswordReset,
    ManagedAccountPasswordResetResult,
    ManagedAccountRoleUpdate,
    ManagedAccountState,
    PlatformAccountRole,
)
from .http import PresentedBearerToken, VerifiedAuth
from .recovery import (
    AccountRecoveryService,
    PasswordRecoveryAccepted,
    PasswordRecoveryCompleted,
    PasswordRecoveryConfirmation,
    PasswordRecoveryRequest,
    RecoveryEmailConfigured,
    RecoveryEmailConfirmation,
    RecoveryEmailVerificationRequest,
)

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
        "description": "An authentication security or session-admission policy denied the request.",
        "headers": {
            "Retry-After": {
                "description": "Bounded decimal delay-seconds before a retry.",
                "schema": {"type": "integer", "minimum": 1, "maximum": 86_400},
            }
        },
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        },
    },
}

ACCOUNT_ADMIN_PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: PROBLEM_RESPONSES[401],
    403: PROBLEM_RESPONSES[403],
    409: PROBLEM_RESPONSES[409],
    412: {
        "description": "The account changed after the administrator read it.",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        },
    },
    422: PROBLEM_RESPONSES[422],
}

ACCOUNT_ADMIN_CREATE_PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    key: ACCOUNT_ADMIN_PROBLEM_RESPONSES[key] for key in (401, 403, 409, 422)
}
MANAGED_ACCOUNT_MUTATION_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Current managed-account representation.",
        "headers": {
            "Cache-Control": {
                "description": "Authentication material must not be cached.",
                "schema": {"type": "string", "const": "private, no-store"},
            }
        },
    },
    **ACCOUNT_ADMIN_PROBLEM_RESPONSES,
}
MANAGED_ACCOUNT_DELETE_RESPONSES: dict[int | str, dict[str, Any]] = {
    204: {
        "description": "Account soft-deleted and active sessions revoked.",
        "headers": {
            "Cache-Control": {
                "description": "Authentication material must not be cached.",
                "schema": {"type": "string", "const": "private, no-store"},
            }
        },
    },
    **ACCOUNT_ADMIN_PROBLEM_RESPONSES,
}

CLIENT_NETWORK_INVALID_RESPONSE: dict[str, Any] = {
    "description": "The client-network attribution is malformed or untrusted.",
    "content": {
        "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
    },
}
AUTH_CHALLENGE_DENIED_RESPONSE: dict[str, Any] = {
    "description": "A required public-authentication challenge is absent or was rejected.",
    "content": {
        "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
    },
}
AUTH_CHALLENGE_UNAVAILABLE_RESPONSE: dict[str, Any] = {
    "description": "A required authentication security provider is temporarily unavailable.",
    "content": {
        "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
    },
}

NO_STORE_RESPONSE_HEADER: dict[str, Any] = {
    "description": "Authentication and authorization material must not be cached.",
    "schema": {"type": "string", "const": "private, no-store"},
}
ACCOUNT_ETAG_RESPONSE_HEADER: dict[str, Any] = {
    "description": "Strong account mutation version.",
    "schema": {"type": "string", "pattern": r'^"v[1-9][0-9]*"$'},
}
NO_STORE_RESPONSE_HEADERS = {"Cache-Control": NO_STORE_RESPONSE_HEADER}
ACCOUNT_RESPONSE_HEADERS = {
    "Cache-Control": NO_STORE_RESPONSE_HEADER,
    "ETag": ACCOUNT_ETAG_RESPONSE_HEADER,
}


def configure_access_service(service: AccessService) -> None:
    global _default_service
    _default_service = service


def get_access_service(request: Request) -> AccessService:
    configured = getattr(request.app.state, "access_service", None)
    return configured if isinstance(configured, AccessService) else _default_service


AccessServiceDependency = Annotated[AccessService, Depends(get_access_service)]


def get_account_recovery_service(request: Request) -> AccountRecoveryService:
    configured = getattr(request.app.state, "account_recovery_service", None)
    if not isinstance(configured, AccountRecoveryService):
        raise RuntimeError("account recovery service is not configured")
    return configured


AccountRecoveryServiceDependency = Annotated[
    AccountRecoveryService,
    Depends(get_account_recovery_service),
]


def get_admin_account_service(request: Request) -> AdminAccountService:
    configured = getattr(request.app.state, "admin_account_service", None)
    if not isinstance(configured, AdminAccountService):
        raise RuntimeError("admin account service is not configured")
    return configured


AdminAccountServiceDependency = Annotated[
    AdminAccountService,
    Depends(get_admin_account_service),
]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=256)]
IfMatch = Annotated[str, Header(alias="If-Match", min_length=3, max_length=128)]
ChallengeResponse = Annotated[
    str | None,
    Header(alias="X-Auth-Challenge-Response", min_length=1, max_length=2_048),
]


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


def _public_client_network(request: Request) -> str | None:
    """Resolve the network only when the distributed policy is enabled."""

    settings = getattr(request.app.state, "settings", None)
    if not isinstance(settings, Settings):
        raise RuntimeError("application settings are not configured")
    if not settings.auth_abuse_enabled:
        return None
    return client_network_from_request(
        peer_host=None if request.client is None else request.client.host,
        forwarded_for=request.headers.get("X-Forwarded-For"),
        mode=settings.auth_client_ip_mode,
        trusted_proxy_cidrs=settings.auth_trusted_proxy_cidrs,
    )


@router.get(
    "/auth/config",
    operation_id="getPublicAuthConfiguration",
    response_model=PublicAuthConfiguration,
    responses={
        200: {
            "description": "Current centrally configured public authentication policy.",
            "headers": NO_STORE_RESPONSE_HEADERS,
        }
    },
)
def get_public_auth_configuration(
    response: Response,
    service: AccessServiceDependency,
) -> PublicAuthConfiguration:
    _no_store(response)
    return service.public_auth_configuration()


@router.post(
    "/auth/password-recovery-requests",
    operation_id="requestPasswordRecovery",
    response_model=PasswordRecoveryAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {
            "description": "Request accepted without disclosing whether the identity exists.",
            "headers": NO_STORE_RESPONSE_HEADERS,
        },
        400: CLIENT_NETWORK_INVALID_RESPONSE,
        403: AUTH_CHALLENGE_DENIED_RESPONSE,
        422: PROBLEM_RESPONSES[422],
        429: PROBLEM_RESPONSES[429],
        503: AUTH_CHALLENGE_UNAVAILABLE_RESPONSE,
    },
)
def request_password_recovery(
    command: PasswordRecoveryRequest,
    request: Request,
    response: Response,
    service: AccountRecoveryServiceDependency,
    challenge_response: ChallengeResponse = None,
) -> PasswordRecoveryAccepted:
    _no_store(response)
    return service.request_password_recovery(
        command,
        source_network=_public_client_network(request),
        challenge_response=challenge_response,
        request_id=current_request_context().request_id,
    )


@router.post(
    "/auth/password-recovery-confirmations",
    operation_id="confirmPasswordRecovery",
    response_model=PasswordRecoveryCompleted,
    responses={
        200: {
            "description": "Password replaced and every existing session revoked.",
            "headers": NO_STORE_RESPONSE_HEADERS,
        },
        422: PROBLEM_RESPONSES[422],
    },
)
def confirm_password_recovery(
    command: PasswordRecoveryConfirmation,
    response: Response,
    service: AccountRecoveryServiceDependency,
) -> PasswordRecoveryCompleted:
    _no_store(response)
    return service.confirm_password_recovery(
        command,
        request_id=current_request_context().request_id,
    )


@router.post(
    "/account/recovery-email-verifications",
    operation_id="requestRecoveryEmailVerification",
    response_model=PasswordRecoveryAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {
            "description": "Verification message accepted for the authenticated account.",
            "headers": NO_STORE_RESPONSE_HEADERS,
        },
        401: PROBLEM_RESPONSES[401],
        409: PROBLEM_RESPONSES[409],
        422: PROBLEM_RESPONSES[422],
        503: AUTH_CHALLENGE_UNAVAILABLE_RESPONSE,
    },
)
def request_recovery_email_verification(
    command: RecoveryEmailVerificationRequest,
    response: Response,
    auth: VerifiedAuth,
    service: AccountRecoveryServiceDependency,
) -> PasswordRecoveryAccepted:
    _no_store(response)
    return service.request_recovery_email_verification(
        command,
        auth=auth,
        request_id=current_request_context().request_id,
    )


@router.post(
    "/account/recovery-email:confirm",
    operation_id="confirmRecoveryEmail",
    response_model=RecoveryEmailConfigured,
    responses={
        200: {
            "description": "Verified recovery email configured.",
            "headers": NO_STORE_RESPONSE_HEADERS,
        },
        401: PROBLEM_RESPONSES[401],
        409: PROBLEM_RESPONSES[409],
        422: PROBLEM_RESPONSES[422],
    },
)
def confirm_recovery_email(
    command: RecoveryEmailConfirmation,
    response: Response,
    auth: VerifiedAuth,
    service: AccountRecoveryServiceDependency,
) -> RecoveryEmailConfigured:
    _no_store(response)
    return service.confirm_recovery_email(
        command,
        auth=auth,
        request_id=current_request_context().request_id,
    )


@router.post(
    "/auth/registrations",
    operation_id="registerAccount",
    response_model=RegistrationResult,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {
            "description": "The ACTIVE empty account was created.",
            "headers": NO_STORE_RESPONSE_HEADERS,
        },
        400: CLIENT_NETWORK_INVALID_RESPONSE,
        403: AUTH_CHALLENGE_DENIED_RESPONSE,
        409: PROBLEM_RESPONSES[409],
        422: PROBLEM_RESPONSES[422],
        429: PROBLEM_RESPONSES[429],
        503: AUTH_CHALLENGE_UNAVAILABLE_RESPONSE,
    },
)
def register_account(
    command: RegistrationCommand,
    request: Request,
    response: Response,
    service: AccessServiceDependency,
    challenge_response: ChallengeResponse = None,
) -> RegistrationResult:
    _no_store(response)
    return service.register(
        command,
        request_id=current_request_context().request_id,
        source_network=_public_client_network(request),
        challenge_response=challenge_response,
    )


@router.post(
    "/auth/sessions",
    operation_id="createSession",
    response_model=SessionCreated,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {
            "description": "Opaque Bearer session created.",
            "headers": NO_STORE_RESPONSE_HEADERS,
        },
        400: CLIENT_NETWORK_INVALID_RESPONSE,
        403: AUTH_CHALLENGE_DENIED_RESPONSE,
        401: PROBLEM_RESPONSES[401],
        422: PROBLEM_RESPONSES[422],
        429: PROBLEM_RESPONSES[429],
        503: AUTH_CHALLENGE_UNAVAILABLE_RESPONSE,
    },
)
def create_session(
    command: LoginCommand,
    request: Request,
    response: Response,
    service: AccessServiceDependency,
    challenge_response: ChallengeResponse = None,
) -> SessionCreated:
    _no_store(response)
    return service.login(
        command,
        request_id=current_request_context().request_id,
        source_network=_public_client_network(request),
        challenge_response=challenge_response,
    )


@router.get(
    "/auth/session/bootstrap",
    operation_id="getSessionBootstrap",
    response_model=SessionBootstrap,
    responses={
        200: {
            "description": "Current principal and effective access.",
            "headers": NO_STORE_RESPONSE_HEADERS,
        },
        401: PROBLEM_RESPONSES[401],
    },
)
def bootstrap_session(
    request: Request,
    response: Response,
    _: VerifiedAuth,
    service: AccessServiceDependency,
) -> SessionBootstrap:
    _no_store(response)
    return service.bootstrap(
        _bearer_token(request),
        request_id=current_request_context().request_id,
    )


@router.post(
    "/auth/session:logout",
    operation_id="logoutSession",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {
            "description": "Session is revoked or was already revoked.",
            "headers": NO_STORE_RESPONSE_HEADERS,
        },
        401: PROBLEM_RESPONSES[401],
    },
)
def logout_session(
    token: PresentedBearerToken,
    response: Response,
    service: AccessServiceDependency,
) -> None:
    _no_store(response)
    service.logout(
        token,
        request_id=current_request_context().request_id,
    )


@router.get(
    "/account/profile",
    operation_id="getOwnAccountProfile",
    response_model=AccountSettings,
    responses={
        200: {
            "description": "Current account settings.",
            "headers": ACCOUNT_RESPONSE_HEADERS,
        },
        401: PROBLEM_RESPONSES[401],
    },
)
def get_own_account_profile(
    request: Request,
    response: Response,
    _: VerifiedAuth,
    service: AccessServiceDependency,
) -> AccountSettings:
    _no_store(response)
    result = service.get_account_settings(
        _bearer_token(request),
        request_id=current_request_context().request_id,
    )
    response.headers["ETag"] = result.profile.etag
    return result


@router.patch(
    "/account/profile",
    operation_id="updateOwnAccountProfile",
    response_model=AccountSettings,
    responses={
        200: {
            "description": "Updated account settings.",
            "headers": ACCOUNT_RESPONSE_HEADERS,
        },
        401: PROBLEM_RESPONSES[401],
        409: PROBLEM_RESPONSES[409],
        412: {
            "description": "The supplied account version is stale.",
            "content": {
                "application/problem+json": {
                    "schema": {"$ref": "#/components/schemas/ProblemDetails"}
                }
            },
        },
        422: PROBLEM_RESPONSES[422],
    },
)
def update_own_account_profile(
    request: Request,
    command: AccountProfileUpdate,
    response: Response,
    _: VerifiedAuth,
    service: AccessServiceDependency,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
) -> AccountSettings:
    _no_store(response)
    result = service.update_account_profile(
        _bearer_token(request),
        command,
        if_match=if_match,
        idempotency_key=idempotency_key,
        request_id=current_request_context().request_id,
    )
    response.headers["ETag"] = result.profile.etag
    return result


@router.post(
    "/account/password:change",
    operation_id="changeOwnAccountPassword",
    response_model=PasswordChangeResult,
    responses={
        200: {
            "description": "Password changed and other sessions revoked.",
            "headers": ACCOUNT_RESPONSE_HEADERS,
        },
        401: PROBLEM_RESPONSES[401],
        409: PROBLEM_RESPONSES[409],
        412: {
            "description": "The supplied account version is stale.",
            "content": {
                "application/problem+json": {
                    "schema": {"$ref": "#/components/schemas/ProblemDetails"}
                }
            },
        },
        422: PROBLEM_RESPONSES[422],
        429: PROBLEM_RESPONSES[429],
    },
)
def change_own_account_password(
    request: Request,
    command: PasswordChangeCommand,
    response: Response,
    _: VerifiedAuth,
    service: AccessServiceDependency,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
) -> PasswordChangeResult:
    _no_store(response)
    result = service.change_password(
        _bearer_token(request),
        command,
        if_match=if_match,
        idempotency_key=idempotency_key,
        request_id=current_request_context().request_id,
    )
    response.headers["ETag"] = result.account.profile.etag
    return result


@router.get(
    "/account/notifications/unread-count",
    operation_id="getAccountNotificationUnreadCount",
    response_model=AccountNotificationUnreadCount,
    responses={
        200: {
            "description": "Unread notifications for the verified account only.",
            "headers": NO_STORE_RESPONSE_HEADERS,
        },
        401: PROBLEM_RESPONSES[401],
    },
)
def get_account_notification_unread_count(
    response: Response,
    auth: VerifiedAuth,
    service: AccessServiceDependency,
) -> AccountNotificationUnreadCount:
    _no_store(response)
    return service.notification_unread_count(auth=auth)


@router.get(
    "/account/notifications",
    operation_id="listAccountNotifications",
    response_model=AccountNotificationPage,
    responses={
        200: {
            "description": "Recipient-scoped notification page, newest first.",
            "headers": NO_STORE_RESPONSE_HEADERS,
        },
        400: PROBLEM_RESPONSES[422],
        401: PROBLEM_RESPONSES[401],
        422: PROBLEM_RESPONSES[422],
    },
)
def list_account_notifications(
    response: Response,
    auth: VerifiedAuth,
    service: AccessServiceDependency,
    state: Annotated[AccountNotificationState | None, Query()] = None,
    cursor: Annotated[str | None, Query(min_length=16, max_length=16_384)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> AccountNotificationPage:
    _no_store(response)
    return service.notifications(auth=auth, state=state, cursor=cursor, limit=limit)


@router.post(
    "/account/notifications/{notification_id}:read",
    operation_id="markAccountNotificationRead",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {
            "description": "Notification is marked read, or was already read.",
            "headers": NO_STORE_RESPONSE_HEADERS,
        },
        401: PROBLEM_RESPONSES[401],
        404: {
            "description": "The notification is absent or belongs to another account.",
            "content": {
                "application/problem+json": {
                    "schema": {"$ref": "#/components/schemas/ProblemDetails"}
                }
            },
        },
    },
)
def mark_account_notification_read(
    notification_id: UUID,
    response: Response,
    auth: VerifiedAuth,
    service: AccessServiceDependency,
) -> None:
    _no_store(response)
    service.mark_notification_read(
        auth=auth,
        notification_id=str(notification_id),
        request_id=current_request_context().request_id,
    )


@router.get(
    "/platform/accounts",
    operation_id="listPlatformAccounts",
    response_model=ManagedAccountPage,
    responses={
        401: PROBLEM_RESPONSES[401],
        403: PROBLEM_RESPONSES[403],
        422: PROBLEM_RESPONSES[422],
    },
)
def list_platform_accounts(
    response: Response,
    auth: VerifiedAuth,
    service: AdminAccountServiceDependency,
    query: Annotated[str | None, Query(max_length=128)] = None,
    state: ManagedAccountState | None = None,
    role: PlatformAccountRole | None = None,
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=10, le=100)] = 20,
) -> ManagedAccountPage:
    _no_store(response)
    return service.list_accounts(
        auth=auth,
        query=query,
        state=state,
        role=role,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/platform/accounts",
    operation_id="createPlatformAccount",
    response_model=ManagedAccount,
    status_code=status.HTTP_201_CREATED,
    responses=ACCOUNT_ADMIN_CREATE_PROBLEM_RESPONSES,
)
def create_platform_account(
    command: ManagedAccountCreate,
    response: Response,
    auth: VerifiedAuth,
    service: AdminAccountServiceDependency,
) -> ManagedAccount:
    _no_store(response)
    return service.create_account(
        auth=auth,
        command=command,
        request_id=current_request_context().request_id,
    )


@router.post(
    "/platform/accounts/{principal_id}:disable",
    operation_id="disablePlatformAccount",
    response_model=ManagedAccount,
    responses=MANAGED_ACCOUNT_MUTATION_RESPONSES,
)
def disable_platform_account(
    principal_id: UUID,
    response: Response,
    auth: VerifiedAuth,
    service: AdminAccountServiceDependency,
    if_match: IfMatch,
) -> ManagedAccount:
    _no_store(response)
    return service.set_status(
        auth=auth,
        principal_id=str(principal_id),
        target_status=AccountStatus.DISABLED,
        if_match=if_match,
        request_id=current_request_context().request_id,
    )


@router.post(
    "/platform/accounts/{principal_id}:enable",
    operation_id="enablePlatformAccount",
    response_model=ManagedAccount,
    responses=MANAGED_ACCOUNT_MUTATION_RESPONSES,
)
def enable_platform_account(
    principal_id: UUID,
    response: Response,
    auth: VerifiedAuth,
    service: AdminAccountServiceDependency,
    if_match: IfMatch,
) -> ManagedAccount:
    _no_store(response)
    return service.set_status(
        auth=auth,
        principal_id=str(principal_id),
        target_status=AccountStatus.ACTIVE,
        if_match=if_match,
        request_id=current_request_context().request_id,
    )


@router.post(
    "/platform/accounts/{principal_id}:role",
    operation_id="setPlatformAccountRole",
    response_model=ManagedAccount,
    responses=MANAGED_ACCOUNT_MUTATION_RESPONSES,
)
def set_platform_account_role(
    principal_id: UUID,
    command: ManagedAccountRoleUpdate,
    response: Response,
    auth: VerifiedAuth,
    service: AdminAccountServiceDependency,
    if_match: IfMatch,
) -> ManagedAccount:
    _no_store(response)
    return service.set_role(
        auth=auth,
        principal_id=str(principal_id),
        command=command,
        if_match=if_match,
        request_id=current_request_context().request_id,
    )


@router.post(
    "/platform/accounts/{principal_id}:reset-password",
    operation_id="resetPlatformAccountPassword",
    response_model=ManagedAccountPasswordResetResult,
    responses=MANAGED_ACCOUNT_MUTATION_RESPONSES,
)
def reset_platform_account_password(
    principal_id: UUID,
    command: ManagedAccountPasswordReset,
    response: Response,
    auth: VerifiedAuth,
    service: AdminAccountServiceDependency,
    if_match: IfMatch,
) -> ManagedAccountPasswordResetResult:
    _no_store(response)
    return service.reset_password(
        auth=auth,
        principal_id=str(principal_id),
        command=command,
        if_match=if_match,
        request_id=current_request_context().request_id,
    )


@router.delete(
    "/platform/accounts/{principal_id}",
    operation_id="deletePlatformAccount",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=MANAGED_ACCOUNT_DELETE_RESPONSES,
)
def delete_platform_account(
    principal_id: UUID,
    response: Response,
    auth: VerifiedAuth,
    service: AdminAccountServiceDependency,
    if_match: IfMatch,
) -> None:
    _no_store(response)
    service.delete_account(
        auth=auth,
        principal_id=str(principal_id),
        if_match=if_match,
        request_id=current_request_context().request_id,
    )


@router.post(
    "/platform/accounts/{principal_id}:unlock",
    operation_id="unlockPlatformAccount",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {
            "description": (
                "The active temporary lock was cleared, or the account was already unlocked."
            ),
            "headers": NO_STORE_RESPONSE_HEADERS,
        },
        401: PROBLEM_RESPONSES[401],
        403: PROBLEM_RESPONSES[403],
        422: PROBLEM_RESPONSES[422],
    },
)
def unlock_platform_account(
    principal_id: UUID,
    response: Response,
    auth: VerifiedAuth,
    service: AccessServiceDependency,
) -> None:
    _no_store(response)
    service.unlock_account(
        auth=auth,
        principal_id=str(principal_id),
        request_id=current_request_context().request_id,
    )


@router.post(
    "/organizations/{organization_id}/projects/{project_id}/membership-requests",
    operation_id="createMembershipRequest",
    response_model=MembershipRequest,
    status_code=status.HTTP_201_CREATED,
    responses=PROBLEM_RESPONSES,
)
def create_membership_request(
    organization_id: str,
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
        organization_id=organization_id,
        project_id=project_id,
        command=command,
        idempotency_key=idempotency_key,
        request_id=current_request_context().request_id,
    )


@router.get(
    "/organizations/{organization_id}/projects/{project_id}/membership-requests",
    operation_id="listMembershipRequests",
    response_model=MembershipRequestList,
    responses={
        401: PROBLEM_RESPONSES[401],
        403: PROBLEM_RESPONSES[403],
        422: PROBLEM_RESPONSES[422],
    },
)
def list_membership_requests(
    organization_id: str,
    project_id: str,
    response: Response,
    auth: VerifiedAuth,
    service: AccessServiceDependency,
) -> MembershipRequestList:
    _no_store(response)
    return service.list_membership_requests(
        auth=auth, organization_id=organization_id, project_id=project_id
    )


@router.get(
    "/organizations/{organization_id}/projects/{project_id}/membership-requests/{access_request_id}",
    operation_id="getMembershipRequest",
    response_model=MembershipRequest,
    responses={
        401: PROBLEM_RESPONSES[401],
        403: PROBLEM_RESPONSES[403],
        422: PROBLEM_RESPONSES[422],
    },
)
def get_membership_request(
    organization_id: str,
    project_id: str,
    access_request_id: UUID,
    response: Response,
    auth: VerifiedAuth,
    service: AccessServiceDependency,
) -> MembershipRequest:
    _no_store(response)
    return service.get_membership_request(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        access_request_id=str(access_request_id),
    )


def _membership_decision(
    *,
    organization_id: str,
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
        organization_id=organization_id,
        project_id=project_id,
        access_request_id=access_request_id,
        target_status=target_status,
        command=command,
        idempotency_key=idempotency_key,
        request_id=current_request_context().request_id,
    )


@router.post(
    "/organizations/{organization_id}/projects/{project_id}/membership-requests/{access_request_id}:approve",
    operation_id="approveMembershipRequest",
    response_model=MembershipRequest,
    responses=PROBLEM_RESPONSES,
)
def approve_membership_request(
    organization_id: str,
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
        organization_id=organization_id,
        project_id=project_id,
        access_request_id=str(access_request_id),
        command=command,
        auth=auth,
        service=service,
        idempotency_key=idempotency_key,
        target_status=AccessRequestStatus.APPROVED,
    )


@router.post(
    "/organizations/{organization_id}/projects/{project_id}/membership-requests/{access_request_id}:reject",
    operation_id="rejectMembershipRequest",
    response_model=MembershipRequest,
    responses=PROBLEM_RESPONSES,
)
def reject_membership_request(
    organization_id: str,
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
        organization_id=organization_id,
        project_id=project_id,
        access_request_id=str(access_request_id),
        command=command,
        auth=auth,
        service=service,
        idempotency_key=idempotency_key,
        target_status=AccessRequestStatus.REJECTED,
    )


@router.post(
    "/organizations/{organization_id}/projects/{project_id}/membership-requests/{access_request_id}:revoke",
    operation_id="revokeMembershipRequest",
    response_model=MembershipRequest,
    responses=PROBLEM_RESPONSES,
)
def revoke_membership_request(
    organization_id: str,
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
        organization_id=organization_id,
        project_id=project_id,
        access_request_id=str(access_request_id),
        command=command,
        auth=auth,
        service=service,
        idempotency_key=idempotency_key,
        target_status=AccessRequestStatus.REVOKED,
    )


@router.post(
    "/organizations/{organization_id}/projects/{project_id}/membership-requests/{access_request_id}:withdraw",
    operation_id="withdrawMembershipRequest",
    response_model=MembershipRequest,
    responses=PROBLEM_RESPONSES,
)
def withdraw_membership_request(
    organization_id: str,
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
        organization_id=organization_id,
        project_id=project_id,
        access_request_id=str(access_request_id),
        command=command,
        idempotency_key=idempotency_key,
        request_id=current_request_context().request_id,
    )


@router.post(
    "/organizations/{organization_id}/projects/{project_id}/capability-requests",
    operation_id="createCapabilityRequest",
    response_model=CapabilityRequest,
    status_code=status.HTTP_201_CREATED,
    responses=PROBLEM_RESPONSES,
)
def create_capability_request(
    organization_id: str,
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
        organization_id=organization_id,
        project_id=project_id,
        command=command,
        idempotency_key=idempotency_key,
        request_id=current_request_context().request_id,
    )


@router.get(
    "/organizations/{organization_id}/projects/{project_id}/capability-requests",
    operation_id="listCapabilityRequests",
    response_model=CapabilityRequestList,
    responses={
        401: PROBLEM_RESPONSES[401],
        403: PROBLEM_RESPONSES[403],
        422: PROBLEM_RESPONSES[422],
    },
)
def list_capability_requests(
    organization_id: str,
    project_id: str,
    response: Response,
    auth: VerifiedAuth,
    service: AccessServiceDependency,
) -> CapabilityRequestList:
    _no_store(response)
    return service.list_capability_requests(
        auth=auth, organization_id=organization_id, project_id=project_id
    )


@router.get(
    "/organizations/{organization_id}/projects/{project_id}/capability-requests/{access_request_id}",
    operation_id="getCapabilityRequest",
    response_model=CapabilityRequest,
    responses={
        401: PROBLEM_RESPONSES[401],
        403: PROBLEM_RESPONSES[403],
        422: PROBLEM_RESPONSES[422],
    },
)
def get_capability_request(
    organization_id: str,
    project_id: str,
    access_request_id: UUID,
    response: Response,
    auth: VerifiedAuth,
    service: AccessServiceDependency,
) -> CapabilityRequest:
    _no_store(response)
    return service.get_capability_request(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        access_request_id=str(access_request_id),
    )


def _capability_decision(
    *,
    organization_id: str,
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
        organization_id=organization_id,
        project_id=project_id,
        access_request_id=access_request_id,
        target_status=target_status,
        command=command,
        idempotency_key=idempotency_key,
        request_id=current_request_context().request_id,
    )


@router.post(
    "/organizations/{organization_id}/projects/{project_id}/capability-requests/{access_request_id}:approve",
    operation_id="approveCapabilityRequest",
    response_model=CapabilityRequest,
    responses=PROBLEM_RESPONSES,
)
def approve_capability_request(
    organization_id: str,
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
        organization_id=organization_id,
        project_id=project_id,
        access_request_id=str(access_request_id),
        command=command,
        auth=auth,
        service=service,
        idempotency_key=idempotency_key,
        target_status=AccessRequestStatus.APPROVED,
    )


@router.post(
    "/organizations/{organization_id}/projects/{project_id}/capability-requests/{access_request_id}:reject",
    operation_id="rejectCapabilityRequest",
    response_model=CapabilityRequest,
    responses=PROBLEM_RESPONSES,
)
def reject_capability_request(
    organization_id: str,
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
        organization_id=organization_id,
        project_id=project_id,
        access_request_id=str(access_request_id),
        command=command,
        auth=auth,
        service=service,
        idempotency_key=idempotency_key,
        target_status=AccessRequestStatus.REJECTED,
    )


@router.post(
    "/organizations/{organization_id}/projects/{project_id}/capability-requests/{access_request_id}:revoke",
    operation_id="revokeCapabilityRequest",
    response_model=CapabilityRequest,
    responses=PROBLEM_RESPONSES,
)
def revoke_capability_request(
    organization_id: str,
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
        organization_id=organization_id,
        project_id=project_id,
        access_request_id=str(access_request_id),
        command=command,
        auth=auth,
        service=service,
        idempotency_key=idempotency_key,
        target_status=AccessRequestStatus.REVOKED,
    )


@router.post(
    "/organizations/{organization_id}/projects/{project_id}/capability-requests/{access_request_id}:withdraw",
    operation_id="withdrawCapabilityRequest",
    response_model=CapabilityRequest,
    responses=PROBLEM_RESPONSES,
)
def withdraw_capability_request(
    organization_id: str,
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
        organization_id=organization_id,
        project_id=project_id,
        access_request_id=str(access_request_id),
        command=command,
        idempotency_key=idempotency_key,
        request_id=current_request_context().request_id,
    )


@router.get(
    "/organizations/{organization_id}/projects/{project_id}/access-audit-events",
    operation_id="listAccessAuditEvents",
    response_model=AccessAuditEventList,
    responses={
        401: PROBLEM_RESPONSES[401],
        403: PROBLEM_RESPONSES[403],
        422: PROBLEM_RESPONSES[422],
    },
)
def list_access_audit_events(
    organization_id: str,
    project_id: str,
    response: Response,
    auth: VerifiedAuth,
    service: AccessServiceDependency,
) -> AccessAuditEventList:
    _no_store(response)
    return service.list_audit_events(
        auth=auth, organization_id=organization_id, project_id=project_id
    )
