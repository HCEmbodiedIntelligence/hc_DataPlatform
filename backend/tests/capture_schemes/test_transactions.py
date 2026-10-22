"""Real PostgreSQL concurrency/tenant tests; validation evidence here is synthetic."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest

from hc_data_platform.capture_schemes import associations, plans, schemes
from hc_data_platform.capture_schemes.common import digest
from hc_data_platform.capture_schemes.models import (
    AssociateScheme,
    PrepareRecording,
    SchemeDraft,
    SetTaskPlan,
    VersionReference,
)
from hc_data_platform.core.errors import ProblemException

from .test_contracts import draft


@pytest.fixture
def environment():
    dsn = os.environ.get("ROBOT_CONFIG_TEST_DSN")
    if not dsn:
        pytest.skip("explicit isolated PostgreSQL DSN required")
    org = "capture-" + uuid4().hex
    scope = (org, "p", "global")
    tasks = [uuid4().hex for _ in range(3)]
    with psycopg.connect(dsn) as db:
        db.execute(
            "INSERT INTO registry.organization_projects VALUES(%s,'p','Capture tests')", (org,)
        )
        for robot in ("robot-1", "robot-2"):
            db.execute(
                """INSERT INTO robotics.robot_assets
                (organization_id,robot_id,display_name,serial_no,lifecycle_status,
                connectivity_state,etag,topology_revision)
                VALUES(%s,%s,%s,%s,'ACTIVE','OFFLINE','1','1')""",
                (org, robot, robot, robot),
            )
        for task in tasks:
            db.execute(
                """INSERT INTO collection_tasks.collection_tasks
                (organization_id,project_id,collection_task_id,task_code,name,task_type,scenario,
                create_fingerprint,dataset_id) VALUES(%s,'p',%s,
                lpad(nextval('collection_tasks.task_code_sequence')::text,8,'0'),
                'capture','test','test',%s,%s)""",
                (org, task, "a" * 64, "dataset_capture_" + task),
            )
    return dsn, scope, tasks


def publish(db, org, name="joints", revision=0, *, fps=15):
    value = schemes.save(db, org, name, draft(revision, fps=fps), "tester")
    evidence = {
        "source_format": "LEROBOT_V3",
        "parser_version": "lerobot-v3/1",
        "sample_sha256": "1" * 64,
        "draft_sha256": digest(value["draft"]),
        "status": "PASSED",
        "test_fixture": True,
    }
    schemes.record_validation(db, org, name, value["revision"], evidence, "tester")
    result = schemes.publish(db, org, name, value["revision"], "tester")
    return VersionReference.model_validate(
        {
            k: result[k]
            for k in (
                "scheme_id",
                "version",
                "content_sha256",
            )
        }
    )


def associate(db, org, robot, reference, *, revision=0, make_default=True):
    values = associations.associate(
        db,
        org,
        robot,
        AssociateScheme(
            expected_revision=revision,
            scheme=reference,
            make_default=make_default,
        ),
        "tester",
    )
    value = next(v for v in values if v["scheme_id"] == reference.scheme_id)
    associations.record_validation(
        db,
        org,
        robot,
        reference.scheme_id,
        value["mapping_version"],
        {
            "source_format": "LEROBOT_V3",
            "status": "PASSED",
            "sample_robot_id": robot,
            "sample_sha256": "2" * 64,
            "mapping_sha256": value["mapping"]["content_sha256"],
            "parser_version": "lerobot-v3/1",
            "test_fixture": True,
        },
        "tester",
    )
    return value["mapping_version"]


def selection(reference, *robots, revision=0):
    return SetTaskPlan(
        expected_revision=revision,
        scheme=reference,
        participants=[{"robot_id": robot, "mapping_version": 1} for robot in robots],
    )


def test_automatic_scheme_uses_one_real_sample_without_fabricating_other_format_evidence(
    environment,
):
    dsn, scope, tasks = environment
    org = scope[0]
    with psycopg.connect(dsn) as db:
        value = draft().model_dump(mode="json")
        value["input_mode"] = "AUTO"
        saved = schemes.save(db, org, "auto", SchemeDraft.model_validate(value), "tester")
        with pytest.raises(ProblemException):
            schemes.publish(db, org, "auto", 1, "tester")
        schemes.record_validation(
            db,
            org,
            "auto",
            1,
            {
                "source_format": "LEROBOT_V3",
                "parser_version": "lerobot-v3/1",
                "sample_sha256": "1" * 64,
                "draft_sha256": digest(saved["draft"]),
                "status": "PASSED",
                "test_fixture": True,
            },
            "tester",
        )
        published = schemes.publish(db, org, "auto", 1, "tester")
        assert set(published["validations"]) == {"LEROBOT_V3"}
        reference = VersionReference.model_validate(
            {k: published[k] for k in ("scheme_id", "version", "content_sha256")}
        )
        associate(db, org, "robot-1", reference)
        plans.save_plan(db, scope, tasks[0], selection(reference, "robot-1"), "tester")
        for fmt in ("MCAP", "CAPTURE_BUNDLE", "LEROBOT_V3"):
            result = plans.prepare_recording(
                db,
                scope,
                tasks[0],
                "robot-1",
                PrepareRecording(
                    expected_plan_revision=1, recording_id="auto-" + fmt, source_format=fmt
                ),
                "tester",
            )
            assert result["source_format"] == fmt
        link = associations.list_links(db, org, "robot-1")[0]
        assert link["status"] == "READY"
        assert link["validated_formats"] == ["LEROBOT_V3"]


@pytest.mark.parametrize("change_existing", [False, True])
def test_locked_plan_can_add_format_without_changing_old_recordings(environment, change_existing):
    dsn, scope, tasks = environment
    org = scope[0]
    with psycopg.connect(dsn) as db:
        ref = publish(db, org)
        associate(db, org, "robot-1", ref)
        plans.save_plan(db, scope, tasks[0], selection(ref, "robot-1"), "tester")
        command = PrepareRecording(
            expected_plan_revision=1, recording_id="before", source_format="LEROBOT_V3"
        )
        original = plans.prepare_recording(db, scope, tasks[0], "robot-1", command, "tester")
        definition = draft(1).model_dump(mode="json")
        if change_existing:
            definition["adapters"][0]["feature_sources"] = {"action": "different.action"}
        definition["adapters"].append(
            {
                "source_format": "MCAP",
                "configuration": {
                    "collection_mode": "JOINT_ONLY",
                    "time_source": "STATE_LOG_TIME",
                    "fps": 15,
                    "state_topic": "/state",
                    "action_topic": "/command",
                    "semantic_output": definition["output"],
                    "state_joint_names": ["left", "right"],
                    "state_joint_units": ["rad", "rad"],
                    "action_joint_names": ["right", "left"],
                    "action_joint_units": ["rad", "rad"],
                },
            }
        )
        saved = schemes.save(db, org, "joints", SchemeDraft.model_validate(definition), "tester")
        for adapter in saved["draft"]["adapters"]:
            schemes.record_validation(
                db,
                org,
                "joints",
                saved["revision"],
                {
                    "source_format": adapter["source_format"],
                    "status": "PASSED",
                    "parser_version": adapter["parser_version"],
                    "sample_sha256": "1" * 64,
                    "draft_sha256": digest(saved["draft"]),
                    "test_fixture": True,
                },
                "tester",
            )
        version = schemes.publish(db, org, "joints", saved["revision"], "tester")
        upgraded = VersionReference.model_validate({k: version[k] for k in ref.model_fields})
        current = associations.list_links(db, org, "robot-1")[0]
        linked = associations.associate(
            db,
            org,
            "robot-1",
            AssociateScheme(
                expected_revision=current["revision"],
                scheme=upgraded,
            ),
            "tester",
        )[0]
        for adapter in version["adapters"]:
            associations.record_validation(
                db,
                org,
                "robot-1",
                "joints",
                linked["mapping_version"],
                {
                    "source_format": adapter["source_format"],
                    "status": "PASSED",
                    "parser_version": adapter["parser_version"],
                    "sample_sha256": "1" * 64,
                    "sample_robot_id": "robot-1",
                    "mapping_sha256": linked["mapping"]["content_sha256"],
                    "test_fixture": True,
                },
                "tester",
            )
        selection2 = SetTaskPlan(
            expected_revision=1,
            scheme=upgraded,
            participants=[
                {
                    "robot_id": "robot-1",
                    "mapping_version": linked["mapping_version"],
                }
            ],
        )
        if change_existing:
            with pytest.raises(ProblemException, match="CAPTURE_PLAN_LOCKED"):
                plans.save_plan(db, scope, tasks[0], selection2, "tester")
            assert plans.get_plan(db, scope, tasks[0])["revision"] == 1
        else:
            updated = plans.save_plan(db, scope, tasks[0], selection2, "tester")
            assert updated["revision"] == 2 and updated["locked_at"] is not None
            assert updated["output"] == original["output"]
            assert (
                plans.for_robot(db, scope, tasks[0], "robot-1", "MCAP")["scheme"]
                == upgraded.model_dump()
            )
        assert (
            plans.prepare_recording(db, scope, tasks[0], "robot-1", command, "tester") == original
        )


def test_cross_instance_reuse_independent_tasks_and_immutable_snapshots(environment):
    dsn, scope, tasks = environment
    org = scope[0]
    with psycopg.connect(dsn) as db:
        ref = publish(db, org)
        associate(db, org, "robot-1", ref)
        associate(db, org, "robot-2", ref)
        other = publish(db, org, "other", fps=30)
        associate(db, org, "robot-1", other)
        first = plans.save_plan(db, scope, tasks[0], selection(ref, "robot-1", "robot-2"), "tester")
        plans.save_plan(db, scope, tasks[1], selection(other, "robot-1"), "tester")
        assert len(first["participants"]) == 2
        assert len(schemes.detail(db, org, "joints")["versions"]) == 1
        assert plans.get_plan(db, scope, tasks[0])["locked_at"] is None
        assert (
            plans.for_robot(db, scope, tasks[0], "robot-1", "LEROBOT_V3")["scheme"]
            == ref.model_dump()
        )
        assert plans.get_plan(db, scope, tasks[0])["locked_at"] is None
        snapshot = plans.prepare_recording(
            db,
            scope,
            tasks[0],
            "robot-1",
            PrepareRecording(
                expected_plan_revision=1,
                recording_id="recording-1",
                source_format="LEROBOT_V3",
            ),
            "tester",
        )
        assert snapshot["output"]["frequency_hz"] == 15
        assert plans.get_plan(db, scope, tasks[0])["locked_at"] is not None
        ref2 = publish(db, org, revision=1, fps=20)
        associate(db, org, "robot-1", ref2, revision=2)
        assert schemes.publish(db, org, "joints", 1, "tester")["version"] == 1
        assert (
            plans.for_robot(db, scope, tasks[0], "robot-1", "LEROBOT_V3")["scheme"]
            == ref.model_dump()
        )
        assert (
            plans.prepare_recording(
                db,
                scope,
                tasks[0],
                "robot-1",
                PrepareRecording(
                    expected_plan_revision=1,
                    recording_id="recording-1",
                    source_format="LEROBOT_V3",
                ),
                "tester",
            )
            == snapshot
        )
    with psycopg.connect(dsn) as db, pytest.raises(ProblemException, match="CAPTURE_PLAN_LOCKED"):
        plans.save_plan(db, scope, tasks[0], selection(other, "robot-1", revision=1), "tester")
    with psycopg.connect(dsn) as db, pytest.raises(psycopg.errors.RaiseException):
        db.execute(
            "UPDATE ingest.capture_scheme_versions SET document='{}' WHERE organization_id=%s",
            (org,),
        )


def test_archive_remove_do_not_break_locked_tasks_but_block_new_use(environment):
    dsn, scope, tasks = environment
    org = scope[0]
    with psycopg.connect(dsn) as db:
        ref = publish(db, org)
        associate(db, org, "robot-1", ref)
        plans.save_plan(db, scope, tasks[0], selection(ref, "robot-1"), "tester")
        plans.prepare_recording(
            db,
            scope,
            tasks[0],
            "robot-1",
            PrepareRecording(
                expected_plan_revision=1,
                recording_id="recording-1",
                source_format="LEROBOT_V3",
            ),
            "tester",
        )
        associations.remove(db, org, "robot-1", "joints", 1, "tester")
        schemes.archive(db, org, "joints", 1, "tester")
        result = plans.prepare_recording(
            db,
            scope,
            tasks[0],
            "robot-1",
            PrepareRecording(
                expected_plan_revision=1,
                recording_id="recording-2",
                source_format="LEROBOT_V3",
            ),
            "tester",
        )
        assert result["scheme"]["version"] == 1
    with (
        psycopg.connect(dsn) as db,
        pytest.raises(ProblemException, match="CAPTURE_SCHEME_ARCHIVED"),
    ):
        plans.save_plan(db, scope, tasks[1], selection(ref, "robot-1"), "tester")
    with (
        psycopg.connect(dsn) as db,
        pytest.raises(ProblemException, match="CAPTURE_VERSION_MISMATCH"),
    ):
        schemes.version(db, "foreign", ref)


def test_start_racing_plan_change_has_one_consistent_winner(environment):
    dsn, scope, tasks = environment
    with psycopg.connect(dsn) as db:
        ref = publish(db, scope[0])
        other = publish(db, scope[0], "other", fps=30)
        associate(db, scope[0], "robot-1", ref)
        associate(db, scope[0], "robot-1", other, make_default=False)
        plans.save_plan(db, scope, tasks[0], selection(ref, "robot-1"), "tester")

    def start_or_change(start):
        try:
            with psycopg.connect(dsn) as db:
                if start:
                    return plans.prepare_recording(
                        db,
                        scope,
                        tasks[0],
                        "robot-1",
                        PrepareRecording(
                            expected_plan_revision=1,
                            recording_id="race",
                            source_format="LEROBOT_V3",
                        ),
                        "tester",
                    )
                return plans.save_plan(
                    db, scope, tasks[0], selection(other, "robot-1", revision=1), "tester"
                )
        except ProblemException as exc:
            return exc.problem.code

    with ThreadPoolExecutor(2) as workers:
        results = list(workers.map(start_or_change, (True, False)))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert any(
        r in ("CAPTURE_REVISION_CONFLICT", "CAPTURE_PLAN_LOCKED")
        for r in results
        if isinstance(r, str)
    )
    with psycopg.connect(dsn) as db:
        head = plans.get_plan(db, scope, tasks[0])
        assert (head["revision"], bool(head["locked_at"])) in {(1, True), (2, False)}


def test_stale_validation_cannot_publish_and_lost_publication_is_idempotent(environment):
    dsn, scope, _ = environment
    org = scope[0]
    with psycopg.connect(dsn) as db:
        ref = publish(db, org)
        schemes.save(db, org, "joints", draft(1, fps=20), "tester")
        assert schemes.publish(db, org, "joints", 1, "tester")["version"] == ref.version
    with (
        psycopg.connect(dsn) as db,
        pytest.raises(
            ProblemException,
            match="CAPTURE_SAMPLE_VALIDATION_REQUIRED",
        ),
    ):
        schemes.publish(db, org, "joints", 2, "tester")


def test_two_tasks_cannot_claim_incompatible_structure_in_same_dataset(environment):
    dsn, scope, tasks = environment
    with psycopg.connect(dsn) as db:
        ref = publish(db, scope[0])
        other = publish(db, scope[0], "other", fps=30)
        associate(db, scope[0], "robot-1", ref)
        associate(db, scope[0], "robot-1", other, make_default=False)
        db.execute(
            "UPDATE collection_tasks.collection_tasks SET dataset_id=%s "
            "WHERE organization_id=%s AND collection_task_id=%s",
            ("dataset_capture_" + tasks[0], scope[0], tasks[1]),
        )
        plans.save_plan(db, scope, tasks[0], selection(ref, "robot-1"), "tester")
    with (
        psycopg.connect(dsn) as db,
        pytest.raises(ProblemException, match="CAPTURE_DATASET_INCOMPATIBLE"),
    ):
        plans.save_plan(db, scope, tasks[1], selection(other, "robot-1"), "tester")
