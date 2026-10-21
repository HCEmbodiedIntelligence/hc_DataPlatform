"""Real transaction boundaries through task and robot application entrypoints."""

from contextlib import contextmanager
from uuid import uuid4

import psycopg
import pytest

from hc_data_platform.capture_schemes import plans
from hc_data_platform.capture_schemes.postgres import PostgresRecordingPlans
from hc_data_platform.capture_schemes.references import CaptureReference, PrepareRecording
from hc_data_platform.collection_tasks.models import CreateCollectionTask
from hc_data_platform.collection_tasks.postgres import PostgresCollectionTaskRepository
from hc_data_platform.collection_tasks.service import CollectionTaskService
from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.object_storage.memory import InMemoryObjectStorage
from hc_data_platform.robot_ingest.models import CreateRobotIngestIdentity, IssueCredentialCommand
from hc_data_platform.robot_ingest.repository import PostgresRobotIngestRepository
from hc_data_platform.robot_ingest.service import RobotIngestService
from hc_data_platform.security.auth import AuthContext
from tests.robot_ingest.test_robot_ingest_service import _manifest

from .test_transactions import associate, environment, publish, selection  # noqa: F401


@contextmanager
def request_scope(scope):
    token = bind_request_context(
        RequestContext(
            organization_id=scope[0],
            project_id=scope[1],
            region_code=scope[2],
            subject_id="capture-integration-test",
        )
    )
    try:
        yield
    finally:
        reset_request_context(token)


def task_service(dsn):
    return CollectionTaskService(PostgresCollectionTaskRepository(psycopg_connection_factory(dsn)))


def seed_scheme(dsn, scope):
    with psycopg.connect(dsn) as db:
        ref = publish(db, scope[0])
        associate(db, scope[0], "robot-1", ref)
    return ref


def create_task(dsn, scope, ref, *, key="create", region=None):
    context_scope = (*scope[:2], region or scope[2])
    command = CreateCollectionTask(
        name="Captured task",
        type="ROBOT_CAPTURE",
        scenario="integration",
        capture_plan=selection(ref, "robot-1"),
    )
    with request_scope(context_scope):
        return (
            task_service(dsn)
            .create(
                organization_id=scope[0],
                project_id=scope[1],
                command=command,
                idempotency_key=key,
            )
            .record
        )


def robot_service(dsn, scope, *, connection_factory=None, storage=None):
    factory = connection_factory or psycopg_connection_factory(dsn)
    return RobotIngestService(
        PostgresRobotIngestRepository.from_dsn(dsn, factory),
        storage or InMemoryObjectStorage(),
        credential_hmac_key="capture-integration-test-key",
        capture_plans=PostgresRecordingPlans(psycopg_connection_factory(dsn)),
    )


def robot_token(dsn, scope, service):
    with psycopg.connect(dsn) as db:
        db.execute(
            """INSERT INTO registry.robot_models
            (organization_id,model_id,manufacturer,model_code,display_name)
            VALUES(%s,'test-model','test','test-model','Test model')""",
            (scope[0],),
        )
        db.execute(
            """INSERT INTO registry.robot_model_versions
            (organization_id,version_id,robot_model_id,version_label,lifecycle,
             asset_availability,publish_readiness,etag)
            VALUES(%s,'test-model-v1','test-model','v1','PUBLISHED','AVAILABLE','READY','test')""",
            (scope[0],),
        )
        db.execute(
            """UPDATE robotics.robot_assets SET robot_model_version_id='test-model-v1',
            binding_id=gen_random_uuid(),binding_valid_from=now(),binding_etag='test'
            WHERE organization_id=%s AND robot_id='robot-1'""",
            (scope[0],),
        )
    admin = AuthContext(
        subject_id="capture-integration-test",
        organization_ids=frozenset({scope[0]}),
        project_ids=frozenset({scope[1]}),
        region_codes=frozenset({scope[2]}),
        organization_scope_triples=frozenset({scope}),
        organization_scoped_capabilities=frozenset(
            (scope[0], scope[1], c) for c in ("ingest_source.manage", "ingest_source.read")
        ),
    )
    with request_scope(scope):
        identity = service.create_identity(
            auth=admin,
            organization_id=scope[0],
            project_id=scope[1],
            command=CreateRobotIngestIdentity(robot_id="robot-1", allowed_formats=("LEROBOT_V3",)),
        ).data
        return service.issue_credential(
            auth=admin,
            organization_id=scope[0],
            project_id=scope[1],
            ingest_identity_id=identity.ingest_identity_id,
            command=IssueCredentialCommand(),
        ).credential.token


