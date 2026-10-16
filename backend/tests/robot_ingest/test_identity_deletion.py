from dataclasses import replace

import pytest

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.robot_ingest.models import (
    CreateRobotIngestIdentity,
    IdentityState,
    IssueCredentialCommand,
)
from hc_data_platform.robot_ingest.repository import RobotIngestIdentityMissing
from tests.robot_ingest.test_robot_ingest_service import (
    ORG,
    PROJECT_A,
    REGION,
    ROBOT,
    Harness,
    _admin,
    _assert_problem,
    _complete_first_asset,
    _manifest,
)


@pytest.mark.parametrize("disabled", [False, True])
def test_delete_revokes_all_credentials_preserves_uploads_and_allows_fresh_identity(disabled):
    harness = Harness()
    service = harness.service
    admin = {"auth": _admin(), "organization_id": ORG, "project_id": PROJECT_A}
    selected = {**admin, "ingest_identity_id": harness.identity_id}
    upload_id = _complete_first_asset(harness, _manifest())
    committed = service.commit_upload(token=harness.token, upload_id=upload_id).data
    extra = service.issue_credential(**selected, command=IssueCredentialCommand()).credential
    assert extra is not None
    stale_identity = service.get_identity(**selected).data
    history = service.list_attempts(**admin, region_code=REGION, robot_id=ROBOT)
    if disabled:
        service.set_identity_state(**selected, state=IdentityState.DISABLED)

    service.delete_identity(**selected)
    service.delete_identity(**selected)  # Safe retry after a lost response.
    assert service.list_identities(**admin).items == ()
    assert service.list_robot_uploads(**admin, region_code=REGION).items == (committed,)
    assert service.list_attempts(**admin, region_code=REGION, robot_id=ROBOT) == history
    for action in (
        lambda: service.get_identity(**selected),
        lambda: service.set_identity_state(**selected, state=IdentityState.ENABLED),
        lambda: service.issue_credential(**selected, command=IssueCredentialCommand()),
    ):
        _assert_problem("ROBOT_INGEST_IDENTITY_NOT_FOUND", action)
    with pytest.raises(RobotIngestIdentityMissing):
        harness.repository.save_identity(stale_identity)

    fresh = service.create_identity(
        **admin, command=CreateRobotIngestIdentity(robot_id=ROBOT, allowed_formats=("MCAP",))
    ).data
    assert fresh.ingest_identity_id != harness.identity_id
    assert (
        service.create_identity(
            **admin, command=CreateRobotIngestIdentity(robot_id=ROBOT, allowed_formats=("MCAP",))
        ).data
        == fresh
    )
    issued = service.issue_credential(
        **admin, ingest_identity_id=fresh.ingest_identity_id, command=IssueCredentialCommand()
    ).credential
    assert issued is not None
    assert service.authenticate(issued.token).ingest_identity_id == fresh.ingest_identity_id
    for token in (harness.token, extra.token):
        _assert_problem("ROBOT_CREDENTIAL_REVOKED", lambda token=token: service.authenticate(token))
    # Retrying deletion of the old identity must not remove its replacement.
    service.delete_identity(**selected)
    assert (
        service.get_identity(
            **admin, ingest_identity_id=fresh.ingest_identity_id
        ).data.ingest_identity_id
        == fresh.ingest_identity_id
    )


def test_delete_requires_management_permission_and_exact_organization():
    harness = Harness()
    selected = {
        "organization_id": ORG,
        "project_id": PROJECT_A,
        "ingest_identity_id": harness.identity_id,
    }
    read_only = replace(
        _admin(),
        organization_scoped_capabilities=frozenset(
            {
                (ORG, PROJECT_A, "ingest_source.read"),
            }
        ),
    )
    for auth, scope in (
        (read_only, selected),
        (_admin(), {**selected, "organization_id": "another-org"}),
    ):
        with pytest.raises(ProblemException) as caught:
            harness.service.delete_identity(auth=auth, **scope)
        assert caught.value.problem.status == 403
    assert harness.service.authenticate(harness.token).ingest_identity_id == harness.identity_id
    _assert_problem(
        "ROBOT_INGEST_IDENTITY_NOT_FOUND",
        lambda: harness.service.delete_identity(
            auth=_admin(), **{**selected, "ingest_identity_id": "rii-missing"}
        ),
    )


def test_delete_api_returns_no_content_and_deleted_identity_cannot_be_reenabled():
    from tests.robot_ingest.test_robot_ingest_api import ORG as API_ORG
    from tests.robot_ingest.test_robot_ingest_api import PROJECT, _app, _service

    client = _app(_service())
    root = f"/api/v1/projects/{PROJECT}/robot-ingest/identities"
    headers = {"Authorization": "Bearer user-session", "X-Organization-Id": API_ORG}
    created = client.post(
        root,
        headers=headers,
        json={"robot_id": "delete-api-robot", "allowed_formats": ["MCAP"]},
    )
    assert created.status_code == 201
    identity_id = created.json()["data"]["ingest_identity_id"]
    path = f"{root}/{identity_id}"
    for _ in range(2):
        deleted = client.delete(path, headers=headers)
        assert deleted.status_code == 204
        assert deleted.content == b""
        assert deleted.headers["cache-control"] == "no-store"
    assert client.get(root, headers=headers).json() == {"items": []}
    assert client.get(path, headers=headers).status_code == 404
    assert client.post(path + ":enable", headers=headers).status_code == 404
