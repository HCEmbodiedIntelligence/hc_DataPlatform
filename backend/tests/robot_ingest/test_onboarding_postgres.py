"""Real SQL/locks, canonical assets, legacy restrictions and commit-time totals."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.ingest.ports import InMemoryObjectStorage
from hc_data_platform.lerobot_imports import configuration, mcap_processing
from hc_data_platform.lerobot_imports.robot_eligibility import import_robots
from hc_data_platform.robot_ingest.models import (
    CreateRobotIngestIdentity,
    IdentityState,
    IssueCredentialCommand,
)
from hc_data_platform.robot_ingest.onboarding import robot_page
from hc_data_platform.robot_ingest.repository import PostgresRobotIngestRepository
from hc_data_platform.robot_ingest.service import RobotIngestService
from hc_data_platform.security.auth import AuthContext
from tests.robot_ingest.test_robot_ingest_postgres import NOW

DSN = os.environ.get("ROBOT_INGEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="Requires a disposable migrated PostgreSQL")
ORG = "onboarding-pg-org"
PROJECT_A, PROJECT_B = "onboarding-pg-a", "onboarding-pg-b"
REGION_A, REGION_B = "onboarding-region-a", "onboarding-region-b"
TASK_A = "onboarding-pg-task"


def _admin():
    return AuthContext(
        subject_id="onboarding-admin",
        organization_ids=frozenset({ORG}),
        project_ids=frozenset({PROJECT_A, PROJECT_B}),
        region_codes=frozenset({REGION_A, REGION_B}),
        scope_pairs=frozenset({(PROJECT_A, REGION_A), (PROJECT_B, REGION_B)}),
        organization_scope_triples=frozenset(
            {(ORG, PROJECT_A, REGION_A), (ORG, PROJECT_B, REGION_B)}
        ),
        organization_scoped_capabilities=frozenset(
            (ORG, project, capability)
            for project in (PROJECT_A, PROJECT_B)
            for capability in ("ingest_source.read", "ingest_source.manage")
        ),
    )


@pytest.fixture
def pg_robot():
    robot = "onboarding-" + uuid4().hex
    with psycopg.connect(DSN) as db:
        for project in (PROJECT_A, PROJECT_B):
            db.execute(
                """INSERT INTO registry.organization_projects
                (organization_id,project_id,display_name) VALUES (%s,%s,%s)
                ON CONFLICT DO NOTHING""",
                (ORG, project, project),
            )
        db.execute(
            """INSERT INTO collection_tasks.collection_tasks
            (collection_task_id,organization_id,project_id,dataset_id,task_code,name,
             task_type,scenario,status,create_fingerprint,upload_region_code)
            VALUES (%s,%s,%s,%s,'93000001','Onboarding regression',
                    'ROBOT_CAPTURE','regression','ACTIVE',%s,%s) ON CONFLICT DO NOTHING""",
            (TASK_A, ORG, PROJECT_A, f"dataset_{PROJECT_A.replace('-', '_')}", "c" * 64, REGION_A),
        )
        db.execute(
            """INSERT INTO robotics.robot_assets (organization_id,robot_id,display_name,
            serial_no,lifecycle_status,connectivity_state,etag,topology_revision)
            VALUES (%s,%s,%s,%s,'ACTIVE','OFFLINE','etag-1','topology-1')""",
            (ORG, robot, robot, robot),
        )
    factory = psycopg_connection_factory(DSN)
    repo = PostgresRobotIngestRepository.from_dsn(DSN, factory)
    service = RobotIngestService(
        repo,
        InMemoryObjectStorage(),
        credential_hmac_key="onboarding-postgres-test-key",
        clock=lambda: NOW,
    )
    context = bind_request_context(
        RequestContext(organization_id=ORG, project_id=PROJECT_A, region_code=REGION_A)
    )
    identity = service.create_identity(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        command=CreateRobotIngestIdentity(robot_id=robot, allowed_formats=("MCAP",)),
    ).data
    try:
        yield robot, repo, service, identity, factory
    finally:
        reset_request_context(context)


def test_pg_concurrent_credential_receipts_and_stale_policy_do_not_restore_disabled(pg_robot):
    robot, repo, service, identity, _ = pg_robot

    def ensure(index):
        context = bind_request_context(RequestContext(organization_id=ORG))
        try:
            return service.issue_credential(
                auth=_admin(),
                organization_id=ORG,
                project_id=PROJECT_A,
                ingest_identity_id=identity.ingest_identity_id,
                command=IssueCredentialCommand(idempotency_key=f"ensure-request-{index}"),
                only_if_missing=True,
            )
        finally:
            reset_request_context(context)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(ensure, range(20)))
    assert sum(result.credential is not None for result in results) == 1
    current = repo.get_identity(ORG, identity.ingest_identity_id)
    stale = current.model_copy(update={"allowed_formats": ("MCAP",)})
    token = next(result.credential.token for result in results if result.credential)
    service.authenticate(token)
    service.set_identity_state(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        ingest_identity_id=identity.ingest_identity_id,
        state=IdentityState.DISABLED,
    )
    saved = repo.save_identity(stale, changed_fields=("allowed_formats",))
    assert saved.state is IdentityState.DISABLED and saved.last_seen_at == NOW
    credential = repo.list_credentials(ORG, identity.ingest_identity_id)[0]
    service.revoke_credential(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        ingest_identity_id=identity.ingest_identity_id,
        credential_id=credential.credential_id,
    )
    service.set_identity_state(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        ingest_identity_id=identity.ingest_identity_id,
        state=IdentityState.ENABLED,
    )
    assert all(ensure(index).credential is None for index in range(20))
    assert len(repo.list_credentials(ORG, identity.ingest_identity_id)) == 1
    assert repo.onboarding_facts("other-organization") == ()
    page = robot_page(repo.onboarding_facts(ORG), NOW, query=robot)
    assert len(page.items) == 1 and page.items[0].connection_state == "ONLINE"


def legacy(db, robot, state, suffix, project=PROJECT_A, region=REGION_A):
    source = robot + suffix
    document = {
        "id": source,
        "source_type": "ROBOT",
        "administrative_state": state,
        "binding": {"kind": "ROBOT", "robot_id": robot},
        "configuration": {"kind": "ROBOT"},
    }
    db.execute(
        """INSERT INTO ingest.data_sources
        (organization_id,project_id,region_code,source_id,name,source_type,source_format,
        administrative_state,connectivity_state,credential_state,version,source_document,created_at,updated_at)
        VALUES (%s,%s,%s,%s,%s,'ROBOT','MULTI_FORMAT',%s,'UNKNOWN','MISSING',1,%s::jsonb,%s,%s)""",
        (ORG, project, region, source, source, state, json.dumps(document), NOW, NOW),
    )


def test_pg_import_rules_preserve_disabled_project_and_legacy_states(pg_robot, monkeypatch):
    robot, repo, service, identity, factory = pg_robot
    monkeypatch.setattr(configuration, "connections", lambda: factory)
    monkeypatch.setattr(mcap_processing, "connections", lambda: factory)
    with factory() as db:
        assert import_robots(db, ORG, PROJECT_A, REGION_A, robot)
    service.set_identity_state(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        ingest_identity_id=identity.ingest_identity_id,
        state=IdentityState.DISABLED,
    )
    # Operator MCAP imports do not require an enabled robot-token identity or a connector.
    assert mcap_processing.profile((ORG, PROJECT_A, REGION_A), robot)
    with factory() as db:
        legacy(db, robot, "ENABLED", "-a")
        legacy(db, robot, "DISABLED", "-b")
        legacy(db, robot, "ENABLED", "-c", PROJECT_B, REGION_B)
        assert not import_robots(db, ORG, PROJECT_A, REGION_A, robot)
        assert import_robots(db, ORG, PROJECT_B, REGION_B, robot)
        assert not import_robots(db, "other-org", PROJECT_A, REGION_A, robot)
    with pytest.raises(ProblemException) as denied:
        mcap_processing.profile((ORG, PROJECT_A, REGION_A), robot)
    assert denied.value.problem.status == 404
    facts = next(
        row
        for row in repo.onboarding_facts(ORG, legacy_scopes=((PROJECT_A, REGION_A),))
        if row.robot_id == robot
    )
    assert len(facts.legacy_sources) == 2
    assert {source.administrative_state for source in facts.legacy_sources} == {
        "ENABLED",
        "DISABLED",
    }
    assert not next(
        row for row in repo.onboarding_facts(ORG) if row.robot_id == robot
    ).legacy_sources
    with factory() as db:
        db.execute(
            """INSERT INTO robotics.project_robot_assignments
            (organization_id,project_id,region_code,robot_id,active,assigned_by,revoked_at,revoked_by)
            VALUES (%s,%s,%s,%s,false,'test',%s,'test')""",
            (ORG, PROJECT_B, REGION_B, robot, NOW),
        )
        assert not import_robots(db, ORG, PROJECT_B, REGION_B, robot)


def test_pg_success_volume_uses_commit_time_scope_and_unique_raw_receipt(pg_robot):
    robot, repo, _, _, factory = pg_robot
    start = NOW.replace(hour=16, minute=0)
    end = start + timedelta(days=1)
    with factory() as db:
        for index, (project, region, state, committed, size, fmt) in enumerate(
            [
                (PROJECT_A, REGION_A, "COMMITTED", start, 17, "MCAP"),
                (
                    PROJECT_A,
                    REGION_A,
                    "COMMITTED",
                    end - timedelta(microseconds=1),
                    23,
                    "LEROBOT_V3",
                ),
                (PROJECT_A, REGION_A, "COMMITTED", end, 97, "CAPTURE_BUNDLE"),
                (PROJECT_A, REGION_A, "INVALID", start, 101, "MCAP"),
                (PROJECT_B, REGION_B, "COMMITTED", start, 103, "MCAP"),
            ]
        ):
            key = robot + str(index)
            for _ in range(2):
                db.execute(
                    """INSERT INTO ingest.raw_sources
                    (organization_id,project_id,region_code,raw_source_id,upload_id,robot_id,
                    source_format,source_format_version,manifest_key,storage_prefix,content_hash,
                    file_count,total_bytes,raw_status,processing_status,created_at,committed_at,updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,'1',%s,%s,%s,1,%s,%s,'PENDING',%s,%s,%s)
                    ON CONFLICT DO NOTHING""",
                    (
                        ORG,
                        project,
                        region,
                        key,
                        key,
                        robot,
                        fmt,
                        key,
                        key,
                        "a" * 64,
                        size,
                        state,
                        start - timedelta(days=1),
                        committed,
                        committed,
                    ),
                )
    assert repo.successful_volume(
        organization_id=ORG,
        project_id=PROJECT_A,
        region_code=REGION_A,
        start=start,
        end=end,
        robot_id=robot,
    ) == (40, 2)
    assert repo.successful_volume(
        organization_id=ORG,
        project_id=PROJECT_A,
        region_code=REGION_B,
        start=start,
        end=end,
        robot_id=robot,
    ) == (0, 0)


def test_pg_lerobot_target_still_checks_task_dataset_region_and_status(pg_robot, monkeypatch):
    robot, _, _, _, factory = pg_robot
    monkeypatch.setattr(configuration, "connections", lambda: factory)
    monkeypatch.setattr(
        configuration,
        "processing_configuration",
        lambda *_: configuration.NativeConfiguration(configured=True),
    )
    dataset = f"dataset_{PROJECT_A.replace('-', '_')}"
    manifest = SimpleNamespace(
        processing_mode="PROCESS", dataset_id=dataset, collection_task_id=TASK_A, robot_id=robot
    )
    with factory() as db:
        db.execute(
            """INSERT INTO dataset_registry.datasets
            (organization_id,project_id,region_code,dataset_id,name,description,availability,owner_id,
            owner_display_name,asset_state,storage_class,version,dataset_document,created_at,updated_at,activity_at)
            VALUES (%s,%s,%s,%s,%s,'','ACTIVE','test','test','ACTIVE','STANDARD',
                    1,%s::jsonb,%s,%s,%s)
            ON CONFLICT DO NOTHING""",
            (
                ORG,
                PROJECT_A,
                REGION_A,
                dataset,
                dataset,
                json.dumps({"dataset_id": dataset}),
                NOW,
                NOW,
                NOW,
            ),
        )
    configuration.validate_target(ORG, PROJECT_A, REGION_A, manifest)
    for org, project, region in [
        (ORG, PROJECT_A, REGION_B),
        (ORG, PROJECT_B, REGION_A),
        ("foreign", PROJECT_A, REGION_A),
    ]:
        with pytest.raises(ProblemException):
            configuration.validate_target(org, project, region, manifest)
    manifest.dataset_id = "dataset_wrong"
    with pytest.raises(ProblemException):
        configuration.validate_target(ORG, PROJECT_A, REGION_A, manifest)
