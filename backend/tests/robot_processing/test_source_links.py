"""Approved same-source links must protect legacy READY Episodes and counts."""

import pytest

from hc_data_platform.collection_tasks.captures import capture_counts, read_captures
from hc_data_platform.continuous_recordings.mcap_native import McapProcessingConfig
from hc_data_platform.ingest.raw_sources import PostgresRawSourceRepository
from hc_data_platform.lerobot_imports import mcap_processing
from hc_data_platform.lerobot_imports.source_browser import OriginalSourceBrowser
from hc_data_platform.robot_processing import migration
from hc_data_platform.security.audit import canonical_hash
from tests.continuous_recordings.test_mcap_native import synthetic_mcap
from tests.episode_store.test_native_mcap_pipeline_services import upload_robot_mcap
from tests.robot_ingest.test_raw_derivation_services import uploaded_raw  # noqa: F401

pytest_plugins = ["tests.episode_store.test_commit_services"]


@pytest.mark.integration
def test_explicit_link_is_idempotent_and_never_rewrites_legacy_episode(uploaded_raw, tmp_path):  # noqa: F811
    repository, storage, old, _, command, _ = uploaded_raw
    scope = (old.target.organization_id, old.target.project_id, old.target.region_code)
    # Other fixture organizations deliberately share this project identifier.
    # Even a database owner connection must not leak their dashboard tasks.
    from hc_data_platform.dashboard.postgres import PostgresDashboardRepository
    from hc_data_platform.dashboard.repository import DashboardScope
    from hc_data_platform.security.auth import AuthContext

    auth = AuthContext(
        subject_id="test",
        organization_ids=frozenset({scope[0]}),
        project_ids=frozenset({scope[1]}),
        region_codes=frozenset({scope[2]}),
        organization_scope_triples=frozenset({scope}),
        scope_pairs=frozenset({scope[1:]}),
        capabilities=frozenset({"dashboard.read"}),
    )
    dashboard = PostgresDashboardRepository(repository.connections)
    facts = dashboard.task_status_projection(
        auth=auth,
        scope=DashboardScope(principal_id="test", project_id=scope[1], region_code=scope[2]),
    )
    assert {t.task_id for t in facts.tasks} == {command.collection_task_id}
    content = synthetic_mcap(tmp_path / "joints.mcap", joint_only=True).read_bytes()
    failed = upload_robot_mcap(
        repository, storage, old, command, content, configure=False, queued=False
    )
    episode = "legacy-ready-protected"
    with repository.transaction() as cursor:
        cursor.execute(
            """INSERT INTO
            ingest.raw_source_episodes(organization_id,project_id,region_code,raw_source_id,episode_id,source_episode_index,status,frame_count,dataset_version,lance_version,quality_status,created_at,updated_at)
            VALUES(%s,%s,%s,%s,%s,0,'READY',12,1,1,'PASS',now(),now())""",
            (*scope, old.raw_source_id, episode),
        )
        cursor.execute(
            (
                "SELECT row_to_json(e) FROM ingest.raw_source_episodes e "
                "WHERE organization_id=%s AND episode_id=%s"
            ),
            (scope[0], episode),
        )
        before = cursor.fetchone()[0]
        cursor.execute(
            "SELECT count(*) FROM core.outbox_events WHERE organization_id=%s", (scope[0],)
        )
        events = cursor.fetchone()[0]
    link = dict(
        organization_id=scope[0],
        project_id=scope[1],
        region_code=scope[2],
        raw_source_id=failed.raw_source_id,
        canonical_episode_id=episode,
        evidence="explicit synthetic same-recording approval",
    )
    link["candidate_id"] = canonical_hash(link)
    report = dict(inventory_sha256=canonical_hash(link), source_links=[link])
    approval = dict(
        inventory_sha256=report["inventory_sha256"], source_link_ids=[link["candidate_id"]]
    )
    with repository.connections() as db:
        assert migration.apply_links(db, report, approval, "tester")[0]["result"] == "LINK_CREATED"
        assert (
            migration.apply_links(db, report, approval, "tester")[0]["result"] == "ALREADY_APPLIED"
        )
        db.commit()
    browser = OriginalSourceBrowser(storage, PostgresRawSourceRepository(repository.connections))
    raw = browser.source(*scope, failed.raw_source_id)
    with repository.connections() as db:
        mcap_processing.enqueue_files(
            db,
            scope,
            raw,
            browser.manifest(raw),
            command,
            McapProcessingConfig(
                collection_mode="JOINT_ONLY",
                time_source="STATE_LOG_TIME",
                state_topic="/joint_states",
                action_topic="/joint_cmd",
            ),
        )
        db.commit()
    with repository.transaction() as cursor:
        cursor.execute(
            "SELECT count(*) FROM core.outbox_events WHERE organization_id=%s", (scope[0],)
        )
        assert cursor.fetchone()[0] == events
        cursor.execute(
            (
                "SELECT row_to_json(e) FROM ingest.raw_source_episodes e "
                "WHERE organization_id=%s AND episode_id=%s"
            ),
            (scope[0], episode),
        )
        assert cursor.fetchone()[0] == before
        captures = read_captures(cursor, *scope, command.collection_task_id)
        counts = capture_counts(captures)
        linked = next(c for c in captures if c.upload_id == failed.upload_id)
        assert linked.canonical_episode_id == episode and linked.episode_count == 0
        assert counts.upload_count == 2 and counts.received_count == 1
        assert counts.ready_episode_count == 1 and counts.failed_count == 0
        from datetime import datetime, timezone

        from hc_data_platform.collection_tasks.capture_facts import combine_capture_facts
        from hc_data_platform.collection_tasks.models import ProgressFacts

        task_facts = combine_capture_facts(
            ProgressFacts(
                as_of=datetime.now(timezone.utc),
                received_package_count=0,
                pass_count=0,
                risk_count=0,
                reject_count=0,
                duration_observed_package_count=0,
                duration_unknown_package_count=0,
            ),
            captures,
        )
        assert task_facts.received_package_count == 1
        assert task_facts.pass_count == 1
        assert task_facts.pending_count == 0
    assert b"".join(storage.read_chunks(failed.assets[0].object_key)) == content
