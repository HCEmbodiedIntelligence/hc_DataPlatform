"""Short-lived read grants for an individual member of a registered source ZIP."""

import hashlib
import hmac
import json
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from urllib.parse import quote

from fastapi import HTTPException, Request
from starlette.responses import StreamingResponse

from hc_data_platform.core.config import get_settings
from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    current_request_context,
    reset_request_context,
)
from hc_data_platform.storage.oss_zip import is_member


def _signature(token):
    key = get_settings().data_source_credential_key.get_secret_value().encode()
    return hmac.new(key, b"oss-zip-download/v1:" + token.encode(), hashlib.sha256).hexdigest()


def download_url(locator, ttl, name):
    scope = current_request_context()
    payload = [
        scope.organization_id,
        scope.project_id,
        scope.region_code,
        locator,
        int(time.time()) + min(ttl, 900),
        name or "",
    ]
    token = urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    return f"/api/v1/oss-archive-member/{token}?signature={_signature(token)}"


def download_member(token: str, signature: str, request: Request):
    if len(token) > 8192 or not hmac.compare_digest(signature, _signature(token)):
        raise HTTPException(403)
    org, project, region, locator, expires, name = json.loads(urlsafe_b64decode(token))
    if time.time() >= expires or not is_member(locator):
        raise HTTPException(403)
    scope = RequestContext(
        organization_id=org, project_id=project, region_code=region, service_identity=True
    )
    storage = request.app.state.lerobot_uploads.storage
    previous = bind_request_context(scope)
    try:
        metadata = storage.head(locator)
    finally:
        reset_request_context(previous)

    def content():
        # Starlette may resume successive iterations in distinct thread contexts.
        stream = iter(storage.read_chunks(locator))
        try:
            while True:
                previous = bind_request_context(scope)
                try:
                    chunk = next(stream, None)
                finally:
                    reset_request_context(previous)
                if chunk is None:
                    break
                yield chunk
        finally:
            stream.close()

    headers = {
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Length": str(metadata.size),
    }
    if name:
        headers["Content-Disposition"] = "attachment; filename*=UTF-8''" + quote(name, safe="")
    return StreamingResponse(content(), media_type="application/octet-stream", headers=headers)
