"""Scheme-only tasks accept compatible robots without prior task enrollment.

These use real PostgreSQL and upload services, with synthetic mapping evidence.
"""

from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from hc_data_platform.capture_schemes import associations, plans
from hc_data_platform.capture_schemes.models import AssociateScheme
from hc_data_platform.capture_schemes.references import (
    CaptureReference,
    PrepareRecording,
    SetTaskPlan,
)
from hc_data_platform.capture_schemes.web_uploads import PostgresWebUploadPlans
from hc_data_platform.collection_tasks.models import CreateCollectionTask
from hc_data_platform.core.errors import ProblemException
from tests.robot_ingest.test_robot_ingest_service import _manifest

from .test_entrypoint_transactions import request_scope, robot_service, robot_token, task_service
from .test_transactions import associate, environment, publish, selection  # noqa: F401


def preparation(recording="recording"):
    return PrepareRecording(
        expected_plan_revision=1, recording_id=recording, source_format="LEROBOT_V3"
    )


def test_scheme_only_creation_and_multiple_robots_start_without_plan_changes(environment):  # noqa: F811
    dsn, scope, _ = environment
    with psycopg.connect(dsn) as db:
        ref = publish(db, scope[0])
    # Creation succeeds even before either robot has a scheme association.
    command = CreateCollectionTask(
        name="Shared task",
        type="ROBOT_CAPTURE",
        scenario="test",
        capture_plan=SetTaskPlan(expected_revision=0, scheme=ref),
    )
    with request_scope(scope):
        task = (
            task_service(dsn)
            .create(
                organization_id=scope[0],
                project_id=scope[1],
                command=command,
                idempotency_key="open-task",
            )
            .record
        )
        replay = (
            task_service(dsn)
            .create(
                organization_id=scope[0],
                project_id=scope[1],
                command=command,
                idempotency_key="open-task",
            )
            .record
        )
        assert replay == task
    with psycopg.connect(dsn) as db:
        original = plans.get_plan(db, scope, task.collection_task_id)
        assert original["participants"] == [] and original["locked_at"] is None
        for robot in ("robot-1", "robot-2"):
            associate(db, scope[0], robot, ref)
            preview = plans.for_robot(db, scope, task.collection_task_id, robot, "LEROBOT_V3")
            assert preview["scheme"] == ref.model_dump()
        assert plans.get_plan(db, scope, task.collection_task_id) == original

    def start(robot):
        with psycopg.connect(dsn) as db:
            return plans.prepare_recording(
                db, scope, task.collection_task_id, robot, preparation(), "tester"
            )

    with ThreadPoolExecutor(2) as pool:
        snapshots = list(pool.map(start, ("robot-1", "robot-2")))
    assert {s["robot_id"] for s in snapshots} == {"robot-1", "robot-2"}
    assert {s["plan_sha256"] for s in snapshots} == {original["plan_sha256"]}
    assert all(s["scheme"] == ref.model_dump() for s in snapshots)
    assert snapshots[0]["snapshot_sha256"] != snapshots[1]["snapshot_sha256"]
    with psycopg.connect(dsn) as db:
        head = plans.get_plan(db, scope, task.collection_task_id)
        assert head["revision"] == 1 and head["participants"] == [] and head["locked_at"]

    # Both upload entrypoints use the same plan and retain each robot's identity.
    service = robot_service(dsn, scope)
    token = robot_token(dsn, scope, service)
    with request_scope(scope):
        auth = service.authenticate(token)
        item = service.connections.task(
            auth, task.collection_task_id, "LEROBOT_V3", require_processing_config=True
        )
        assert item["selectable"] and item["capture_plan"]["revision"] == 1
        assert any(
            t["collection_task_id"] == task.collection_task_id and t["selectable"]
            for t in service.connections.tasks(auth, "LEROBOT_V3")["items"]
        )
        reference = CaptureReference.model_validate(
            {k: snapshots[0][k] for k in CaptureReference.model_fields}
        )
        manifest = _manifest(
            task_id=task.collection_task_id, robot_id="robot-1", source_format="LEROBOT_V3"
        ).model_copy(update={"capture_plan": reference})
        receipt = service.create_upload(token=token, manifest=manifest)
        assert receipt.data.processing_config_snapshot == snapshots[0]
        assert service.create_upload(token=token, manifest=manifest).resumed
    web = PostgresWebUploadPlans(lambda: psycopg.connect(dsn))
    browser = web.prepare(
        scope,
        "second-robot-upload",
        dataset_id=task.dataset_id,
        task_id=task.collection_task_id,
        robot_id="robot-2",
        source_format="LEROBOT_V3",
        actor="tester",
    )
    assert browser["robot_id"] == "robot-2" and browser["scheme"] == snapshots[0]["scheme"]


