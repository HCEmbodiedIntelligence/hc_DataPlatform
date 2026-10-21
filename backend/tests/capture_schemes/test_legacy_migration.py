"""Real database migration is additive, reviewable and repeatable without new evidence."""

from copy import deepcopy

import psycopg
import pytest

from hc_data_platform.capture_schemes import schemes
from hc_data_platform.capture_schemes.common import digest, encoded
from hc_data_platform.capture_schemes.legacy_migration import (
    apply_migration,
    migration_plan,
    source_inventory,
)
from hc_data_platform.capture_schemes.models import SchemeDraft
from hc_data_platform.core.errors import ProblemException

from .test_transactions import environment  # noqa: F401


def seed(db, org):
    document = {
        "name": "Legacy joints",
        "software": "legacy/software",
        "source_format": "MCAP",
        "configuration": {
            "collection_mode": "JOINT_ONLY",
            "time_source": "STATE_LOG_TIME",
            "fps": 30,
            "state_topic": "/state",
            "action_topic": "/action",
            "joint_names": ["left", "right"],
            "joint_units": ["rad", "rad"],
        },
    }
    for robot in ("robot-1", "robot-2"):
        db.execute(
            """INSERT INTO ingest.robot_processing_schemes
            (organization_id,robot_id,scheme_id,name,software,source_format,revision,draft)
            VALUES(%s,%s,'legacy','Legacy joints','legacy/software','MCAP',1,%s::jsonb)""",
            (org, robot, encoded(document)),
        )
        historical = {
            **document,
            "source": "INSTANCE_VERSION",
            "published_at": "2001-01-01T00:00:00+00:00",
            "validation": {"status": "PASSED", "test_fixture": True},
        }
        db.execute(
            """INSERT INTO ingest.robot_processing_versions
            (organization_id,robot_id,scheme_id,version,document)
            VALUES(%s,%s,'legacy',1,%s::jsonb)""",
            (org, robot, encoded(historical)),
        )
        db.execute(
            """INSERT INTO ingest.robot_processing_audit
            (organization_id,robot_id,scheme_id,actor,action,document)
            VALUES(%s,%s,'legacy','old-user','PUBLISH',%s::jsonb)""",
            (org, robot, encoded(historical)),
        )
    db.execute(
        "INSERT INTO ingest.robot_processing_templates (organization_id,template_id,document) "
        "VALUES(%s,'legacy-template',%s::jsonb)",
        (org, encoded(document)),
    )


def counts(db, org):
    return {
        table: db.execute(
            f"SELECT count(*) FROM ingest.{table} WHERE organization_id=%s", (org,)
        ).fetchone()[0]
        for table in (
            "capture_schemes",
            "capture_legacy_references",
            "capture_scheme_versions",
            "capture_scheme_validations",
            "capture_scheme_audit",
        )
    }


def test_legacy_migration_twice_keeps_sources_audit_and_later_shared_edits(environment):  # noqa: F811
    dsn, scope, _ = environment
    org = scope[0]
    with psycopg.connect(dsn) as db:
        seed(db, org)
        before = source_inventory(db, org)
        old_audit = db.execute(
            "SELECT to_jsonb(a) FROM ingest.robot_processing_audit a "
            "WHERE organization_id=%s ORDER BY audit_id",
            (org,),
        ).fetchall()
        plan = migration_plan(db, org)
        assert len(plan["entries"]) == 5
        assert len({entry["scheme_id"] for entry in plan["entries"]}) == 1
        assert all(entry["action"] == "IMPORT_DRAFT" for entry in plan["entries"])
        assert counts(db, org)["capture_schemes"] == 0
        first = apply_migration(db, org, plan, "migration-tester")
        assert len(first["imported"]) == 5 and not first["replayed"]
        first_counts = counts(db, org)
        assert first_counts["capture_schemes"] == 1
        assert first_counts["capture_legacy_references"] == 5
        assert (
            first_counts["capture_scheme_versions"]
            == first_counts["capture_scheme_validations"]
            == 0
        )
        assert len(apply_migration(db, org, plan, "migration-tester")["replayed"]) == 5
        assert counts(db, org) == first_counts
        shared_id = first["imported"][0]
        detail = schemes.detail(db, org, shared_id)
        schemes.save(
            db,
            org,
            shared_id,
            SchemeDraft.model_validate(
                {**detail["draft"], "expected_revision": 1, "name": "Operator reviewed draft"}
            ),
            "editor",
        )
        assert len(apply_migration(db, org, plan, "migration-tester")["replayed"]) == 5
        assert schemes.detail(db, org, shared_id)["draft"]["name"] == "Operator reviewed draft"
        assert source_inventory(db, org) == before
        assert (
            db.execute(
                "SELECT to_jsonb(a) FROM ingest.robot_processing_audit a "
                "WHERE organization_id=%s ORDER BY audit_id",
                (org,),
            ).fetchall()
            == old_audit
        )
        refs = db.execute(
            "SELECT document FROM ingest.capture_legacy_references WHERE organization_id=%s "
            "AND legacy_kind='INSTANCE_VERSION'",
            (org,),
        ).fetchall()
        assert all(
            r[0]["source"]["document"]["published_at"] == "2001-01-01T00:00:00+00:00" for r in refs
        )
        assert all(r[0]["migrated_at"] != r[0]["source"]["document"]["published_at"] for r in refs)


def test_migration_rejects_modified_plan_and_changed_source_before_any_write(environment):  # noqa: F811
    dsn, scope, _ = environment
    org = scope[0]
    with psycopg.connect(dsn) as db:
        seed(db, org)
        plan = migration_plan(db, org)
    tampered = deepcopy(plan)
    tampered["entries"][0]["candidate"]["output"]["frequency_hz"] = 15
    tampered["plan_sha256"] = digest({k: v for k, v in tampered.items() if k != "plan_sha256"})
    with (
        psycopg.connect(dsn) as db,
        pytest.raises(ProblemException, match="CAPTURE_MIGRATION_SOURCE_CHANGED"),
    ):
        apply_migration(db, org, tampered, "tester")
    with psycopg.connect(dsn) as db:
        db.execute(
            "UPDATE ingest.robot_processing_schemes SET name='changed' WHERE organization_id=%s",
            (org,),
        )
    with (
        psycopg.connect(dsn) as db,
        pytest.raises(ProblemException, match="CAPTURE_MIGRATION_SOURCE_CHANGED"),
    ):
        apply_migration(db, org, plan, "tester")
    with psycopg.connect(dsn) as db:
        assert counts(db, org)["capture_schemes"] == 0


def test_migration_does_not_guess_camera_shape_or_missing_axis_units(environment):  # noqa: F811
    dsn, scope, _ = environment
    org = scope[0]
    with psycopg.connect(dsn) as db:
        seed(db, org)
        for robot, update in (
            ("robot-1", {"cameras": [{"camera_id": "head"}]}),
            ("robot-2", {"joint_units": []}),
        ):
            db.execute(
                """UPDATE ingest.robot_processing_schemes
                SET draft=jsonb_set(draft,'{configuration}',draft->'configuration' || %s::jsonb)
                WHERE organization_id=%s AND robot_id=%s""",
                (encoded(update), org, robot),
            )
        plan = migration_plan(db, org)
        assert [entry["action"] for entry in plan["entries"]].count("NEEDS_REVIEW") == 2
        result = apply_migration(db, org, plan, "tester")
        assert len(result["needs_review"]) == 2
        assert counts(db, org)["capture_legacy_references"] == 3
