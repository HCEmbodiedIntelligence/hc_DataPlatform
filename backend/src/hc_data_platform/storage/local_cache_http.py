"""Short-lived signed downloads, served by the same platform origin."""

import mimetypes

from fastapi import APIRouter, HTTPException
from starlette.responses import FileResponse

from hc_data_platform.core.config import get_settings

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
