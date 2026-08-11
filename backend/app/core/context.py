from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Annotated, Any

import jwt
from fastapi import Depends, Request

from app.core.catalog import CAPABILITIES
from app.core.errors import ForbiddenError, UnauthenticatedError, ValidationError


@dataclass(frozen=True, slots=True)
class RequestContext:
    request_id: str
    actor_id: str
    organization_id: str
    project_id: str
    region_code: str
    capabilities: frozenset[str]


def _claim_str(payload: dict[str, Any], name: str) -> str | None:
    value = payload.get(name)
    return value if isinstance(value, str) and value else None


def _decode_token(token: str) -> dict[str, Any]:
    environment = os.getenv("APP_ENV", "development").lower()
    if token.startswith("test:") and environment in {"test", "development", "local"}:
        actor, _, raw_caps = token[5:].partition(":")
        if not actor:
            raise UnauthenticatedError()
        return {"sub": actor, "capabilities": [x for x in raw_caps.split(",") if x]}

    key = os.getenv("JWT_PUBLIC_KEY") or os.getenv("JWT_SECRET")
    if not key:
        raise UnauthenticatedError(code="AUTH_CONFIGURATION_UNAVAILABLE")
    algorithm = os.getenv("JWT_ALGORITHM", "RS256")
    if algorithm not in {"RS256", "ES256", "HS256"}:
        raise UnauthenticatedError(code="AUTH_ALGORITHM_REJECTED")
    kwargs: dict[str, Any] = {
        "algorithms": [algorithm],
        "options": {"require": ["exp", "iat", "sub"]},
    }
    if audience := os.getenv("JWT_AUDIENCE"):
        kwargs["audience"] = audience
    if issuer := os.getenv("JWT_ISSUER"):
        kwargs["issuer"] = issuer
    try:
        result = jwt.decode(token, key, **kwargs)
    except jwt.PyJWTError as exc:
        raise UnauthenticatedError() from exc
    if not isinstance(result, dict):
        raise UnauthenticatedError()
    return result


async def get_context(request: Request) -> RequestContext:
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise UnauthenticatedError()
    payload = _decode_token(token)
    actor_id = _claim_str(payload, "sub") or _claim_str(payload, "actor_id")
    if not actor_id:
        raise UnauthenticatedError()

    organization_id = request.headers.get("X-Organization-Id")
    project_id = request.headers.get("X-Project-Id") or request.path_params.get(
        "projectId", request.path_params.get("project_id")
    )
    region_code = request.headers.get("X-Region-Code") or request.path_params.get(
        "regionCode", request.path_params.get("region_code")
    )
    missing = [
        name
        for name, value in (
            ("X-Organization-Id", organization_id),
            ("X-Project-Id/projectId", project_id),
            ("X-Region-Code/regionCode", region_code),
        )
        if not value
    ]
    if missing:
        raise ValidationError(
            code="MISSING_SCOPE",
            field_errors=[
                {"path": f"/{name}", "code": "REQUIRED", "message": "Scope is required."}
                for name in missing
            ],
        )

    for claim, value in (
        ("organization_id", organization_id),
        ("project_id", project_id),
        ("region_code", region_code),
    ):
        claimed = _claim_str(payload, claim)
        if claimed is not None and claimed != value:
            raise ForbiddenError(code="SCOPE_MISMATCH")

    raw_caps = payload.get("capabilities", payload.get("scope", []))
    if isinstance(raw_caps, str):
        candidates = raw_caps.replace(",", " ").split()
    elif isinstance(raw_caps, list):
        candidates = [item for item in raw_caps if isinstance(item, str)]
    else:
        candidates = []
    capabilities = frozenset(cap for cap in candidates if cap in CAPABILITIES)
    return RequestContext(
        request_id=getattr(request.state, "request_id", "request-unknown"),
        actor_id=actor_id,
        organization_id=str(organization_id),
        project_id=str(project_id),
        region_code=str(region_code),
        capabilities=capabilities,
    )


def require(*caps: str):
    """FastAPI dependency factory. Unknown and absent capabilities both deny."""

    async def dependency(
        ctx: Annotated[RequestContext, Depends(get_context)],
    ) -> RequestContext:
        unknown = [cap for cap in caps if cap not in CAPABILITIES]
        missing = [cap for cap in caps if cap not in ctx.capabilities]
        if unknown or missing:
            raise ForbiddenError(
                code="CAPABILITY_REQUIRED",
                blocked_reasons=[
                    {"code": "CAPABILITY_REQUIRED", "message": "Required capability is absent."}
                ],
            )
        return ctx

    return dependency
