"""Appending participants preserves fixed semantics and prior recording receipts."""

import psycopg
import pytest

from hc_data_platform.capture_schemes import associations, plans, schemes
from hc_data_platform.capture_schemes.models import AssociateScheme
from hc_data_platform.capture_schemes.references import AddParticipants, PrepareRecording
from hc_data_platform.core.errors import ProblemException

from .test_transactions import associate, environment, publish, selection  # noqa: F401


def test_add_after_start_retains_lock_old_mapping_and_snapshot_with_idempotent_replay(environment):  # noqa: F811
    dsn, scope, tasks = environment
    with psycopg.connect(dsn) as db:
        reference = publish(db, scope[0])
        associate(db, scope[0], "robot-1", reference)
        associate(db, scope[0], "robot-2", reference)
        plans.save_plan(db, scope, tasks[0], selection(reference, "robot-1"), "tester")
        prepared = PrepareRecording(
            expected_plan_revision=1, recording_id="old-recording", source_format="LEROBOT_V3"
        )
        original = plans.prepare_recording(db, scope, tasks[0], "robot-1", prepared, "tester")
        before = plans.get_plan(db, scope, tasks[0])
    command = AddParticipants(
        expected_revision=1, participants=[{"robot_id": "robot-2", "mapping_version": 1}]
    )
    with psycopg.connect(dsn) as db:
        after = plans.add_participants(db, scope, tasks[0], command, "tester")
        assert after["revision"] == 2 and after["locked_at"] == before["locked_at"]
        assert after["scheme"] == before["scheme"]
        assert after["output_contract_sha256"] == before["output_contract_sha256"]
        assert after["participants"][0] == before["participants"][0]
        assert plans.add_participants(db, scope, tasks[0], command, "tester") == after
        assert (
            plans.prepare_recording(db, scope, tasks[0], "robot-1", prepared, "tester") == original
        )
        second = plans.prepare_recording(
            db,
            scope,
            tasks[0],
            "robot-2",
            PrepareRecording(
                expected_plan_revision=2, recording_id="new-recording", source_format="LEROBOT_V3"
            ),
            "tester",
        )
        assert second["plan_revision"] == 2 and second["scheme"] == original["scheme"]
        assert (
            db.execute(
                "SELECT count(*) FROM collection_tasks.capture_plans "
                "WHERE organization_id=%s AND collection_task_id=%s",
                (scope[0], tasks[0]),
            ).fetchone()[0]
            == 2
        )
    with psycopg.connect(dsn) as db, pytest.raises(ProblemException) as exc:
        plans.add_participants(
            db,
            scope,
            tasks[0],
            AddParticipants(
                expected_revision=2, participants=[{"robot_id": "robot-1", "mapping_version": 2}]
            ),
            "tester",
        )
    assert exc.value.problem.code == "CAPTURE_PARTICIPANT_EXISTS"
    with psycopg.connect(dsn) as db, pytest.raises(ProblemException) as exc:
        plans.save_plan(db, scope, tasks[0], selection(reference, "robot-2", revision=2), "tester")
    assert exc.value.problem.code == "CAPTURE_PLAN_LOCKED"


def test_addition_requires_verified_mapping_and_unarchived_scheme(environment):  # noqa: F811
    dsn, scope, tasks = environment
    with psycopg.connect(dsn) as db:
        reference = publish(db, scope[0])
        associate(db, scope[0], "robot-1", reference)
        associations.associate(
            db,
            scope[0],
            "robot-2",
            AssociateScheme(expected_revision=0, scheme=reference),
            "tester",
        )
        plans.save_plan(db, scope, tasks[0], selection(reference, "robot-1"), "tester")
        plans.prepare_recording(
            db,
            scope,
            tasks[0],
            "robot-1",
            PrepareRecording(
                expected_plan_revision=1, recording_id="recording", source_format="LEROBOT_V3"
            ),
            "tester",
        )
    command = AddParticipants(
        expected_revision=1, participants=[{"robot_id": "robot-2", "mapping_version": 1}]
    )
    with psycopg.connect(dsn) as db, pytest.raises(ProblemException) as exc:
        plans.add_participants(db, scope, tasks[0], command, "tester")
    assert exc.value.problem.code == "CAPTURE_MAPPING_UNVERIFIED"
    with psycopg.connect(dsn) as db:
        schemes.archive(db, scope[0], reference.scheme_id, 1, "tester")
    with psycopg.connect(dsn) as db, pytest.raises(ProblemException) as exc:
        plans.add_participants(db, scope, tasks[0], command, "tester")
    assert exc.value.problem.code == "CAPTURE_SCHEME_ARCHIVED"
    with psycopg.connect(dsn) as db:
        assert plans.get_plan(db, scope, tasks[0])["revision"] == 1
        assert (
            plans.for_robot(db, scope, tasks[0], "robot-1", "LEROBOT_V3")["scheme"]
            == reference.model_dump()
        )
