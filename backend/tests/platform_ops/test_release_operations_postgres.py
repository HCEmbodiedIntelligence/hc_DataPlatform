from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from hc_data_platform.platform_control.release_feed import ReleaseImagesV1
from hc_data_platform.platform_ops.releases import (
    PostgresReleaseRepository,
    ReleaseEventRecord,
    ReleaseRunRecord,
)

NOW = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
SHA = "a" * 64
IMAGES = ReleaseImagesV1(
    frontend=f"registry/frontend@sha256:{SHA}",
    api=f"registry/api@sha256:{SHA}",
    worker=f"registry/worker@sha256:{SHA}",
    media_worker=f"registry/media-worker@sha256:{SHA}",
)


def _disposable_dsn() -> str:
    dsn = os.getenv("HC_RELEASE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("HC_RELEASE_TEST_POSTGRES_DSN is required for durable release integration")
    database = urlparse(dsn.replace("postgresql+asyncpg://", "postgresql://", 1)).path.lstrip("/")
    if not database.startswith("hc_rel6_acceptance_"):
        pytest.fail("release integration requires a dedicated hc_rel6_acceptance_* database")
    return dsn


def test_postgres_release_state_and_events_are_atomic_and_durable() -> None:
    repository = PostgresReleaseRepository.from_dsn(_disposable_dsn())
    suffix = uuid4().hex
    environment_id = f"rel6-test-{suffix}"
    release_id = f"platform-v0.1.1-{suffix}"
    record = ReleaseRunRecord(
        environment_id=environment_id,
        release_id=release_id,
        source_version="0.1.0",
        target_version="0.1.1",
        manifest_sha256=SHA,
        source_images=IMAGES,
        target_images=IMAGES,
        state="AWAITING_APPROVAL",
        state_version=1,
        requested_by="requester",
        created_at=NOW,
        updated_at=NOW,
    )
    repository.create(
        record,
        ReleaseEventRecord(
            state_version=1,
            event_kind="PREFLIGHT_PASSED",
            state="AWAITING_APPROVAL",
            actor_id="requester",
            reason_code="SIGNED_COMPATIBLE_RELEASE_VERIFIED",
            request_id="request-preflight",
            occurred_at=NOW,
        ),
    )
    approved = repository.approve(
        environment_id,
        release_id,
        expected_state_version=1,
        actor_id="distinct-approver",
        reason="approved disposable integration release",
        request_id="request-approve",
        occurred_at=NOW + timedelta(seconds=1),
    )
    current = approved
    for next_state in ("EXPAND", "CANARY", "ROLLOUT", "COMPLETED"):
        current = repository.transition(
            environment_id,
            release_id,
            expected_state_version=current.state_version,
            next_state=next_state,
            actor_id="release-controller",
            reason_code=f"ENTERED_{next_state}",
            request_id=f"request-{next_state.lower()}",
            occurred_at=NOW + timedelta(seconds=current.state_version),
        )

    reloaded = PostgresReleaseRepository.from_dsn(_disposable_dsn()).get(environment_id, release_id)
    events = repository.events(environment_id, release_id)
    assert reloaded.state == "COMPLETED"
    assert reloaded.state_version == 6
    assert reloaded.approved_by == "distinct-approver"
    assert [event.state_version for event in events] == [1, 2, 3, 4, 5, 6]
    assert [event.state for event in events] == [
        "AWAITING_APPROVAL",
        "APPROVED",
        "EXPAND",
        "CANARY",
        "ROLLOUT",
        "COMPLETED",
    ]
