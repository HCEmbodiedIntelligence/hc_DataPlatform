from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.episode_store.models import DataVersionRef
from hc_data_platform.episode_store.traceback import members, origins, router, suggestions
from hc_data_platform.episode_store.export_file_names import record_files
from hc_data_platform.ingest.upload_batches import register
from hc_data_platform.security.http import require_auth_context
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_detail_services import auth_for


@pytest.mark.integration
def test_trace_exact_members_repeated_submission_and_missing_episode(services, package):
    repo, _ = services
    _, source = package
    source = source.model_copy(
        update={
            "task_id": "task-" + uuid4().hex,
            "identity": source.identity.model_copy(
                update={
                    "rollout_id": "trace-" + uuid4().hex,
                    "dataset_id": "dataset_" + uuid4().hex,
                }
            ),
        }
    )
    packages = SimpleNamespace(deliveries=SimpleNamespace(repository=repo))
    ref = DataVersionRef(
        identity=source.identity,
        data_version_id="ev_trace",
        version=1,
        content_hash="c" * 64,
        schema_snapshot_id=source.schema_snapshot_id,
        package_prefix="test/trace",
        frame_count=3,
    )
    scope = ref.identity.scope
    upload = "u-" + uuid4().hex
    raw = "source-" + uuid4().hex
    with scoped(source):
        with repo.connections() as db:
            db.execute(
                """INSERT INTO registry.organization_projects (organization_id,
                    project_id,display_name) VALUES (%s,%s,'trace')""",
                scope[:2],
            )
            db.execute(
                """INSERT INTO episode_store.bindings (organization_id,project_id,
                    region_code,dataset_id,episode_id,rollout_id) VALUES (%s,%s,%s,%s,%s,%s)""",
                (*ref.identity.key, ref.identity.rollout_id),
            )
            db.execute(
                """INSERT INTO episode_store.versions
                        (organization_id,project_id,region_code,dataset_id,
                            episode_id,data_version_id,version,idempotency_key,
                         input_document,content_hash,status,receipt_document,committed_at)
                        VALUES (%s,%s,%s,%s,%s,%s,1,%s,%s::jsonb,%s,
                            'COMMITTED',jsonb_build_object('data',%s::jsonb),now())""",
                (
                    *ref.identity.key,
                    ref.data_version_id,
                    "b" * 64,
                    source.model_dump_json(),
                    ref.content_hash,
                    ref.model_dump_json(),
                ),
            )
            db.execute(
                """INSERT INTO episode_store.collections
                        (organization_id,project_id,region_code,dataset_id,collection_version_id,content_hash,document,version)
                        VALUES (%s,%s,%s,%s,'cv_trace',%s,'{}',1)""",
                (*scope, ref.identity.dataset_id, ref.content_hash),
            )
            db.execute(
                """INSERT INTO episode_store.collection_publications
                        (organization_id,project_id,region_code,dataset_id,data_version_id,collection_version_id)
                        VALUES (%s,%s,%s,%s,%s,'cv_trace')""",
                (*scope, ref.identity.dataset_id, ref.data_version_id),
            )
        assert origins(repo, scope, ref.data_version_id)[0].batches == ()
        with repo.connections() as db:
            first = register(db, scope, "web", "trace-first", upload)
            register(db, scope, "web", "trace-first", "unfinished")
            second = register(db, scope, "oss", "trace-second", upload)
            register(db, scope, "oss", "trace-second", "other-batch-only")
            db.execute(
                """INSERT INTO ingest.raw_sources
                   (organization_id,project_id,region_code,raw_source_id,upload_id,dataset_id,
                    collection_task_id,source_format,source_format_version,manifest_key,storage_prefix,
                    content_hash,file_count,total_bytes,raw_status,processing_status,created_at,committed_at,updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,'LEROBOT_V3','v3',%s,%s,%s,1,10,'COMMITTED',
                           'READY',now(),now(),now())""",
                (
                    *scope,
                    raw,
                    upload,
                    ref.identity.dataset_id,
                    source.task_id,
                    raw + "/manifest",
                    raw,
                    source.source_sha256,
                ),
            )
            db.execute(
                """INSERT INTO ingest.collection_jobs
                (organization_id,project_id,region_code,task_id,collection_job_id,robot_id,status,created_at,updated_at)
                VALUES (%s,%s,%s,'task',%s,'robot','COMPLETED',now(),now())""",
                (*scope, upload),
            )
            db.execute(
                """INSERT INTO ingest.rollouts
                (organization_id,project_id,region_code,collection_job_id,rollout_id,sequence_no,robot_id,
                 source_sha256,status,created_at,updated_at,collection_session_id,recording_request_id,data_package_id)
                VALUES (%s,%s,%s,%s,%s,1,'robot',%s,'RAW_VERIFIED',now(),now(),%s,%s,%s)""",
                (
                    *scope,
                    upload,
                    ref.identity.rollout_id,
                    source.source_sha256,
                    raw,
                    upload,
                    upload,
                ),
            )
            db.execute(
                """INSERT INTO ingest.raw_source_episodes
                   (organization_id,project_id,region_code,raw_source_id,episode_id,source_episode_index,
                    status,frame_count,dataset_version,data_ref,quality_status,created_at,updated_at)
                   VALUES (%s,%s,%s,%s,%s,0,'READY',%s,%s,%s::jsonb,'PASS',now(),now())""",
                (
                    *scope,
                    raw,
                    ref.identity.rollout_id,
                    ref.frame_count,
                    1,
                    ref.model_dump_json(),
                ),
            )
            db.execute(
                """INSERT INTO collection_tasks.collection_tasks
                   (collection_task_id,organization_id,project_id,dataset_id,task_code,name,task_type,scenario,
                    create_fingerprint,upload_region_code,collection_description,upload_day_notes)
                   VALUES (%s,%s,%s,%s,lpad(nextval('collection_tasks.task_code_sequence')::text,
                       8,'0'),
                           'trace','','test',%s,%s,'采集说明',
                           jsonb_build_object(to_char(now()
                               AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD'),'当日备注'))""",
                (source.task_id, *scope[:2], ref.identity.dataset_id, "a" * 64, scope[2]),
            )
        name = "teleop_20261117_a1b2c3.mcap"
        record_files(repo, [("collection/" + name, ref.data_version_id)] * 2)
        assert suggestions(repo, scope, "1117_A1") == [name]
        assert not suggestions(repo, scope, "%")
        assert not suggestions(repo, (*scope[:2], "another-region"), "1117_A1")
        for lookup in (
            name.upper(),
            ref.data_version_id,
            ref.identity.episode_id,
            ref.identity.rollout_id,
            upload,
            raw,
        ):
            [seed] = origins(repo, scope, lookup)
            assert [b.batch_number for b in seed.batches] == [first, second]
        auth = auth_for(ref, annotations=False)
        auth = replace(auth, capabilities=auth.capabilities | {"episode.read", "upload.read"})
        app = FastAPI()
        app.include_router(router)
        app.state.episode_packages = packages
        app.dependency_overrides[require_auth_context] = lambda: auth
        with TestClient(app) as client:
            url = f"/projects/{scope[1]}/regions/{scope[2]}/data-trace/batches/{first}"
            response = client.get(url, params={"limit": 1})
            assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
            first_page = response.json()
            assert (first_page["upload_count"], first_page["total"]) == (2, 2)
            rows = (
                first_page["items"]
                + client.get(url, params={"limit": 1, "offset": 1}).json()["items"]
            )
            by_id = {row["upload_id"]: row for row in rows}
            assert set(by_id) == {upload, "unfinished"}
            assert by_id["unfinished"]["data"] is None
            row = by_id[upload]
            assert row["data"] == ref.model_dump(mode="json")
            assert row["viewer_version_id"].startswith("version_")
            assert (row["collection_description"], row["upload_note"], row["raw_source_ids"]) == (
                "采集说明",
                "当日备注",
                [raw],
            )
            granted = auth
            for capability in ("dataset_version.read", "upload.read", "episode.read"):
                auth = replace(granted, capabilities=granted.capabilities - {capability})
                with pytest.raises(ProblemException) as exc:
                    client.get(url)
                assert exc.value.problem.status == 403
        other = (*scope[:2], "another-region")
        assert not origins(repo, other, ref.data_version_id)
        assert members(repo, other, first, 0, 25).total == 0