@pytest.mark.parametrize("legacy", [False, True])
def test_robot_can_join_after_recording_started_without_changing_task(environment, legacy):  # noqa: F811
    dsn, scope, tasks = environment
    with psycopg.connect(dsn) as db:
        ref = publish(db, scope[0])
        associate(db, scope[0], "robot-1", ref)
        command = (
            selection(ref, "robot-1") if legacy else SetTaskPlan(expected_revision=0, scheme=ref)
        )
        original = plans.save_plan(db, scope, tasks[0], command, "tester")
        plans.prepare_recording(db, scope, tasks[0], "robot-1", preparation(), "tester")
        associate(db, scope[0], "robot-2", ref)
        snapshot = plans.prepare_recording(db, scope, tasks[0], "robot-2", preparation(), "tester")
        assert snapshot["plan_sha256"] == original["plan_sha256"]
        assert plans.get_plan(db, scope, tasks[0])["participants"] == original["participants"]


def test_new_recordings_resolve_mapping_but_retries_keep_original_snapshot(environment):  # noqa: F811
    dsn, scope, tasks = environment
    with psycopg.connect(dsn) as db:
        ref = publish(db, scope[0])
        associate(db, scope[0], "robot-1", ref)
        plans.save_plan(db, scope, tasks[0], SetTaskPlan(expected_revision=0, scheme=ref), "tester")
        first = plans.prepare_recording(db, scope, tasks[0], "robot-1", preparation(), "tester")
        associate(db, scope[0], "robot-1", ref, revision=1)
        second = plans.prepare_recording(
            db, scope, tasks[0], "robot-1", preparation("second"), "tester"
        )
        assert first["mapping_version"] == 1 and second["mapping_version"] == 2
        newer = publish(db, scope[0], revision=1, fps=30)
        associate(db, scope[0], "robot-1", newer, revision=2)
        preview = plans.for_robot(db, scope, tasks[0], "robot-1", "LEROBOT_V3")
        assert preview["scheme"] == ref.model_dump() and preview["mapping"]["mapping_version"] == 2
        associations.remove(db, scope[0], "robot-1", ref.scheme_id, 3, "tester")
        assert (
            plans.prepare_recording(db, scope, tasks[0], "robot-1", preparation(), "tester")
            == first
        )
    with (
        psycopg.connect(dsn) as db,
        pytest.raises(ProblemException, match="CAPTURE_ASSOCIATION_REQUIRED"),
    ):
        plans.prepare_recording(db, scope, tasks[0], "robot-1", preparation("third"), "tester")


@pytest.mark.parametrize(
    "problem", ["missing", "version", "unverified", "disabled", "foreign", "format"]
)
def test_open_task_still_rejects_incompatible_or_unavailable_robots(environment, problem):  # noqa: F811
    dsn, scope, tasks = environment
    with psycopg.connect(dsn) as db:
        ref = publish(db, scope[0])
        plans.save_plan(db, scope, tasks[0], SetTaskPlan(expected_revision=0, scheme=ref), "tester")
        if problem == "version":
            newer = publish(db, scope[0], revision=1, fps=30)
            associate(db, scope[0], "robot-2", newer)
        elif problem == "unverified":
            associate(db, scope[0], "robot-2", ref)
            associations.associate(
                db, scope[0], "robot-2", AssociateScheme(expected_revision=1, scheme=ref), "tester"
            )
        elif problem not in {"missing", "foreign"}:
            associate(db, scope[0], "robot-2", ref)
        if problem == "disabled":
            db.execute(
                "UPDATE robotics.robot_assets SET lifecycle_status='DISABLED' "
                "WHERE organization_id=%s AND robot_id='robot-2'",
                (scope[0],),
            )
    code = {
        "missing": "CAPTURE_ASSOCIATION_REQUIRED",
        "version": "CAPTURE_MAPPING_MISMATCH",
        "unverified": "CAPTURE_MAPPING_UNVERIFIED",
        "disabled": "CAPTURE_ROBOT_UNAVAILABLE",
        "foreign": "CAPTURE_ROBOT_UNAVAILABLE",
        "format": "CAPTURE_FORMAT_UNSUPPORTED",
    }[problem]
    with psycopg.connect(dsn) as db, pytest.raises(ProblemException, match=code):
        plans.for_robot(
            db,
            scope,
            tasks[0],
            "foreign-robot" if problem == "foreign" else "robot-2",
            "MCAP" if problem == "format" else "LEROBOT_V3",
        )
    with psycopg.connect(dsn) as db:
        assert plans.get_plan(db, scope, tasks[0])["locked_at"] is None
