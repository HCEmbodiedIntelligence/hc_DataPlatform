"""Explicit retry recovers an unresolved format, while immutable sources stay pinned."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest

from hc_data_platform.capture_schemes import plans, schemes
from hc_data_platform.capture_schemes.common import digest
from hc_data_platform.capture_schemes.models import AddParticipants, SchemeDraft, VersionReference
from hc_data_platform.capture_schemes.processing import (
    resolve_raw_snapshot,
    save_raw_snapshot,
    unresolved,
)
from hc_data_platform.capture_schemes.recovery import KIND, recover_for_retry
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.lerobot_imports.mcap_processing import record_rejection, retry_source
from hc_data_platform.robot_ingest.recording_bridge import retry

from .test_contracts import draft
from .test_entrypoint_transactions import request_scope
from .test_transactions import associate, environment, publish, selection  # noqa: F401


def seed(fixture, source_format="MCAP", *, supported=True, error="CAPTURE_FORMAT_UNSUPPORTED"):
    dsn, scope, tasks = fixture
    raw_id = "retry-" + uuid4().hex
    original = unresolved(error, "original rejection")
    with psycopg.connect(dsn) as db:
        if supported:
            definition = draft().model_dump(mode="json")
            definition["input_mode"] = "AUTO"
            saved = schemes.save(db, scope[0], "auto", SchemeDraft(**definition), "tester")
            schemes.record_validation(
                db,
                scope[0],
                "auto",
                saved["revision"],
                {
                    "source_format": "LEROBOT_V3",
                    "status": "PASSED",
                    "parser_version": "fixture",
                    "sample_sha256": "1" * 64,
                    "draft_sha256": digest(saved["draft"]),
                    "test_fixture": True,
                },
                "tester",
            )
            version = schemes.publish(db, scope[0], "auto", saved["revision"], "tester")
            ref = VersionReference(
                **{key: version[key] for key in ("scheme_id", "version", "content_sha256")}
            )
        else:
            ref = publish(db, scope[0])
        associate(db, scope[0], "robot-1", ref)
        plans.save_plan(db, scope, tasks[0], selection(ref, "robot-1"), "tester")
        db.execute(
            """INSERT INTO ingest.raw_sources
            (organization_id,project_id,region_code,raw_source_id,upload_id,dataset_id,
             collection_task_id,robot_id,source_format,source_format_version,manifest_key,
             storage_prefix,content_hash,file_count,total_bytes,raw_status,processing_status,
             created_at,committed_at,updated_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s,'robot-1',%s,'1',%s,%s,%s,1,100,
                   'COMMITTED','FAILED',now(),now(),now())""",
            (
                *scope,
                raw_id,
                raw_id,
                "dataset_capture_" + tasks[0],
                tasks[0],
                source_format,
                "raw/" + raw_id + "/manifest",
                "raw/" + raw_id,
                "a" * 64,
            ),
        )
        db.execute(
            """INSERT INTO ingest.raw_ingest_jobs
            (organization_id,project_id,region_code,raw_source_id,job_id,job_type,adapter_name,
             status,last_error_code,created_at,updated_at)
            VALUES(%s,%s,%s,%s,%s,'CONTINUOUS_RECORDING_DISCOVERY',%s,'FAILED',%s,now(),now())""",
            (
                *scope,
                raw_id,
                "job-" + raw_id,
                "mcap" if source_format == "MCAP" else "capture_bundle",
                error,
            ),
        )
        save_raw_snapshot(db, scope, raw_id, original)
    return dsn, scope, tasks[0], raw_id, original, ref


@pytest.mark.parametrize("source_format", ["MCAP", "CAPTURE_BUNDLE"])
def test_retry_pins_current_plan_once_preserving_raw_and_rejection(environment, source_format):  # noqa: F811
    dsn, scope, task, raw_id, original, ref = seed(environment, source_format)
    with psycopg.connect(dsn) as db:
        before = db.execute(
            "SELECT to_jsonb(s) FROM ingest.raw_sources s WHERE raw_source_id=%s", (raw_id,)
        ).fetchone()[0]
        assert resolve_raw_snapshot(db, scope, raw_id) == original
        recovered = recover_for_retry(db, scope, raw_id, "operator")
        assert recovered["scheme"] == ref.model_dump(mode="json")
        assert recovered["source_format"] == source_format
        assert recovered["reference_origin"] == "EXPLICIT_PROCESSING_RETRY"
        assert resolve_raw_snapshot(db, scope, raw_id) == recovered
        assert (
            db.execute(
                "SELECT document FROM ingest.raw_capture_snapshots WHERE raw_source_id=%s",
                (raw_id,),
            ).fetchone()[0]
            == original
        )
        assert (
            db.execute(
                "SELECT to_jsonb(s) FROM ingest.raw_sources s WHERE raw_source_id=%s", (raw_id,)
            ).fetchone()[0]
            == before
        )
        associate(db, scope[0], "robot-2", ref)
        plans.add_participants(
            db,
            scope,
            task,
            AddParticipants(
                expected_revision=1, participants=[{"robot_id": "robot-2", "mapping_version": 1}]
            ),
            "tester",
        )
        assert plans.get_plan(db, scope, task)["revision"] == 2
        assert recover_for_retry(db, scope, raw_id, "second-user") == recovered
        assert (
            db.execute(
                "SELECT count(*) FROM ingest.capture_legacy_references "
                "WHERE organization_id=%s AND legacy_kind=%s",
                (scope[0], KIND),
            ).fetchone()[0]
            == 1
        )
        assert db.execute(
            "SELECT actor FROM ingest.capture_scheme_audit "
            "WHERE organization_id=%s AND action='RECOVER_RAW_CONFIGURATION'",
            (scope[0],),
        ).fetchall() == [("operator",)]


def test_unsupported_current_plan_fails_without_enqueuing_or_changing_history(environment):  # noqa: F811
    dsn, scope, _, raw_id, original, _ = seed(environment, "CAPTURE_BUNDLE", supported=False)
    upload = SimpleNamespace(
        target=SimpleNamespace(organization_id=scope[0], project_id=scope[1], region_code=scope[2]),
        raw_source_id=raw_id,
        upload_id=raw_id,
        processing_config_snapshot=original,
    )
    with request_scope(scope), pytest.raises(ProblemException, match="CAPTURE_FORMAT_UNSUPPORTED"):
        retry(lambda: psycopg.connect(dsn), upload, "retry-1")
    with psycopg.connect(dsn) as db:
        assert resolve_raw_snapshot(db, scope, raw_id) == original
        assert (
            db.execute(
                "SELECT count(*) FROM core.outbox_events WHERE organization_id=%s", (scope[0],)
            ).fetchone()[0]
            == 0
        )
        assert (
            db.execute(
                "SELECT count(*) FROM ingest.capture_legacy_references WHERE organization_id=%s",
                (scope[0],),
            ).fetchone()[0]
            == 0
        )


def test_mcap_retry_creates_new_attempt_with_recovered_snapshot(environment):  # noqa: F811
    dsn, scope, task, raw_id, original, _ = seed(environment)
    with psycopg.connect(dsn) as db:
        record_rejection(
            db,
            scope,
            SimpleNamespace(raw_source_id=raw_id, dataset_id="dataset_capture_" + task),
            {
                "files": [
                    {
                        "path": "recording.mcap",
                        "size": 100,
                        "sha256": "a" * 64,
                        "object_key": "raw/" + raw_id,
                    }
                ]
            },
            SimpleNamespace(collection_task_id=task, robot_id="robot-1"),
            original,
        )
    with request_scope(scope):
        assert retry_source(scope, raw_id, lambda: psycopg.connect(dsn))
    with psycopg.connect(dsn) as db:
        rows = db.execute(
            "SELECT status,config_reference FROM ingest.mcap_processing_files "
            "WHERE raw_source_id=%s ORDER BY created_at",
            (raw_id,),
        ).fetchall()
        assert rows[0] == ("FAILED", original)
        assert rows[1][0] == "PENDING"
        assert rows[1][1] == resolve_raw_snapshot(db, scope, raw_id)
        assert (
            db.execute(
                "SELECT count(*) FROM core.outbox_events WHERE organization_id=%s", (scope[0],)
            ).fetchone()[0]
            == 1
        )


def test_concurrent_bundle_retries_share_recovery_and_dispatch(environment):  # noqa: F811
    dsn, scope, _, raw_id, original, _ = seed(environment, "CAPTURE_BUNDLE")
    upload = SimpleNamespace(
        target=SimpleNamespace(organization_id=scope[0], project_id=scope[1], region_code=scope[2]),
        raw_source_id=raw_id,
        upload_id=raw_id,
        processing_config_snapshot=original,
    )

    def retry_scoped():
        with request_scope(scope):
            retry(lambda: psycopg.connect(dsn), upload, "same-request")

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(retry_scoped) for _ in range(2)]
        for future in futures:
            future.result(timeout=15)
    with psycopg.connect(dsn) as db:
        assert "error" not in resolve_raw_snapshot(db, scope, raw_id)
        assert (
            db.execute(
                "SELECT count(*) FROM ingest.capture_legacy_references "
                "WHERE organization_id=%s AND legacy_kind=%s",
                (scope[0], KIND),
            ).fetchone()[0]
            == 1
        )
        assert (
            db.execute(
                "SELECT count(*) FROM core.outbox_events WHERE organization_id=%s", (scope[0],)
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize("case", ["scope", "integrity", "prepared"])
def test_recovery_does_not_cross_scope_or_bypass_integrity_or_prepared_data(environment, case):  # noqa: F811
    dsn, scope, _, raw_id, _, _ = seed(
        environment,
        error="CAPTURE_VERSION_MISMATCH" if case == "integrity" else "CAPTURE_FORMAT_UNSUPPORTED",
    )
    if case == "scope":
        scope = (scope[0], "another-project", scope[2])
    elif case == "prepared":
        with psycopg.connect(dsn) as db:
            db.execute(
                "UPDATE ingest.raw_sources SET derived_episode_count=1 WHERE raw_source_id=%s",
                (raw_id,),
            )
    with pytest.raises(ProblemException), psycopg.connect(dsn) as db:
        recover_for_retry(db, scope, raw_id, "operator")


def test_rollback_does_not_leave_recovery_receipt(environment):  # noqa: F811
    dsn, scope, _, raw_id, original, _ = seed(environment)
    with pytest.raises(RuntimeError), psycopg.connect(dsn) as db:
        recover_for_retry(db, scope, raw_id, "operator")
        raise RuntimeError("retry enqueue failed")
    with psycopg.connect(dsn) as db:
        assert resolve_raw_snapshot(db, scope, raw_id) == original
        assert (
            db.execute(
                "SELECT count(*) FROM ingest.capture_legacy_references WHERE organization_id=%s",
                (scope[0],),
            ).fetchone()[0]
            == 0
        )
