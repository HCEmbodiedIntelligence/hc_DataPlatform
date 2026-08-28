from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from hc_data_platform.preview.memory import (
    HmacUrlSigner,
    InMemoryPreviewArtifactStore,
    InMemoryPreviewRepository,
)
from hc_data_platform.preview.models import (
    PreviewPendingDescriptorV1,
    PreviewRequestV1,
    PreviewScopeV1,
)
from hc_data_platform.preview.service import PreviewControlPlaneService


def test_cold_preview_capacity_is_a_durable_202_not_an_api_compute_503() -> None:
    service = PreviewControlPlaneService(
        repository=InMemoryPreviewRepository(),
        store=InMemoryPreviewArtifactStore(),
        signer=HmacUrlSigner(),
        clock=lambda: datetime(2026, 8, 20, tzinfo=timezone.utc),
    )
    scope = PreviewScopeV1(
        organization_id="organization-1",
        project_id="project-1",
        region_code="cn-test",
    )
    request = PreviewRequestV1(
        project_id="project-1",
        dataset_id="dataset-1",
        rollout_id="rollout-1",
        lance_version="1",
        camera_id="front",
        start_step=0,
        end_step=1_000,
    )

    async def authorize_all():  # type: ignore[no-untyped-def]
        return await asyncio.gather(
            *(service.create_session(scope, request) for _ in range(1_000))
        )

    results = asyncio.run(authorize_all())

    assert all(isinstance(item, PreviewPendingDescriptorV1) for item in results)
    assert len({item.job_id for item in results}) == 1
