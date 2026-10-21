from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi import Response
from starlette.requests import Request

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.robot_assets.repository import InMemoryOrganizationRobotAssetRepository
from hc_data_platform.robot_assets.service import OrganizationRobotAssetService
from hc_data_platform.robot_ingest.models import IdentityState, IssueCredentialCommand
from hc_data_platform.robot_ingest.onboarding import project_robot, robot_page
from hc_data_platform.robot_ingest.onboarding_api import (
    onboard_robot,
    robot_heartbeat,
    successful_volume,
)
from hc_data_platform.robot_ingest.onboarding_models import (
    OnboardingFacts,
    OnboardRobotCommand,
    RobotHeartbeat,
)
from tests.robot_ingest.test_robot_ingest_service import (
    NOW,
    ORG,
    PROJECT_A,
    REGION,
    ROBOT,
    Harness,
    _admin,
    _complete_first_asset,
    _manifest,
)


def facts(h: Harness, **patch) -> OnboardingFacts:
    identity = h.repository.get_identity(ORG, h.identity_id)
    return OnboardingFacts(
        robot_id=ROBOT,
        display_name="OpenArm",
        serial_no="serial-1",
        lifecycle_status="ACTIVE",
        model_version_id="published-model",
        updated_at=NOW,
        identity=identity,
        credentials=h.repository.list_credentials(ORG, h.identity_id),
    ).model_copy(update=patch)


def test_device_observation_timeout_idle_and_activity_recovery():
    h = Harness()
    assert project_robot(facts(h), h.now).connection_state == "UNVERIFIED"
    assert project_robot(facts(h), h.now).pending
    robot_heartbeat(RobotHeartbeat(activity="COLLECTING"), h.token, Response(), h.service)
    assert project_robot(facts(h), h.now).connection_state == "ONLINE"
    h.now += timedelta(seconds=300)
    current = project_robot(facts(h), h.now)
    assert current.connection_state == "OFFLINE"
    assert [f.code for f in current.faults] == ["ACTIVE_COMMUNICATION_TIMEOUT"]
    h.now += timedelta(hours=1)
    assert project_robot(facts(h), h.now).faults  # passage of time is not recovery
    robot_heartbeat(RobotHeartbeat(), h.token, Response(), h.service)
    assert not project_robot(facts(h), h.now).faults
    h.now += timedelta(seconds=301)
    assert project_robot(facts(h), h.now).connection_state == "OFFLINE"
    assert not project_robot(facts(h), h.now).faults


def test_credential_availability_independent_of_permission_and_resolved_failures():
    h = Harness()
    h.service.authenticate(h.token)
    replacement = h.service.issue_credential(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        ingest_identity_id=h.identity_id,
        command=IssueCredentialCommand(revoke_previous=True),
    ).credential
    h.now += timedelta(seconds=1)
    with pytest.raises(ProblemException):
        h.service.authenticate(h.token)
    current = project_robot(facts(h), h.now)
    assert current.credential_availability == "AVAILABLE"
    assert current.faults[0].code == "ROBOT_CREDENTIAL_REVOKED"
    h.now += timedelta(seconds=1)
    h.service.authenticate(replacement.token)
    assert not project_robot(facts(h), h.now).faults
    h.service.set_identity_state(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        ingest_identity_id=h.identity_id,
        state=IdentityState.DISABLED,
    )
    current = project_robot(facts(h), h.now)
    assert current.upload_permission == "DISABLED"
    assert current.credential_availability == "AVAILABLE"
    assert not current.pending and not current.faults


def test_expired_credentials_only_need_attention_after_first_onboarding():
    h = Harness()
    h.service.revoke_credential(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        ingest_identity_id=h.identity_id,
        credential_id=h.credential_id,
    )
    assert project_robot(facts(h), NOW).pending
    assert not project_robot(facts(h), NOW).faults
    identity = facts(h).identity.model_copy(update={"last_seen_at": NOW - timedelta(days=1)})
    row = project_robot(facts(h, identity=identity), NOW)
    assert row.faults[0].code == "NO_USABLE_CREDENTIAL"
    assert not project_robot(facts(h, identity=identity, lifecycle_status="RETIRED"), NOW).faults


