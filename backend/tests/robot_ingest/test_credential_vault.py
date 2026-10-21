"""Credential retrieval and removal preserve existing authentication and history."""

from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.robot_ingest.credential_vault import CredentialVault
from hc_data_platform.robot_ingest.models import (
    CreateRobotIngestIdentity,
    IdentityState,
    IssueCredentialCommand,
)
from hc_data_platform.robot_ingest.service import RobotIngestService
from tests.robot_ingest import test_robot_ingest_api as api
from tests.robot_ingest.test_robot_ingest_service import (
    NOW,
    ORG,
    PROJECT_A,
    Harness,
    _admin,
    _manifest,
)


def args(h):
    return dict(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        ingest_identity_id=h.identity_id,
        credential_id=h.credential_id,
    )


def test_reveal_survives_service_restart_without_issuing_or_marking_online():
    h = Harness()
    stored = h.repository._credentials[h.credential_id]
    assert stored.secret_ciphertext and h.token not in stored.secret_ciphertext
    assert h.token not in repr(stored)
    service = RobotIngestService(
        h.repository,
        h.storage,
        credential_hmac_key="robot-ingest-test-hmac-key",
        clock=lambda: h.now,
    )
    for _ in range(3):
        assert service.reveal_credential(**args(h)).token == h.token
    identity = h.repository.get_identity(ORG, h.identity_id)
    assert identity.credential_revision == 1 and identity.last_seen_at is None
    credentials = h.repository.list_credentials(ORG, h.identity_id)
    assert len(credentials) == 1 and credentials[0].reveal_available
    assert h.token not in credentials[0].model_dump_json()
    assert service.authenticate(h.token).credential_id == h.credential_id


def test_reveal_does_not_enable_a_disabled_identity_and_active_delete_is_rejected():
    h = Harness()
    h.service.set_identity_state(
        **{k: v for k, v in args(h).items() if k != "credential_id"}, state=IdentityState.DISABLED
    )
    assert h.service.reveal_credential(**args(h)).token == h.token
    with pytest.raises(ProblemException) as error:
        h.service.delete_credential(**args(h))
    assert error.value.problem.code == "ROBOT_CREDENTIAL_STILL_ACTIVE"
    with pytest.raises(ProblemException) as error:
        h.service.authenticate(h.token)
    assert error.value.problem.code == "ROBOT_IDENTITY_DISABLED"
    assert h.repository.get_identity(ORG, h.identity_id).state == IdentityState.DISABLED


def test_legacy_secret_is_backfilled_only_after_valid_authentication():
    h = Harness()
    original = h.repository._credentials[h.credential_id]
    h.repository._credentials[h.credential_id] = replace(
        original,
        secret_ciphertext=None,
        summary=original.summary.model_copy(update={"reveal_available": False}),
    )
    with pytest.raises(ProblemException) as error:
        h.service.reveal_credential(**args(h))
    assert error.value.problem.code == "ROBOT_CREDENTIAL_LEGACY_SECRET_UNAVAILABLE"
    with pytest.raises(ProblemException):
        h.service.authenticate(h.token + "wrong")
    assert h.repository._credentials[h.credential_id].secret_ciphertext is None
    h.service.authenticate(h.token)
    assert h.service.reveal_credential(**args(h)).token == h.token
    assert h.repository._credentials[h.credential_id].token_digest == original.token_digest
    assert h.repository.get_identity(ORG, h.identity_id).credential_revision == 1


def test_revoke_delete_and_repeated_delete_keep_upload_and_do_not_resurrect_secret():
    h = Harness()
    upload = h.service.create_upload(token=h.token, manifest=_manifest()).data
    auth = h.service.authenticate(h.token)
    original = h.repository._credentials[h.credential_id]
    h.service.revoke_credential(**args(h))
    for _ in range(2):
        h.service.delete_credential(**args(h))
    h.repository.cache_credential_secret(
        auth, original.token_digest, original.secret_ciphertext, NOW
    )
    assert h.repository.list_credentials(ORG, h.identity_id) == ()
    tombstone = h.repository._credentials[h.credential_id]
    assert tombstone.secret_ciphertext is None and tombstone.deleted_at == NOW
    assert (
        h.repository.get_upload(ORG, upload.upload_id, h.identity_id).credential_id
        == h.credential_id
    )
    with pytest.raises(ProblemException) as error:
        h.service.authenticate(h.token)
    assert error.value.problem.code == "ROBOT_CREDENTIAL_REVOKED"
    with pytest.raises(ProblemException) as error:
        h.service.reveal_credential(**args(h))
    assert error.value.problem.status == 404


