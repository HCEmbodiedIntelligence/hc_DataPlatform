"""Shared FastAPI dependencies for verified tenant-scoped requests."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from hc_data_platform.core.context import select_request_scope
from hc_data_platform.core.errors import problem

from .auth import AuthContext
from .scope import ScopeGuard

_bearer_scheme = HTTPBearer(auto_error=False, scheme_name="bearerAuth")


def require_presented_bearer_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> str:
    """Require a Bearer credential without requiring the session to remain active.

    Session revocation must be safely repeatable.  The request middleware still verifies an
    active session on the first call, while the exact logout route accepts the same now-revoked
    opaque credential on retries without revealing whether it existed.
    """

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise problem(
            status=401,
            code="AUTHENTICATION_REQUIRED",
            title="Authentication required",
            detail="A Bearer session token is required.",
        )
    token = credentials.credentials.strip()
    if not token:
        raise problem(
            status=401,
            code="AUTHENTICATION_REQUIRED",
            title="Authentication required",
            detail="A Bearer session token is required.",
        )
    return token


PresentedBearerToken = Annotated[str, Depends(require_presented_bearer_token)]


def require_auth_context(
    request: Request,
    _: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> AuthContext:
    auth = getattr(request.state, "auth_context", None)
    if not isinstance(auth, AuthContext):
        raise problem(
            status=401,
            code="AUTHENTICATION_REQUIRED",
            title="Authentication required",
            detail="A verified Bearer access token is required.",
        )
    return auth


VerifiedAuth = Annotated[AuthContext, Depends(require_auth_context)]


def authorize_scope(
    auth: AuthContext,
    project_id: str,
    capability: str,
    region_code: str | None = None,
    organization_id: str | None = None,
) -> None:
    """Authorize and select the exact tenant scope used by downstream RLS adapters."""

    ScopeGuard.require(auth, project_id, region_code, organization_id)
    auth.require_capability(capability, project_id, organization_id)
    select_request_scope(project_id, region_code, organization_id=organization_id)
