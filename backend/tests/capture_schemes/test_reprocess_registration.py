"""Real MCAP preparation and PostgreSQL registration across two task plans."""

import hashlib
import json
from types import SimpleNamespace

import psycopg
import pytest

from hc_data_platform.capture_schemes.reprocess import ReprocessMcap, request
from hc_data_platform.continuous_recordings.mcap_contracts import McapProcessingConfig
from hc_data_platform.continuous_recordings.registration import PostgresRecordingRegistration
from hc_data_platform.core.events import DomainEventEnvelope
from hc_data_platform.lerobot_imports import mcap_processing as worker
from hc_data_platform.lerobot_imports.mcap_original import ORIGINAL_PATH
from hc_data_platform.object_storage.memory import InMemoryObjectStorage
from hc_data_platform.robot_ingest.recording_bridge import scope_context

from .test_mcap_v3 import NAMES, source
from .test_transactions import environment  # noqa: F401
from .test_versioned_reprocess import seed


@pytest.mark.parametrize("old_receipt", [False, True], ids=["new-plan", "retry-old-failure"])
def test_new_plan_registers_its_own_recording(environment, tmp_path, monkeypatch, old_receipt):  # noqa: F811
    dsn, scope, task, raw, _, original = seed(environment, validate_new_mapping=False)
    store = InMemoryObjectStorage()
    inputs = {
        "raw.mcap": source(tmp_path / "raw.mcap", [NAMES] * 6).read_bytes(),
        "raw.takeovers.json": json.dumps(
            {
                "schema": "hc-dagger-takeovers/v1",
                "tag": "dagger",
                "clock": "ros",
                "recording": "raw.mcap",
                "complete": True,
                "recording_start_ns": 0,
                "intervals": [{"start_ns": 10**12, "end_ns": 10**12 + 2_000_000}],
            }
        ).encode(),
    }
    files = []
    for path, data in inputs.items():
        key = f"raw/{scope[0]}/{raw.raw_source_id}/{path}"
        store.objects[key] = data
        files.append(
            dict(path=path, object_key=key, size=len(data), sha256=hashlib.sha256(data).hexdigest())
        )
    manifest = {"files": files}

    def factory():
        return psycopg.connect(dsn)

    handler = SimpleNamespace(
        connections=factory, registrations=PostgresRecordingRegistration(), storage=store
    )

    def deliver(ident, attempt=0):
        with factory() as db:
            (envelope,) = db.execute(
                """SELECT envelope FROM core.outbox_events WHERE organization_id=%s
                   AND envelope->'payload'->>'mcap_processing_id'=%s
                   AND (envelope->'payload'->>'attempt')::integer=%s""",
                (scope[0], ident, attempt),
            ).fetchone()
        with scope_context(*scope):
            worker.process_file(handler, DomainEventEnvelope.model_validate(envelope), lambda: None)

    def attachment(ident):
        with factory() as db:
            return db.execute(
                """SELECT r.recording_id, r.recording_upload_id, a.object_key, a.expected_sha256,
                          r.status, r.recording_document FROM ingest.continuous_recordings r
                   JOIN ingest.recording_upload_assets a ON
                     (a.organization_id,a.project_id,a.region_code,a.upload_id) =
                     (r.organization_id,r.project_id,r.region_code,r.recording_upload_id)
                   WHERE r.organization_id=%s AND r.recording_id=%s AND a.asset_path=%s""",
                (scope[0], "mcap-" + ident[:40], ORIGINAL_PATH),
            ).fetchone()

    with factory() as db:
        worker.enqueue_files(
            db,
            scope,
            raw,
            manifest,
            SimpleNamespace(collection_task_id=task, robot_id="robot-1"),
            McapProcessingConfig.model_validate(original["effective_adapter"]["configuration"]),
            reference=original,
        )
        (first,) = db.execute(
            "SELECT processing_id FROM ingest.mcap_processing_files "
            "WHERE raw_source_id=%s AND status='PENDING'",
            (raw.raw_source_id,),
        ).fetchone()
    deliver(first)
    historic = attachment(first)
    assert historic[2] == files[0]["object_key"]

    with factory() as db:
        result = request(
            db, scope, raw, manifest, ReprocessMcap(request_id="upgrade", plan_revision=2), "tester"
        )
        second = result["files"][0]["processing_id"]
    prefix = "/".join(("derived-mcap", *scope, second))
    if old_receipt:
        # A historical failure may already have persisted a receipt with the shared Raw key.
        def interrupt_registration(*args):
            raise RuntimeError("simulated registration interruption")
        with monkeypatch.context() as old:
            old.setattr(handler.registrations, "register", interrupt_registration)
            with pytest.raises(RuntimeError, match="registration interruption"):
                deliver(second)
        receipt = store.objects[prefix + "/receipt.json"]
        assert attachment(second) is None
        assert worker.retry_source(scope, raw.raw_source_id, factory)
        with factory() as db:
            pending = db.execute(
                "SELECT processing_id,attempts,source_document FROM "
                "ingest.mcap_processing_files WHERE raw_source_id=%s "
                "AND status='PENDING'",
                (raw.raw_source_id,),
            ).fetchall()
            assert [(ident, attempt) for ident, attempt, _ in pending] == [(second, 1)]
            assert pending[0][2]["takeover_file"] == files[1]
        old_objects = set(store.objects)
        deliver(second, 1)
        assert store.objects[prefix + "/receipt.json"] == receipt
        assert set(store.objects) == old_objects
    else:
        deliver(second)
    updated = attachment(second)
    assert historic == attachment(first)
    assert updated[0] != historic[0] and updated[1] != historic[1]
    assert updated[2] == historic[2] == files[0]["object_key"]
    assert updated[3] == historic[3] == files[0]["sha256"]
    assert store.objects[updated[2]] == store.objects[historic[2]] == inputs["raw.mcap"]
    assert updated[4] == "PENDING_QC"
    with factory() as db:
        (evidence,) = db.execute(
            "SELECT object_key FROM ingest.recording_upload_assets "
            "WHERE upload_id=%s AND asset_path='mcap-provenance.json'",
            (updated[1],),
        ).fetchone()
    assert json.loads(store.objects[evidence])["takeover_intervals_ns"] == [
        [10**12, 10**12 + 2_000_000]
    ]
    deliver(second, int(old_receipt))
    assert attachment(second) == updated
    verify_slice_reuse(factory, scope, store, historic[0], updated[0], already_waiting=old_receipt)


