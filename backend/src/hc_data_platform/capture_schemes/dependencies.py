"""Every capture route uses only the components owned by its application."""

from typing import Annotated, cast

from fastapi import Depends, Request

from .common import reject
from .components import CaptureServices


def application_capture(request: Request) -> CaptureServices | None:
    # Check authorization in the endpoint before reporting missing dependencies.
    return cast(CaptureServices | None, getattr(request.app.state, "capture_schemes", None))


Services = Annotated[CaptureServices | None, Depends(application_capture)]


def require_services(services: CaptureServices | None) -> CaptureServices:
    if services is None:
        reject("CAPTURE_STORE_UNAVAILABLE", "当前服务未配置采集方案存储。", 503)
    return services
