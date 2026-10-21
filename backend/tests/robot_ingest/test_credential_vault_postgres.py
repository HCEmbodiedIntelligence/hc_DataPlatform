"""Real transactions, encryption persistence, deletion fences and upload FKs."""

from datetime import timedelta

import psycopg
import pytest

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.robot_ingest.models import IssueCredentialCommand
from hc_data_platform.robot_ingest.onboarding import robot_page
from hc_data_platform.robot_ingest.service import RobotIngestService
from tests.robot_ingest.test_onboarding_postgres import (
    DSN,
    NOW,
    ORG,
    PROJECT_A,
    TASK_A,
    _admin,
)
from tests.robot_ingest.test_onboarding_postgres import pg_robot as pg_robot  # noqa: F401
from tests.robot_ingest.test_robot_ingest_service import _manifest

pytestmark = pytest.mark.skipif(not DSN, reason="Requires a disposable migrated PostgreSQL")


def test_pg_reveal_legacy_backfill_and_delete_preserve_history(pg_robot):
    robot, repo, service, identity, _ = pg_robot
    scope = dict(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        ingest_identity_id=identity.ingest_identity_id,
    )
    command = IssueCredentialCommand(idempotency_key="persistent-credential-request")
    credential = service.issue_credential(**scope, command=command).credential
    assert credential is not None
    lookup = dict(**scope, credential_id=credential.credential_id)
    reopened = RobotIngestService(
        repo, service.storage, credential_hmac_key="onboarding-postgres-test-key", clock=lambda: NOW
    )
    assert reopened.reveal_credential(**lookup).token == credential.token
    assert repo.get_identity(ORG, identity.ingest_identity_id).last_seen_at is None
    with psycopg.connect(DSN) as db:
        cipher, digest = db.execute(
            """SELECT secret_ciphertext, token_digest
            FROM ingest.robot_ingest_credentials WHERE credential_id=%s""",
            (credential.credential_id,),
        ).fetchone()
        assert cipher and credential.token not in cipher
        db.execute(
            """UPDATE ingest.robot_ingest_credentials SET secret_ciphertext=NULL
            WHERE credential_id=%s""",
            (credential.credential_id,),
        )
    with pytest.raises(ProblemException) as error:
        service.reveal_credential(**lookup)
    assert error.value.problem.code == "ROBOT_CREDENTIAL_LEGACY_SECRET_UNAVAILABLE"
    auth = service.authenticate(credential.token)
    assert service.reveal_credential(**lookup).token == credential.token
    upload = service.create_upload(
        token=credential.token, manifest=_manifest(robot_id=robot, task_id=TASK_A)
    ).data
    service.revoke_credential(**lookup)
    service.delete_credential(**lookup)
    service.delete_credential(**lookup)
    # Late legacy-auth backfill cannot undo deletion or restore ciphertext.
    repo.cache_credential_secret(auth, digest, cipher, NOW)
    assert not repo.list_credentials(ORG, identity.ingest_identity_id)
    assert service.issue_credential(**scope, command=command).credential is None
    assert (
        repo.get_upload(ORG, upload.upload_id, identity.ingest_identity_id).credential_id
        == credential.credential_id
    )
    page = robot_page(repo.onboarding_facts(ORG), NOW, query=robot)
    assert page.items[0].credential_availability == "UNAVAILABLE"
    with psycopg.connect(DSN) as db:
        row = db.execute(
            """SELECT state,secret_ciphertext,deleted_at
            FROM ingest.robot_ingest_credentials WHERE credential_id=%s""",
            (credential.credential_id,),
        ).fetchone()
        assert row == ("REVOKED", None, NOW)
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                """UPDATE ingest.robot_ingest_credentials SET secret_ciphertext=%s
                WHERE credential_id=%s""",
                (cipher, credential.credential_id),
            )


def test_pg_expiry_and_foreign_credential_are_enforced(pg_robot):
    _, repo, service, identity, _ = pg_robot
    scope = dict(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        ingest_identity_id=identity.ingest_identity_id,
    )
    issued = service.issue_credential(
        **scope, command=IssueCredentialCommand(expires_at=NOW + timedelta(minutes=1))
    ).credential
    assert issued is not None
    lookup = dict(**scope, credential_id=issued.credential_id)
    with pytest.raises(ProblemException) as error:
        service.delete_credential(**lookup)
    assert error.value.problem.status == 409
    assert (
        repo.credential_record("other-org", identity.ingest_identity_id, issued.credential_id)
        is None
    )
    assert not repo.delete_credential(
        "other-org", identity.ingest_identity_id, issued.credential_id, NOW
    )
    later = RobotIngestService(
        repo,
        service.storage,
        credential_hmac_key="onboarding-postgres-test-key",
        clock=lambda: NOW + timedelta(minutes=2),
    )
    with pytest.raises(ProblemException) as error:
        later.reveal_credential(**lookup)
    assert error.value.problem.status == 409
    later.delete_credential(**lookup)
    assert not repo.list_credentials(ORG, identity.ingest_identity_id)
