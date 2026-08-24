from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Query, Request
from fastapi import Path as ApiPath
from fastapi.responses import FileResponse, Response

from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import ProblemDetails, problem
from hc_data_platform.security.http import VerifiedAuth, authorize_read

from .audit import (
    InMemoryPreviewAuditRecorder,
    PreviewAuditRecorder,
    PreviewDescriptorAuditEvent,
)
from .memory import (
    HmacUrlSigner,
    InMemoryExclusionReader,
    InMemoryMediaEncoder,
    InMemoryPreviewCache,
    InMemoryPreviewMediaReader,
    InMemoryStepReader,
)
from .models import PreviewDescriptorV1, PreviewRequestV1
from .service import PreviewService

router = APIRouter(prefix="/api/v1/previews", tags=["preview"])

_service = PreviewService(
    step_reader=InMemoryStepReader(),
    exclusions=InMemoryExclusionReader(),
    encoder=InMemoryMediaEncoder(),
    cache=InMemoryPreviewCache(),
    signer=HmacUrlSigner(),
    media_reader=InMemoryPreviewMediaReader(),
)
_audit_recorder: PreviewAuditRecorder = InMemoryPreviewAuditRecorder()

_PLAYLIST_ASSET = "index.m3u8"
_PLAYLIST_MEMBER = re.compile(r"^(?:init\.mp4|segment_[0-9]{5}\.m4s)$")
_MAP_URI = re.compile(r'URI="(?P<asset>[^"]+)"')


def configure_preview_service(service: PreviewService) -> None:
    """Application composition hook for real Lance, FFmpeg, cache and signer adapters."""

    global _service
    _service = service


def configure_preview_audit_recorder(recorder: PreviewAuditRecorder) -> None:
    """Install the production audit ledger used after authenticated authorization."""

    global _audit_recorder
    _audit_recorder = recorder


@router.post("/sessions", response_model=PreviewDescriptorV1, status_code=201)
def create_preview_session(
    request: PreviewRequestV1,
    auth: VerifiedAuth,
    organization_id: Annotated[str | None, Header(alias="X-Organization-Id")] = None,
    region_code: Annotated[str | None, Header(alias="X-Region-Code")] = None,
) -> PreviewDescriptorV1:
    authorize_read(auth, request.project_id, region_code, organization_id)
    descriptor = _service.create(request)
    _record_descriptor_issue(descriptor, actor_id=auth.subject_id, operation="CREATED")
    return descriptor


@router.get("/sessions/{session_id}", response_model=PreviewDescriptorV1)
def get_preview_session(
    session_id: str,
    auth: VerifiedAuth,
    organization_id: Annotated[str | None, Header(alias="X-Organization-Id")] = None,
    region_code: Annotated[str | None, Header(alias="X-Region-Code")] = None,
) -> PreviewDescriptorV1:
    descriptor = _service.get(session_id)
    authorize_read(auth, descriptor.project_id, region_code, organization_id)
    _record_descriptor_issue(descriptor, actor_id=auth.subject_id, operation="REFRESHED")
    return descriptor


def _record_descriptor_issue(
    descriptor: PreviewDescriptorV1,
    *,
    actor_id: str,
    operation: Literal["CREATED", "REFRESHED"],
) -> None:
    """Record the bearer-authorized grant before returning its HLS capability URL."""

    context = current_request_context()
    _audit_recorder.append_descriptor_issue(
        PreviewDescriptorAuditEvent(
            project_id=descriptor.project_id,
            region_code=context.region_code,
            actor_id=actor_id,
            request_id=context.request_id,
            session_id=descriptor.session_id,
            dataset_id=descriptor.dataset_id,
            rollout_id=descriptor.rollout_id,
            camera_id=descriptor.camera_id,
            view_mode=descriptor.view_mode.value,
            operation=operation,
            grant_expires_at=descriptor.signed_url_expires_at,
        )
    )