def test_unknown_is_not_zero_and_filters_apply_before_paging():
    h = Harness()
    old = facts(h).identity.model_copy(update={"observation_started_at": None})
    rows = tuple(
        facts(h, robot_id=f"robot-{i:02d}", display_name=f"Device {i:02d}", identity=old)
        for i in range(31)
    )
    page = robot_page(rows, NOW, page_size=10, page=3, sort="name")
    assert len(page.items) == 10 and page.summary.total_count == 31
    assert page.summary.online_count is None and page.summary.unknown_count == 31
    filtered = robot_page(
        rows,
        NOW,
        query="robot-30",
        page_size=10,
        credential="AVAILABLE",
        permission="ENABLED",
        segment="pending",
    )
    assert [item.robot_id for item in filtered.items] == ["robot-30"]
    assert filtered.summary == page.summary
    assert not robot_page(rows, NOW, credential="AVAILABLE", permission="DISABLED").items


def test_concurrent_ensure_and_explicit_rotation_receipts_do_not_multiply_credentials():
    h = Harness()

    def ensure(_):
        return h.service.issue_credential(
            auth=_admin(),
            organization_id=ORG,
            project_id=PROJECT_A,
            ingest_identity_id=h.identity_id,
            command=IssueCredentialCommand(),
            only_if_missing=True,
        )

    with ThreadPoolExecutor(max_workers=8) as workers:
        assert all(result.credential is None for result in workers.map(ensure, range(20)))
    assert len(h.repository.list_credentials(ORG, h.identity_id)) == 1
    h.service.revoke_credential(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        ingest_identity_id=h.identity_id,
        credential_id=h.credential_id,
    )
    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(ensure, range(20)))
    assert sum(result.credential is not None for result in results) == 1
    command = IssueCredentialCommand(revoke_previous=True, idempotency_key="same-rotation")
    for _ in range(2):
        h.service.issue_credential(
            auth=_admin(),
            organization_id=ORG,
            project_id=PROJECT_A,
            ingest_identity_id=h.identity_id,
            command=command,
        )
    assert len(h.repository.list_credentials(ORG, h.identity_id)) == 3


def test_replaying_a_noop_after_revocation_does_not_issue_a_new_credential():
    h = Harness()
    command = IssueCredentialCommand(idempotency_key="reuse-existing-credential")

    def replay():
        return h.service.issue_credential(
            auth=_admin(),
            organization_id=ORG,
            project_id=PROJECT_A,
            ingest_identity_id=h.identity_id,
            command=command,
            only_if_missing=True,
        )

    assert replay().credential is None
    h.service.revoke_credential(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        ingest_identity_id=h.identity_id,
        credential_id=h.credential_id,
    )
    assert replay().credential is None
    assert len(h.repository.list_credentials(ORG, h.identity_id)) == 1


def test_unified_creation_partial_failure_resume_and_disabled_replay():
    h = Harness()
    assets_repo = InMemoryOrganizationRobotAssetRepository((ORG,))
    assets = OrganizationRobotAssetService(assets_repo, clock=lambda: NOW)
    auth = replace(
        _admin(),
        organization_scoped_capabilities=_admin().organization_scoped_capabilities
        | frozenset(
            (ORG, PROJECT_A, c) for c in ("robot.manage", "robot.read", "robot_model.manage")
        ),
    )
    command = OnboardRobotCommand(
        display_name="New OpenArm",
        serial_no="real-serial",
        model_version_id="published-model",
        allowed_formats=("MCAP",),
        idempotency_key="onboarding-creation",
        generate_credential=True,
    )

    def submit(command=command, auth=auth):
        return onboard_robot(
            PROJECT_A, command, auth, ORG, Request({"type": "http"}), Response(), h.service, assets
        )

    with pytest.raises(ProblemException):
        submit()  # instance succeeds, unpublished model fails
    assert len(assets_repo._robots) == 1
    assets_repo.add_published_version(ORG, "published-model")
    first = submit()
    repeated = submit()
    assert len(assets_repo._robots) == 1
    assert first.robot.robot_id == repeated.robot.robot_id
    assert first.identity.credential and repeated.identity.credential is None
    identity_id = first.identity.data.ingest_identity_id
    h.service.set_identity_state(
        auth=auth,
        organization_id=ORG,
        project_id=PROJECT_A,
        ingest_identity_id=identity_id,
        state=IdentityState.DISABLED,
    )
    assert submit().identity.data.state is IdentityState.DISABLED
    assert len(h.repository.list_credentials(ORG, identity_id)) == 1
    with pytest.raises(ProblemException) as denied:
        submit(auth=replace(auth, organization_scope_triples=frozenset()))
    assert denied.value.problem.status == 403


