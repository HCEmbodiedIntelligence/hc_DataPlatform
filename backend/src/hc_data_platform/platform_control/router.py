"""Read-only platform identity HTTP boundary."""

from __future__ import annotations

from fastapi import APIRouter, Request

from hc_data_platform.platform_control.release_identity import PlatformReleaseIdentityV1

router = APIRouter(prefix="/api/v1/platform", tags=["platform-control"])


@router.get(
    "/version",
    operation_id="getPlatformVersion",
    response_model=PlatformReleaseIdentityV1,
)
def get_platform_version(request: Request) -> PlatformReleaseIdentityV1:
    identity = getattr(request.app.state, "release_identity", None)
    if not isinstance(identity, PlatformReleaseIdentityV1):
        raise RuntimeError("platform release identity is not initialized")
    return identity
