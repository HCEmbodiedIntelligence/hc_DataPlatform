from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.robot_ingest.connections import LocalRobot, RedeemConnection
from hc_data_platform.robot_ingest.models import (
    IdentityState,
    IssueCredentialCommand,
    UpdateRobotIngestIdentity,
)
from hc_data_platform.robot_ingest.onboarding_models import OnboardingFacts
from hc_data_platform.robot_ingest.service import RobotIngestService
from tests.robot_ingest.test_robot_ingest_service import (
    NOW,
    ORG,
    PROJECT_A,
    PROJECT_B,
    ROBOT,
    TASK_A,
    TASK_B,
    Harness,
    _admin,
    _manifest,
    _target,
)

LOCAL = LocalRobot(local_robot_id="unitree_g1", profile_id="g1", config_fingerprint="a" * 64)


def harness():
    h = Harness()
    h.repository.robot_facts[(ORG, ROBOT)] = OnboardingFacts(
        robot_id=ROBOT,
        display_name="G1",
        serial_no="real-1",
        lifecycle_status="ACTIVE",
        model_version_id="g1-model",
        updated_at=NOW,
    )
    h.repository.task_labels[TASK_A] = {
        "name": "抓杯子",
        "task_code": "00000001",
        "project_name": "项目 A",
    }
    h.repository.task_labels[TASK_B] = {
        "name": "抓杯子",
        "task_code": "00000002",
        "project_name": "项目 B",
    }
    return h


def issue(h):
    return h.service.connections.issue(_admin(), ORG, PROJECT_A, h.identity_id)


def command(ticket, request="request-id-at-least-16"):
    return RedeemConnection(ticket=ticket["ticket"], request_id=request, local_robot=LOCAL)


def revoke(h, credential_id=None):
    h.service.revoke_credential(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        ingest_identity_id=h.identity_id,
        credential_id=credential_id or h.credential_id,
    )


def test_copying_and_reconnecting_with_new_links_reuses_existing_credential():
    h = harness()
    before = h.repository.list_credentials(ORG, h.identity_id)
    tickets = [issue(h) for _ in range(5)]
    assert h.repository.list_credentials(ORG, h.identity_id) == before
    for ticket in tickets:
        assert h.service.connections.redeem(command(ticket))["token"] == h.token
    assert len(h.repository.list_credentials(ORG, h.identity_id)) == 1
    assert h.repository.get_identity(ORG, h.identity_id).credential_revision == 1


def test_different_links_concurrently_create_only_one_missing_credential():
    h = harness()
    revoke(h)
    commands = [command(issue(h)) for _ in range(12)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(h.service.connections.redeem, commands))
    assert len({result["token"] for result in results}) == 1
    assert len(h.repository.list_credentials(ORG, h.identity_id)) == 2


def test_reused_credential_is_fixed_for_retries_and_never_resurrected():
    h = harness()
    cmd = command(issue(h))
    assert h.service.connections.redeem(cmd)["token"] == h.token
    newer = h.service.issue_credential(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        ingest_identity_id=h.identity_id,
        command=IssueCredentialCommand(),
    ).credential
    assert h.service.connections.redeem(cmd)["token"] == h.token
    revoke(h)
    with pytest.raises(ProblemException):
        h.service.connections.redeem(cmd)
    assert h.service.connections.redeem(command(issue(h)))["token"] == newer.token
    assert len(h.repository.list_credentials(ORG, h.identity_id)) == 2


def test_legacy_secret_creates_once_and_expired_credentials_are_not_reused():
    from dataclasses import replace

    h = harness()
    record = h.repository._credentials[h.credential_id]
    h.repository._credentials[h.credential_id] = replace(
        record,
        secret_ciphertext=None,
        summary=record.summary.model_copy(update={"reveal_available": False}),
    )
    connected = h.service.connections.redeem(command(issue(h)))
    assert connected["token"] != h.token
    assert h.service.connections.redeem(command(issue(h)))["token"] == connected["token"]
    assert len(h.repository.list_credentials(ORG, h.identity_id)) == 2

    h = harness()
    record = h.repository._credentials[h.credential_id]
    h.repository._credentials[h.credential_id] = replace(
        record,
        summary=record.summary.model_copy(update={"expires_at": NOW}),
    )
    assert h.service.connections.redeem(command(issue(h)))["token"] != h.token


