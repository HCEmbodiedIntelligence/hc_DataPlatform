"""Shared FastAPI dependencies for verified tenant-scoped requests."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from hc_data_platform.core.context import select_request_scope
from hc_data_platform.core.errors import problem

from .auth import AuthContext, Permission
from .scope import ScopeGuard


def require_auth_context(request: Request) -> AuthContext:
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
    permission: Permission,
    region_code: str | None = None,
) -> None:
    """Authorize and select the exact tenant scope used by downstream RLS adapters."""

    auth.require_permission(permission)
    ScopeGuard.require(auth, project_id, region_code)
    select_request_scope(project_id, region_code)


def authorize_read(auth: AuthContext, project_id: str, region_code: str | None = None) -> None:
    authorize_scope(auth, project_id, Permission.READ, region_code)
