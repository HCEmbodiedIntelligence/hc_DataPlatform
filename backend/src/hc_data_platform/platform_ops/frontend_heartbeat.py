"""Minimal frontend-Pod heartbeat sidecar with no Kubernetes API credential."""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Mapping
from contextlib import suppress
from urllib.request import urlopen
from uuid import UUID

from hc_data_platform.platform_ops.instances import (
    HEARTBEAT_INTERVAL_SECONDS,
    InstanceReadinessSummary,
    PlatformInstanceIdentity,
    PlatformInstanceService,
    PostgresPlatformInstanceRepository,
)


def frontend_identity_from_environment(environment: Mapping[str, str]) -> PlatformInstanceIdentity:
    required = (
        "HC_INSTANCE_ID",
        "HC_NODE_NAME",
        "HC_POD_NAME",
        "HC_KUBERNETES_NODE_NAME",
        "HC_RELEASE_ID",
        "HC_RELEASE_MANIFEST_DIGEST",
        "HC_FRONTEND_IMAGE_DIGEST",
        "HC_PLATFORM_VERSION",
    )
    missing = tuple(name for name in required if not environment.get(name, "").strip())
    if missing:
        raise RuntimeError(f"frontend heartbeat identity is incomplete: {missing}")
    for name in (
        "HC_RELEASE_ID",
        "HC_RELEASE_MANIFEST_DIGEST",
        "HC_FRONTEND_IMAGE_DIGEST",
    ):
        value = environment[name]
        if value == "unreleased" or value == f"sha256:{'0' * 64}":
            raise RuntimeError("frontend heartbeat identity cannot be unreleased or zero")
    return PlatformInstanceIdentity(
        instance_id=UUID(environment["HC_INSTANCE_ID"]),
        node_name=environment["HC_NODE_NAME"],
        role="frontend",
        release_id=environment["HC_RELEASE_ID"],
        release_manifest_digest=environment["HC_RELEASE_MANIFEST_DIGEST"],
        component_image_digest=environment["HC_FRONTEND_IMAGE_DIGEST"],
        runtime_version=f"frontend/{environment['HC_PLATFORM_VERSION']}",
        pod_name=environment["HC_POD_NAME"],
        kubernetes_node_name=environment["HC_KUBERNETES_NODE_NAME"],
        kubernetes_zone=environment.get("HC_KUBERNETES_ZONE") or None,
    )


def _frontend_ready(url: str) -> InstanceReadinessSummary:
    try:
        with urlopen(url, timeout=3) as response:  # noqa: S310 - fixed operator-owned localhost URL
            ready = response.status == 200
    except OSError:
        ready = False
    return InstanceReadinessSummary(
        status="ready" if ready else "not_ready",
        failed_checks=() if ready else ("frontend_http",),
    )


async def serve() -> None:
    dsn = os.getenv("HC_POSTGRES_DSN", "")
    if not dsn:
        raise RuntimeError("HC_POSTGRES_DSN is required")
    ready_url = os.getenv("HC_FRONTEND_READINESS_URL", "http://127.0.0.1:8080/healthz")
    if ready_url != "http://127.0.0.1:8080/healthz":
        raise RuntimeError("HC_FRONTEND_READINESS_URL must use the fixed local frontend health URL")
    identity = frontend_identity_from_environment(os.environ)
    service = PlatformInstanceService(PostgresPlatformInstanceRepository.from_dsn(dsn), identity)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(signum, stopped.set)

    while not stopped.is_set():
        readiness = await asyncio.to_thread(_frontend_ready, ready_url)
        await asyncio.to_thread(service.heartbeat, readiness)
        with suppress(TimeoutError):
            await asyncio.wait_for(stopped.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)


def main() -> None:
    asyncio.run(serve())


__all__ = ["frontend_identity_from_environment", "main", "serve"]