def verify_slice_reuse(factory, scope, storage, first, second, *, already_waiting):
    from hc_data_platform.continuous_recordings.asset_repository import (
        PostgresRecordingAssetRepository,
    )
    from hc_data_platform.continuous_recordings.models import RecordingScope, SaveSliceDraftCommand
    from hc_data_platform.continuous_recordings.quality import RecordingQualityService
    from hc_data_platform.continuous_recordings.repository import (
        PostgresContinuousRecordingRepository,
    )
    from hc_data_platform.continuous_recordings.service import ContinuousRecordingService
    from hc_data_platform.security.auth import AuthContext

    org, project, region = scope
    target = RecordingScope(organization_id=org, project_id=project, region_code=region)
    auth = AuthContext(
        subject_id="test-operator",
        organization_ids=frozenset({org}),
        project_ids=frozenset({project}),
        region_codes=frozenset({region}),
        scope_pairs=frozenset({(project, region)}),
        organization_scope_triples=frozenset({scope}),
        capabilities=frozenset({"upload.read", "upload.manage"}),
    )
    access = dict(auth=auth, **target.model_dump())
    actor = dict(actor_id=auth.subject_id, request_id="reuse-test")
    repo = PostgresContinuousRecordingRepository(factory)
    service = ContinuousRecordingService(
        repo, SimpleNamespace(storage=storage), PostgresRecordingAssetRepository(factory)
    )
    report = {
        "status": "PASS",
        "finding_codes": [],
        "warning_codes": [],
        "quality_report": {"findings": []},
    }
    qc = RecordingQualityService(
        factory,
        SimpleNamespace(resolve_recording=lambda **kwargs: None),
        SimpleNamespace(inspect_quality=lambda *a, **kw: report),
        storage=storage,
    )

    def pass_quality(rec_id):
        rec = repo.get(target, rec_id)
        request = {
            **target.model_dump(),
            "recording_id": rec_id,
            "source_sha256": rec.source_sha256,
            "workflow_id": rec.quality_workflow_id,
        }
        assert qc.run(request)["status"] == "PASS"

    with scope_context(*scope):
        # This recovery test exercises historical manually confirmed slices.
        with factory() as db:
            db.execute("UPDATE ingest.continuous_recordings SET recording_document="
                       "recording_document || %s::jsonb "
                       "WHERE organization_id=%s AND recording_id IN (%s,%s)",
                       (json.dumps({"ingest_mode": "SLICE_FIRST"}), org, first, second))
        pass_quality(first)
        if already_waiting:
            pass_quality(second)
            assert repo.get(target, second).current_revision == 0
        old = repo.get(target, first)
        saved = service.save_draft(
            **access,
            **actor,
            recording_id=first,
            if_match=old.etag,
            command=SaveSliceDraftCommand(
                slices=[
                    dict(
                        episode_id="episode_cut1",
                        start_offset_ns=10_520_832,
                        end_offset_ns=100_000_000,
                        title="动作一",
                        task_labels=["测试"],
                        notes="保留备注",
                    ),
                    dict(
                        episode_id="episode_removed",
                        start_offset_ns=120_000_000,
                        end_offset_ns=180_000_000,
                    ),
                ]
            ),
        )
        prior = service.finalize(
            **access,
            **actor,
            recording_id=first,
            if_match=saved.recording.etag,
            expected_draft_revision=saved.revision.revision,
        )
        with factory() as db:
            db.execute(
                "UPDATE ingest.recording_episode_processing SET status='DISCARDED' "
                "WHERE organization_id=%s AND recording_id=%s AND episode_id='episode_removed'",
                (org, first),
            )

        if already_waiting:
            detail = service.get(**access, recording_id=second)
            assert detail.data.current_revision == 0 and detail.current_slice_revision is None
            from datetime import timedelta

            from hc_data_platform.continuous_recordings.slice_reuse import previous_confirmed

            with factory() as db, db.cursor() as cursor:
                for change in (
                    {"current_revision": 1},
                    {
                        "capture_started_at": detail.data.capture_started_at
                        + timedelta(microseconds=1)
                    },
                    {"quality_summary": None},
                ):
                    assert (
                        previous_confirmed(cursor, detail.data.model_copy(update=change), storage)
                        is None
                    )

            assert [ep.episode_id for ep in detail.reusable_slices.slices] == ["episode_cut1"]
            resumed = service.finalize(
                **access,
                **actor,
                recording_id=second,
                if_match=detail.data.etag,
                expected_draft_revision=0,
            )
            assert (
                service.finalize(
                    **access,
                    **actor,
                    recording_id=second,
                    if_match=detail.data.etag,
                    expected_draft_revision=0,
                )
                == resumed
            )
        else:
            pass_quality(second)
            pass_quality(second)

        rec = repo.get(target, second)
        assert rec.status.value == "SLICED" and rec.finalized_revision == 1
        rev = repo.get_revision(target, second, 1)
        assert (
            rev.reused_from.recording_id == first
            and rev.reused_from.revision == prior.revision.revision
        )
        (new,) = rev.slices
        original, _ = prior.revision.slices
        assert (
            new.episode_id,
            new.start_offset_ns,
            new.end_offset_ns,
            new.title,
            new.task_labels,
            new.notes,
        ) == ("episode_cut1", 10_520_832, 100_000_000, "动作一", ("测试",), "保留备注")
        assert new.source_sha256 == rec.source_sha256 != original.source_sha256
        assert (
            new.source_recording_upload_id
            == rec.recording_upload_id
            != original.source_recording_upload_id
        )
        with factory() as db:
            rows = db.execute(
                "SELECT status, workflow_id FROM ingest.recording_episode_processing "
                "WHERE organization_id=%s AND recording_id=%s",
                (org, second),
            ).fetchall()
            assert len(rows) == 1 and rows[0][0] == "PENDING_QC"
            assert db.execute(
                "SELECT count(*) FROM core.outbox_events "
                "WHERE organization_id=%s AND envelope->'payload'->>'workflow_id'=%s",
                (org, rows[0][1]),
            ).fetchone() == (1,)
            assert db.execute(
                "SELECT DISTINCT upload_id FROM ingest.recording_episode_asset_windows "
                "WHERE organization_id=%s AND recording_id=%s",
                (org, second),
            ).fetchall() == [(rec.recording_upload_id,)]