def test_task_and_plan_commit_once_and_invalid_plan_leaves_no_dataset(environment):  # noqa: F811
    dsn, scope, _ = environment
    ref = seed_scheme(dsn, scope)
    first = create_task(dsn, scope, ref, region="cn-east")
    replay = create_task(dsn, scope, ref, region="cn-east")
    assert replay == first
    with psycopg.connect(dsn) as db:
        plan = plans.get_plan(db, (*scope[:2], "cn-east"), first.collection_task_id)
        assert plan["dataset_id"] == first.dataset_id and plan["locked_at"] is None
        assert db.execute(
            "SELECT region_code FROM dataset_registry.datasets "
            "WHERE organization_id=%s AND dataset_id=%s",
            (scope[0], first.dataset_id),
        ).fetchall() == [("cn-east",)]
        before = db.execute(
            "SELECT count(*) FROM collection_tasks.collection_tasks WHERE organization_id=%s",
            (scope[0],),
        ).fetchone()[0]
    invalid = ref.model_copy(update={"content_sha256": "f" * 64})
    with pytest.raises(ProblemException):
        create_task(dsn, scope, invalid, key="invalid")
    with psycopg.connect(dsn) as db:
        assert (
            db.execute(
                "SELECT count(*) FROM collection_tasks.collection_tasks WHERE organization_id=%s",
                (scope[0],),
            ).fetchone()[0]
            == before
        )
        assert (
            db.execute(
                "SELECT count(*) FROM dataset_registry.datasets WHERE organization_id=%s",
                (scope[0],),
            ).fetchone()[0]
            == 1
        )
        assert (
            db.execute(
                "SELECT count(*) FROM collection_tasks.capture_plans WHERE organization_id=%s",
                (scope[0],),
            ).fetchone()[0]
            == 1
        )
    # The rejected key was not poisoned; corrected input can complete normally.
    assert (
        create_task(dsn, scope, ref, key="invalid").collection_task_id != first.collection_task_id
    )


def test_recording_snapshot_is_read_only_until_start_and_upload_replays_after_changes(environment):  # noqa: F811
    dsn, scope, _ = environment
    ref = seed_scheme(dsn, scope)
    task = create_task(dsn, scope, ref)
    service = robot_service(dsn, scope)
    token = robot_token(dsn, scope, service)
    command = PrepareRecording(
        expected_plan_revision=1, recording_id="recording-1", source_format="LEROBOT_V3"
    )
    with request_scope(scope):
        auth = service.authenticate(token)
        preview = service.connections.task(
            auth, task.collection_task_id, "LEROBOT_V3", require_processing_config=True
        )
        assert preview["capture_plan"]["scheme"] == ref.model_dump()
        service.connections.tasks(auth, "LEROBOT_V3")
        with psycopg.connect(dsn) as db:
            assert plans.get_plan(db, scope, task.collection_task_id)["locked_at"] is None
            assert (
                db.execute(
                    "SELECT count(*) FROM ingest.capture_recording_snapshots "
                    "WHERE organization_id=%s",
                    (scope[0],),
                ).fetchone()[0]
                == 0
            )
        snapshot = service.connections.prepare_recording(auth, task.collection_task_id, command)
        reference = CaptureReference.model_validate(
            {k: snapshot[k] for k in CaptureReference.model_fields}
        )
        manifest = _manifest(
            task_id=task.collection_task_id, robot_id="robot-1", source_format="LEROBOT_V3"
        ).model_copy(update={"capture_plan": reference})
        receipt = service.create_upload(token=token, manifest=manifest)
        assert receipt.data.processing_config_snapshot == snapshot
        with psycopg.connect(dsn) as db:
            newer = publish(db, scope[0], revision=1)
            associate(db, scope[0], "robot-1", newer, revision=1)
            db.execute(
                "UPDATE collection_tasks.collection_tasks SET status='CLOSED' "
                "WHERE organization_id=%s AND collection_task_id=%s",
                (scope[0], task.collection_task_id),
            )
        assert (
            service.connections.prepare_recording(auth, task.collection_task_id, command)
            == snapshot
        )
        replay = service.create_upload(token=token, manifest=manifest)
        assert replay.resumed and replay.data.processing_config_snapshot == snapshot