@router.get(
    "/sessions/{session_id}/media/{asset_name}",
    response_class=Response,
    responses={
        200: {
            "description": "Signed preview playlist or complete CMAF asset",
            "headers": {"Cache-Control": {"schema": {"type": "string"}}},
            "content": {
                "application/vnd.apple.mpegurl": {},
                "video/mp4": {},
                "video/iso.segment": {},
            },
        },
        206: {
            "description": "Requested byte range of a CMAF asset",
            "headers": {
                "Accept-Ranges": {"schema": {"type": "string", "const": "bytes"}},
                "Content-Range": {"schema": {"type": "string"}},
                "Cache-Control": {"schema": {"type": "string"}},
            },
        },
        403: {"model": ProblemDetails, "description": "Invalid or expired media capability"},
        404: {"model": ProblemDetails, "description": "Preview session or asset not found"},
        416: {"model": ProblemDetails, "description": "Requested range is not satisfiable"},
    },
)
def get_preview_media(
    session_id: str,
    request: Request,
    asset_name: str = ApiPath(
        pattern=r"^(?:index\.m3u8|init\.mp4|segment_[0-9]{5}\.m4s)$",
    ),
    expires: int = Query(gt=0),
    sig: str = Query(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
) -> Response:
    """Serve only a session-bound, short-lived HLS asset capability.

    HLS element requests cannot add a bearer header.  Authentication happens when
    the session descriptor is issued; every playlist asset is then individually
    bound to that session and expiry by the HMAC grant.
    """

    record, media_path = _service.resolve_media(
        session_id=session_id,
        asset_name=asset_name,
        expires=expires,
        signature=sig,
    )
    if asset_name == _PLAYLIST_ASSET:
        return Response(
            content=_rewrite_playlist(
                media_path,
                record=record,
                expires=expires,
            ),
            media_type="application/vnd.apple.mpegurl",
            headers=_media_headers(),
        )
    _validate_single_range(request, media_path)
    return FileResponse(
        media_path,
        media_type=_media_type(media_path),
        headers=_media_headers(),
    )


def _rewrite_playlist(media_path: Path, *, record: object, expires: int) -> str:
    """Replace local HLS references with individually signed API capability URLs."""

    from .models import PreviewCacheRecordV1

    if not isinstance(record, PreviewCacheRecordV1):
        raise RuntimeError("preview service returned an invalid media record")
    try:
        lines = media_path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        raise problem(
            status=404,
            code="PREVIEW_MEDIA_NOT_FOUND",
            title="Preview media not found",
            detail="The requested preview asset is not available.",
        ) from None

    rewritten: list[str] = []
    for line in lines:
        if line.startswith("#EXT-X-MAP:"):
            match = _MAP_URI.search(line)
            if match is None or _PLAYLIST_MEMBER.fullmatch(match.group("asset")) is None:
                raise _invalid_playlist()
            asset = match.group("asset")
            rewritten.append(
                _MAP_URI.sub(
                    f'URI="{_service.sign_media_asset(record, asset_name=asset, expires=expires)}"',
                    line,
                    count=1,
                )
            )
        elif line and not line.startswith("#"):
            if _PLAYLIST_MEMBER.fullmatch(line) is None:
                raise _invalid_playlist()
            rewritten.append(_service.sign_media_asset(record, asset_name=line, expires=expires))
        else:
            rewritten.append(line)
    return "\n".join(rewritten) + "\n"


def _invalid_playlist() -> Exception:
    return problem(
        status=404,
        code="PREVIEW_MEDIA_NOT_FOUND",
        title="Preview media not found",
        detail="The requested preview asset is not available.",
    )


def _media_type(path: Path) -> str:
    if path.name == "init.mp4":
        return "video/mp4"
    return "video/iso.segment"


def _validate_single_range(request: Request, media_path: Path) -> None:
    """Reject malformed or multi-range requests with the platform problem contract.

    Starlette's efficient ``FileResponse`` handles the actual byte streaming.  Its
    native malformed-range responses are plain text, so validate the supported
    single-range grammar here to keep all API errors structured and no-store.
    """

    raw = request.headers.get("range")
    if raw is None:
        return
    prefix, separator, value = raw.partition("=")
    if prefix.strip().lower() != "bytes" or not separator or "," in value:
        raise _range_not_satisfiable()
    match = re.fullmatch(r"\s*(?:(\d*)\s*-\s*(\d*))\s*", value)
    if match is None:
        raise _range_not_satisfiable()
    start_text, end_text = match.groups()
    if not start_text and not end_text:
        raise _range_not_satisfiable()
    size = media_path.stat().st_size
    if size < 1:
        raise _range_not_satisfiable()
    if start_text:
        start = int(start_text)
        end = size - 1 if not end_text else int(end_text)
        if start >= size or end < start:
            raise _range_not_satisfiable()
    elif int(end_text) < 1:
        raise _range_not_satisfiable()


def _range_not_satisfiable() -> Exception:
    return problem(
        status=416,
        code="PREVIEW_RANGE_NOT_SATISFIABLE",
        title="Preview range is not satisfiable",
        detail="The requested media byte range is invalid.",
    )


def _media_headers() -> dict[str, str]:
    return {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