def test_commit_day_timezone_boundaries_and_retry_deduplication():
    h = Harness()
    # Created before local midnight, submitted exactly at local midnight.
    h.now = NOW.replace(hour=15, minute=59)
    upload_id = _complete_first_asset(h, _manifest())
    h.now = NOW.replace(hour=16, minute=0)
    h.service.commit_upload(token=h.token, upload_id=upload_id)
    h.service.commit_upload(token=h.token, upload_id=upload_id)
    reader = replace(
        _admin(),
        organization_scoped_capabilities=_admin().organization_scoped_capabilities
        | frozenset({(ORG, PROJECT_A, "upload.read")}),
    )
    kwargs = dict(
        project_id=PROJECT_A,
        auth=reader,
        organization_id=ORG,
        region_code=REGION,
        response=Response(),
        service=h.service,
        robot_id=ROBOT,
    )
    today = successful_volume(**kwargs, day=NOW.date() + timedelta(days=1), tz="Asia/Shanghai")
    yesterday = successful_volume(**kwargs, day=NOW.date(), tz="Asia/Shanghai")
    utc = successful_volume(**kwargs, day=NOW.date(), tz="UTC")
    assert today.raw_bytes == len(b"robot-raw-data") and today.upload_count == 1
    assert yesterday.raw_bytes == 0 and utc.raw_bytes == today.raw_bytes
    dst = successful_volume(
        **kwargs, day=NOW.date().replace(month=11, day=1), tz="America/New_York"
    )
    assert dst.to_exclusive - dst.from_inclusive == timedelta(hours=25)
    with pytest.raises(ProblemException) as invalid:
        successful_volume(**kwargs, tz="not-a-timezone")
    assert invalid.value.problem.status == 422


def test_http_onboarding_query_validation_and_cross_organization_denial():
    from hc_data_platform.robot_ingest.onboarding_api import router
    from tests.robot_ingest.test_robot_ingest_api import ORG as API_ORG
    from tests.robot_ingest.test_robot_ingest_api import PROJECT, _app, _service

    client = _app(_service())
    client.app.include_router(router)
    path = f"/api/v1/projects/{PROJECT}/robot-ingest/onboarding"
    for size in (10, 20, 50):
        response = client.get(
            path, headers={"X-Organization-Id": API_ORG}, params={"page_size": size}
        )
        assert response.status_code == 200
        assert response.json()["page_size"] == size
        assert response.headers["cache-control"] == "no-store"
    assert (
        client.get(
            path, headers={"X-Organization-Id": API_ORG}, params={"page_size": 17}
        ).status_code
        == 422
    )
    assert client.get(path, headers={"X-Organization-Id": "other-org"}).status_code == 403


@pytest.mark.parametrize(
    ("method", "path", "code"),
    (
        ("GET", "/api/v1/projects/p/robot-ingest/onboarding", "AUTHENTICATION_REQUIRED"),
        ("POST", "/api/v1/projects/p/robot-ingest/onboarding", "AUTHENTICATION_REQUIRED"),
        ("GET", "/api/v1/projects/p/robot-ingest/successful-volume", "AUTHENTICATION_REQUIRED"),
        (
            "GET",
            "/api/v1/projects/p/regions/r/lerobot-imports/robot-options",
            "AUTHENTICATION_REQUIRED",
        ),
        ("POST", "/api/v1/robot-ingest/heartbeat", "ROBOT_CREDENTIAL_INVALID"),
    ),
)
def test_new_public_operations_reject_anonymous_before_payload_validation(method, path, code):
    from fastapi.testclient import TestClient

    from hc_data_platform.core.app import create_app
    from hc_data_platform.core.config import Settings

    app = create_app(
        settings=Settings(environment="test", runtime_backend="memory", _env_file=None)
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.request(method, path, json={})
    assert response.status_code == 401
    assert response.json()["code"] == code