def test_bad_reference_preserves_upload_permission_without_locking_task(environment):  # noqa: F811
    dsn, scope, _ = environment
    ref = seed_scheme(dsn, scope)
    task = create_task(dsn, scope, ref)
    service = robot_service(dsn, scope)
    token = robot_token(dsn, scope, service)
    bad = CaptureReference(
        plan_revision=1, plan_sha256="a" * 64, snapshot_sha256="b" * 64, recording_id="spoofed"
    )
    manifest = _manifest(
        task_id=task.collection_task_id, robot_id="robot-1", source_format="LEROBOT_V3"
    ).model_copy(update={"capture_plan": bad})
    with request_scope(scope):
        receipt = service.create_upload(token=token, manifest=manifest)
        assert receipt.data.processing_config_snapshot["error"] == "CAPTURE_VERSION_MISMATCH"
        assert receipt.data.processing_config_snapshot["retryable"] is False
    with psycopg.connect(dsn) as db:
        assert plans.get_plan(db, scope, task.collection_task_id)["locked_at"] is None
        assert (
            db.execute(
                "SELECT count(*) FROM ingest.robot_ingest_uploads WHERE organization_id=%s",
                (scope[0],),
            ).fetchone()[0]
            == 1
        )


def test_upload_commit_failure_rolls_back_snapshot_and_plan_lock(environment):  # noqa: F811
    dsn, scope, _ = environment
    ref = seed_scheme(dsn, scope)
    task = create_task(dsn, scope, ref)
    service = robot_service(dsn, scope)
    token = robot_token(dsn, scope, service)
    manifest = _manifest(
        task_id=task.collection_task_id,
        robot_id="robot-1",
        source_format="LEROBOT_V3",
        client_upload_id=str(uuid4()),
    )
    # Fail in the actual database after the upload transaction has prepared its
    # compatibility snapshot. Other organizations/operations are unaffected.
    trigger = "capture_test_" + uuid4().hex
    with psycopg.connect(dsn) as db:
        db.execute(
            psycopg.sql.SQL(
                "CREATE FUNCTION public.{}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN IF NEW.organization_id = {} THEN "
                "RAISE EXCEPTION 'synthetic upload persistence failure'; "
                "END IF; RETURN NEW; END $$"
            ).format(psycopg.sql.Identifier(trigger), psycopg.sql.Literal(scope[0]))
        )
        db.execute(
            psycopg.sql.SQL(
                "CREATE TRIGGER {} BEFORE INSERT ON ingest.robot_ingest_assets "
                "FOR EACH ROW EXECUTE FUNCTION public.{}()"
            ).format(psycopg.sql.Identifier(trigger), psycopg.sql.Identifier(trigger))
        )
    try:
        with (
            request_scope(scope),
            pytest.raises(
                psycopg.errors.RaiseException, match="synthetic upload persistence failure"
            ),
        ):
            service.create_upload(token=token, manifest=manifest)
        with psycopg.connect(dsn) as db:
            assert plans.get_plan(db, scope, task.collection_task_id)["locked_at"] is None
            for table in (
                "ingest.capture_recording_snapshots",
                "ingest.robot_ingest_uploads",
                "episode_store.dataset_capture_contracts",
            ):
                assert (
                    db.execute(
                        psycopg.sql.SQL("SELECT count(*) FROM {} WHERE organization_id=%s").format(
                            psycopg.sql.Identifier(*table.split("."))
                        ),
                        (scope[0],),
                    ).fetchone()[0]
                    == 0
                )
    finally:
        with psycopg.connect(dsn) as db:
            db.execute(
                psycopg.sql.SQL("DROP TRIGGER {} ON ingest.robot_ingest_assets").format(
                    psycopg.sql.Identifier(trigger)
                )
            )
            db.execute(
                psycopg.sql.SQL("DROP FUNCTION public.{}()").format(psycopg.sql.Identifier(trigger))
            )
    with request_scope(scope):
        receipt = service.create_upload(token=token, manifest=manifest)
        assert receipt.data.processing_config_snapshot["scheme"] == ref.model_dump()
        assert (
            receipt.data.processing_config_snapshot["reference_origin"]
            == "SERVER_TASK_PLAN_COMPATIBILITY"
        )