def test_expired_credential_can_be_deleted_and_issue_retry_cannot_restore_it():
    h = Harness()
    issue_args = {k: v for k, v in args(h).items() if k != "credential_id"}
    command = IssueCredentialCommand(
        idempotency_key="credential-vault-retry", expires_at=NOW + timedelta(minutes=1)
    )
    issued = h.service.issue_credential(**issue_args, command=command).credential
    assert issued is not None
    h.now += timedelta(minutes=2)
    with pytest.raises(ProblemException) as error:
        h.service.reveal_credential(**{**args(h), "credential_id": issued.credential_id})
    assert error.value.problem.code == "ROBOT_CREDENTIAL_INACTIVE"
    h.service.delete_credential(**{**args(h), "credential_id": issued.credential_id})
    # The same request receipt stays recorded after removing a revoked credential.
    h.now = NOW
    replay = h.service.issue_credential(**issue_args, command=command)
    assert replay.credential is None
    assert len(h.repository.list_credentials(ORG, h.identity_id)) == 1


@pytest.mark.parametrize("operation", ["reveal_credential", "delete_credential"])
def test_credential_operations_require_management_and_exact_organization(operation):
    h = Harness()
    reader = replace(
        _admin(),
        organization_scoped_capabilities=frozenset({(ORG, PROJECT_A, "ingest_source.read")}),
    )
    for overrides in ({"auth": reader}, {"organization_id": "other-org"}):
        with pytest.raises(ProblemException) as error:
            getattr(h.service, operation)(**{**args(h), **overrides})
        assert error.value.problem.status == 403
    with pytest.raises(ProblemException) as error:
        getattr(h.service, operation)(**{**args(h), "credential_id": "ric-other"})
    assert error.value.problem.status == 404
    other_identity = h.service.create_identity(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        command=CreateRobotIngestIdentity(robot_id="other-robot", allowed_formats=("MCAP",)),
    ).data
    other = h.service.issue_credential(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        ingest_identity_id=other_identity.ingest_identity_id,
        command=IssueCredentialCommand(),
    ).credential
    assert other is not None
    with pytest.raises(ProblemException) as error:
        getattr(h.service, operation)(**{**args(h), "credential_id": other.credential_id})
    assert error.value.problem.status == 404


def test_encryption_rejects_wrong_key_or_scope_and_tampered_ciphertext():
    vault = CredentialVault(b"test-key")
    ciphertext = vault.seal("org-a", "identity-a", "credential-a", "secret-token")
    assert vault.reveal("org-a", "identity-a", "credential-a", ciphertext) == "secret-token"
    for reader, org, value in (
        (CredentialVault(b"other-key"), "org-a", ciphertext),
        (vault, "org-b", ciphertext),
        (vault, "org-a", ciphertext[:-5] + "xxxxx"),
    ):
        with pytest.raises(ProblemException) as error:
            reader.reveal(org, "identity-a", "credential-a", value)
        assert error.value.problem.status == 503
        assert "secret-token" not in error.value.problem.model_dump_json()


def test_http_reveal_delete_authorization_and_cache_control(monkeypatch):
    service = api._service()
    client = api._app(service)
    headers = {"Authorization": "Bearer user-session", "X-Organization-Id": api.ORG}
    root = f"/api/v1/projects/{api.PROJECT}/robot-ingest/identities"
    created = client.post(
        root, headers=headers, json={"robot_id": api.ROBOT, "allowed_formats": ["MCAP"]}
    )
    assert created.status_code == 201
    identity = created.json()["data"]["ingest_identity_id"]
    root = f"{root}/{identity}/credentials"
    issued = client.post(root, headers=headers, json={}).json()["credential"]
    path = f"{root}/{issued['credential_id']}"
    for _ in range(2):
        revealed = client.post(path + ":reveal", headers=headers)
        assert revealed.status_code == 200
        assert revealed.json()["token"] == issued["token"]
        assert revealed.headers["Cache-Control"] == "no-store"
    assert client.delete(path, headers=headers).status_code == 409
    auth = api._auth()
    monkeypatch.setattr(
        api,
        "_auth",
        lambda: replace(
            auth,
            organization_scoped_capabilities=frozenset(
                {(api.ORG, api.PROJECT, "ingest_source.read")}
            ),
        ),
    )
    assert client.post(path + ":reveal", headers=headers).status_code == 403
    assert client.delete(path, headers=headers).status_code == 403
    monkeypatch.setattr(api, "_auth", lambda: auth)
    foreign = {**headers, "X-Organization-Id": "other-org"}
    assert client.post(path + ":reveal", headers=foreign).status_code == 403
    assert client.delete(path, headers=foreign).status_code == 403
    assert client.post(path + ":revoke", headers=headers).status_code == 200
    assert client.post(path + ":reveal", headers=headers).status_code == 409
    for _ in range(2):
        deleted = client.delete(path, headers=headers)
        assert deleted.status_code == 204 and deleted.headers["Cache-Control"] == "no-store"
    assert client.get(root, headers=headers).json() == []


@pytest.mark.parametrize("method,suffix", [("POST", ":reveal"), ("DELETE", "")])
def test_credential_management_requires_a_user_session_before_validation(method, suffix):
    app = create_app(
        settings=Settings(environment="test", runtime_backend="memory", _env_file=None)
    )
    with TestClient(app) as client:
        response = client.request(
            method,
            "/api/v1/projects/project-a/robot-ingest/identities/rii-a/credentials/ric-a" + suffix,
        )
        assert response.status_code == 401