def test_ticket_ttl_retry_after_restart_and_non_revocation():
    h = harness()
    ticket = issue(h)
    assert ticket["expires_at"] == NOW + timedelta(minutes=10)
    assert len(h.repository.list_credentials(ORG, h.identity_id)) == 1
    assert h.repository.get_identity(ORG, h.identity_id).last_seen_at is None
    cmd = command(ticket)
    first = h.service.connections.redeem(cmd)
    assert first["robot_id"] == ROBOT
    h.now += timedelta(minutes=11)
    # Exact lost-response recovery is available after the initial ticket expiry.
    restored = RobotIngestService(
        h.repository,
        h.storage,
        credential_hmac_key="robot-ingest-test-hmac-key",
        clock=lambda: h.now,
    )
    assert restored.connections.redeem(cmd)["token"] == first["token"]
    assert len(h.repository.list_credentials(ORG, h.identity_id)) == 1
    assert h.service.authenticate(h.token).authenticated_robot_id == ROBOT
    with pytest.raises(ProblemException) as error:
        restored.connections.redeem(command(ticket, "different-request-id"))
    assert error.value.problem.code == "CONNECTION_TICKET_USED"


def test_production_routes_use_machine_auth_and_never_echo_ticket_in_errors():
    from fastapi.testclient import TestClient

    from hc_data_platform.core.app import create_app
    from hc_data_platform.core.config import Settings
    from hc_data_platform.robot_ingest.router import get_robot_ingest_service
    from hc_data_platform.security.auth import JwtVerifier

    h = harness()
    app = create_app(
        settings=Settings(environment="test", runtime_backend="memory"),
        jwt_verifier=JwtVerifier(
            issuer="test",
            audience="test",
            algorithms=("HS256",),
            key="synthetic-connection-test-key-123456789",
        ),
    )
    app.dependency_overrides[get_robot_ingest_service] = lambda: h.service
    ticket = issue(h)
    with TestClient(app) as client:
        result = client.post(
            "/api/v1/robot-ingest/connections:redeem", json=command(ticket).model_dump(mode="json")
        )
        assert result.status_code == 200, result.text
        assert result.headers["cache-control"] == "no-store"
        headers = {"Authorization": "Bearer " + result.json()["token"]}
        assert client.get("/api/v1/robot-ingest/me", headers=headers).status_code == 200
        assert client.get("/api/v1/robot-ingest/tasks", headers=headers).json()["total"] == 2
        assert client.get("/api/v1/robot-ingest/tasks").status_code == 401
        assert (
            client.post(
                f"/api/v1/projects/{PROJECT_A}/robot-ingest/identities/{h.identity_id}/connections",
                headers=headers,
                json={},
            ).status_code
            == 401
        )
        invalid = client.post(
            "/api/v1/robot-ingest/connections:redeem",
            json={"ticket": ticket["ticket"], "request_id": "bad"},
        )
        assert invalid.status_code == 422 and ticket["ticket"] not in invalid.text
        assert (
            app.openapi()["paths"]["/api/v1/robot-ingest/connections:redeem"]["post"]["security"]
            == []
        )


def test_expired_unused_ticket_and_disabled_identity_are_rejected():
    h = harness()
    ticket = issue(h)
    h.now += timedelta(minutes=10)
    with pytest.raises(ProblemException) as error:
        h.service.connections.redeem(command(ticket))
    assert error.value.problem.code == "CONNECTION_TICKET_EXPIRED"
    h.now = NOW
    h.service.set_identity_state(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        ingest_identity_id=h.identity_id,
        state=IdentityState.DISABLED,
    )
    with pytest.raises(ProblemException):
        h.service.connections.redeem(command(ticket))
    assert len(h.repository.list_credentials(ORG, h.identity_id)) == 1


def test_concurrent_redemption_and_crash_before_issuance(monkeypatch):
    h = harness()
    revoke(h)
    cmd = command(issue(h))
    original = h.repository.issue_credential
    monkeypatch.setattr(
        h.repository, "issue_credential", lambda **kw: (_ for _ in ()).throw(OSError("crash"))
    )
    with pytest.raises(OSError):
        h.service.connections.redeem(cmd)
    monkeypatch.setattr(h.repository, "issue_credential", original)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: h.service.connections.redeem(cmd), range(12)))
    assert len({r["token"] for r in results}) == 1
    assert len(h.repository.list_credentials(ORG, h.identity_id)) == 2


