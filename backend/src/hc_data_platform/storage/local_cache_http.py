"""Short-lived signed downloads, served by the same platform origin."""

import mimetypes
from http.client import HTTPSConnection, HTTPException as HTTPClientError
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.responses import FileResponse, StreamingResponse

from hc_data_platform.core.config import get_settings
from hc_data_platform.security.http import VerifiedAuth

from .local_cache import CacheError, LocalCacheBucket

from .oss_zip_download import download_member

router = APIRouter()
router.add_api_route("/api/v1/oss-archive-member/{token}", download_member, methods=["GET"], include_in_schema=False)


@router.api_route(
    "/api/v1/local-cache/{kind}/{identity}", methods=["GET", "HEAD"], include_in_schema=False
)
def cached_content(kind: str, identity: str, expires: int, signature: str, name: str = ""):
    settings = get_settings()
    if not settings.object_store_cache_root:
        raise HTTPException(404)
    cache = LocalCacheBucket(
        settings.object_store_cache_root,
        settings.data_source_credential_key.get_secret_value(),
        ttl_days=None,
    )
    try:
        path, meta = cache.signed_file(kind, identity, expires, signature, name)
    except CacheError as exc:
        raise HTTPException(exc.status) from None
    return FileResponse(
        path,
        media_type=mimetypes.guess_type(meta["key"])[0] or "application/octet-stream",
        filename=(name or "dataset-export.zip") if kind == "episode-deliveries" else None,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


class SignedFileRead(BaseModel):
    url: str = Field(max_length=16384)
    byte_range: tuple[int, int, int] | None = None


@router.post("/api/v1/storage-downloads/content", include_in_schema=False)
def read_signed_file(body: SignedFileRead, request: Request, auth: VerifiedAuth):
    """Relay a signed GET to the configured OSS bucket without server credentials."""
    settings = request.app.state.settings
    if settings.object_store_owner_organization_id:
        from hc_data_platform.core.context import current_request_context
        organization = current_request_context().organization_id
        if not organization or (not auth.is_platform_admin and organization not in auth.organization_ids):
            raise HTTPException(403, "Select an authorized organization")
        config = request.app.state.object_store_config_service.for_organization(organization).current()
        if not config.configured:
            raise HTTPException(409, "请先为本组织配置 OSS。")
        provider, public_endpoint, bucket = config.provider, config.public_endpoint, config.bucket
    else:
        provider, public_endpoint, bucket = settings.object_store_provider, settings.object_store_public_endpoint, settings.object_store_bucket
    endpoint = urlsplit(public_endpoint or "")
    if provider != "oss" or endpoint.scheme != "https" or not endpoint.hostname:
        raise HTTPException(400, "No OSS download origin configured")
    host = f"{bucket}.{endpoint.hostname}"
    try:
        url = urlsplit(body.url)
        query = parse_qs(url.query, max_num_fields=64)
    except ValueError:
        raise HTTPException(400, "Invalid file grant") from None
    if (url.scheme != "https" or url.netloc != host or url.fragment
            or not url.path.startswith("/") or url.path.startswith("//") or url.path == "/"
            or not ({"Signature", "Expires", "OSSAccessKeyId"} <= query.keys()
                    or {"x-oss-signature", "x-oss-credential"} <= query.keys()
                    or {"X-Amz-Signature", "X-Amz-Credential"} <= query.keys())):
        raise HTTPException(400, "Invalid file grant")
    expected_range = None
    if body.byte_range is not None:
        start, end, total = body.byte_range
        if not (0 <= start <= end < total and end - start < 8 * 1024**2):
            raise HTTPException(422, "Invalid archive byte range")
        expected_range = f"bytes {start}-{end}/{total}"
    connection = HTTPSConnection(host, timeout=90)
    try:
        target = url.path + "?" + url.query
        if expected_range:
            connection.request("GET", target, headers={"Range": f"bytes={start}-{end}"})
        else:
            connection.request("GET", target)
        upstream = connection.getresponse()
    except (OSError, HTTPClientError, ValueError, UnicodeError):
        connection.close()
        raise HTTPException(502, "Object storage read failed") from None
    if upstream.status != (206 if expected_range else 200) or (
        expected_range and upstream.getheader("Content-Range") != expected_range
    ):
        upstream.close()
        connection.close()
        raise HTTPException(upstream.status if upstream.status in (403, 404, 410) else 502,
                            "Object storage declined this file grant")

    def chunks():
        try:
            while chunk := upstream.read(1024 * 1024):
                yield chunk
        finally:
            upstream.close()
            connection.close()

    headers = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
               "Content-Disposition": "attachment", "X-Accel-Buffering": "no"}
    if upstream.length is not None:
        headers["Content-Length"] = str(upstream.length)
    return StreamingResponse(chunks(), media_type="application/octet-stream", headers=headers,
                             background=BackgroundTask(connection.close))