def test_ticket_scope_policy_and_local_mapping_cannot_change():
    h = harness()
    ticket = issue(h)
    h.service.update_identity(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        ingest_identity_id=h.identity_id,
        command=UpdateRobotIngestIdentity(
            allowed_formats=("MCAP",),
            allowed_transports=("HTTPS",),
            upload_policy=h.repository.get_identity(ORG, h.identity_id).upload_policy,
        ),
    )
    with pytest.raises(ProblemException) as error:
        h.service.connections.redeem(command(ticket))
    assert error.value.problem.code == "CONNECTION_PERMISSION_CHANGED"
    h.service.connections.redeem(command(issue(h)))
    new_ticket = issue(h)
    cmd = command(new_ticket).model_copy(
        update={"local_robot": LOCAL.model_copy(update={"profile_id": "another"})}
    )
    with pytest.raises(ProblemException) as error:
        h.service.connections.redeem(cmd)
    assert error.value.problem.code == "LOCAL_ROBOT_MISMATCH"
    assert len(h.repository.list_credentials(ORG, h.identity_id)) == 1


def test_task_search_projects_pagination_and_cross_organization():
    h = harness()
    foreign = _target("foreign-task", "foreign-project").model_copy(
        update={"organization_id": "other-org"}
    )
    h.repository.add_target(foreign)
    auth = h.service.authenticate(h.token)
    result = h.service.connections.tasks(auth, "MCAP", q="抓", page_size=1)
    assert result["total"] == 2 and len(result["items"]) == 1
    assert len(result["projects"]) == 2
    assert (
        h.service.connections.tasks(auth, "MCAP", project_id=PROJECT_B)["items"][0][
            "collection_task_id"
        ]
        == TASK_B
    )
    assert (
        h.service.connections.tasks(auth, "MCAP", q="00000002")["items"][0]["collection_task_id"]
        == TASK_B
    )
    with pytest.raises(ProblemException) as error:
        h.service.connections.task(auth, "foreign-task", "MCAP")
    assert error.value.problem.status == 404
    with pytest.raises(ProblemException):
        h.service.create_upload(token=h.token, manifest=_manifest(task_id="foreign-task"))
    h.repository._targets[TASK_B] = _target(TASK_B, PROJECT_B, status="CLOSED")
    assert h.service.connections.tasks(auth, "MCAP")["total"] == 1
    with pytest.raises(ProblemException):
        h.service.connections.task(auth, TASK_B, "MCAP")


def test_ownership_change_blocks_new_upload_but_reconciles_original_session():
    h = harness()
    auth = h.service.authenticate(h.token)
    task = h.service.connections.task(auth, TASK_A, "MCAP")
    manifest = _manifest(format_metadata={"ownership_snapshot": task["ownership"]})
    created = h.service.create_upload(token=h.token, manifest=manifest)
    changed = _target(TASK_A, PROJECT_A).model_copy(
        update={"dataset_id": "different", "task_status": "CLOSED"}
    )
    h.repository._targets[TASK_A] = changed
    replay = h.service.create_upload(token=h.token, manifest=manifest)
    assert replay.resumed and replay.data.upload_id == created.data.upload_id
    with pytest.raises(ProblemException) as error:
        h.service.create_upload(
            token=h.token,
            manifest=_manifest(format_metadata={"ownership_snapshot": task["ownership"]}),
        )
    assert error.value.problem.code == "TASK_OWNERSHIP_CHANGED"


def test_existing_credentials_and_legacy_manifest_keep_working():
    h = harness()
    before = h.repository.list_credentials(ORG, h.identity_id)
    h.service.connections.me(h.service.authenticate(h.token))
    assert [c.credential_id for c in before] == [
        c.credential_id for c in h.repository.list_credentials(ORG, h.identity_id)
    ]
    manifest = _manifest()
    upload = h.service.create_upload(token=h.token, manifest=manifest)
    assert (
        h.service.create_upload(token=h.token, manifest=manifest).data.upload_id
        == upload.data.upload_id
    )
