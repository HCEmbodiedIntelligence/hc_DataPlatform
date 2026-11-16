from dataclasses import replace
from datetime import datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hc_data_platform.annotation.models import AnnotationTag, ReviewDecision
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.episode_store.collection_delivery_models import RequestCollectionDelivery
from hc_data_platform.episode_store.delivery_models import RequestEpisodeDelivery
from hc_data_platform.episode_store.delivery_router import router
from hc_data_platform.episode_store.upload_candidates import UploadExportFilter
from hc_data_platform.ingest.upload_batches import register
from hc_data_platform.lerobot_imports import processing
from hc_data_platform.security.http import require_auth_context
from tests.episode_store.test_annotation_bindings_services import actor, publish_schema
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_complete_delivery_services import run_job
from tests.episode_store.test_detail_services import auth_for


@pytest.mark.integration
@pytest.mark.parametrize(
    "prepared_bundle", [{"task_id": "task-" + uuid4().hex, "identity": {"dataset_id": "dataset_upload_notes", "rollout_id": "native-" + uuid4().hex}}], indirect=True
)
def test_qc_passed_batch_export_across_annotation_states(target, monkeypatch):
    packages, source, annotation, automatic, task_request = target
    service = packages.deliveries
    ref = task_request.data_ref
    scope = ref.identity.scope
    auth = auth_for(ref, annotations=False)
    auth = replace(auth, capabilities=auth.capabilities | {"export.create"})
    upload = "raw-" + uuid4().hex
    with scoped(source):
        with packages.repository.connections() as db:
            number = register(db, scope, "web", "web:test", upload)
            assert number == register(db, scope, "web", "web:test", "another-upload")
            assert number == register(db, scope, "web", "web:test", upload)
            second = register(db, scope, "oss", "oss:test", "different-upload")
            assert second == number + 1
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
                    upload,
                    upload,
                    upload,
                ),
            )
            # Shanghai midnight separates these submissions, even though UTC dates match.
            db.execute("""UPDATE ingest.upload_batches SET created_at = CASE batch_number
                WHEN %s THEN '2026-11-10 15:59:00+00'::timestamptz ELSE '2026-11-10 16:01:00+00'::timestamptz END
                WHERE (organization_id,project_id,region_code)=(%s,%s,%s)""", (number,*scope))
            db.execute("""INSERT INTO collection_tasks.collection_tasks
                (collection_task_id,organization_id,project_id,dataset_id,task_code,name,task_type,scenario,
                 create_fingerprint,upload_region_code,collection_description,upload_day_notes)
                VALUES (%s,%s,%s,%s,lpad(nextval('collection_tasks.task_code_sequence')::text,8,'0'),'采集任务','','测试',%s,%s,'料框抓取',
                '{"2026-11-10":"透明件补采","2026-11-11":"剔除的日期"}'::jsonb)""",
                (source.task_id,scope[0],scope[1],ref.identity.dataset_id,"a"*64,scope[2]))
            db.execute(
                """INSERT INTO ingest.raw_sources
                (organization_id,project_id,region_code,raw_source_id,upload_id,dataset_id,collection_task_id,source_format,
                 source_format_version,manifest_key,storage_prefix,content_hash,file_count,total_bytes,
                 raw_status,processing_status,created_at,committed_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,'LEROBOT_V3','v3',%s,%s,%s,1,10,'COMMITTED','READY',now(),now(),now())""",
                (
                    *scope,
                    upload,
                    upload,
                    ref.identity.dataset_id,
                    source.task_id,
                    upload + "/manifest",
                    upload,
                    source.source_sha256,
                ),
            )
        selection = UploadExportFilter(stage="qc_passed", batch_number=number)
        assert service.filtered_candidates(selection, auth).total == 0
        with packages.repository.connections() as db:
            db.execute(
                """INSERT INTO ingest.raw_source_episodes
                (organization_id,project_id,region_code,raw_source_id,episode_id,source_episode_index,status,
                 frame_count,dataset_version,data_ref,quality_status,qc_report_id,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,0,'READY',%s,%s,%s::jsonb,'PASS','test-report',now(),now())""",
                (
                    *scope,
                    upload,
                    ref.identity.rollout_id,
                    ref.frame_count,
                    task_request.dataset_version,
                    ref.model_dump_json(),
                ),
            )

        wrong = UploadExportFilter(stage="qc_passed", batch_number=second)
        assert service.filtered_candidates(wrong, auth).total == 0
        assert service.filtered_candidates(selection, auth).items[0].data == ref
        app = FastAPI()
        app.include_router(router)
        app.state.episode_packages = packages
        app.dependency_overrides[require_auth_context] = lambda: auth
        path = f"/projects/{scope[1]}/regions/{scope[2]}/episode-deliveries"
        with TestClient(app) as client:
            batches = client.get(path + "/upload-batches", params={"limit": 1})
            assert batches.json()[0]["batch_number"] == second
            previous = client.get(path + "/upload-batches", params={"limit": 1, "before": second})
            assert previous.json()[0]["batch_number"] == number
            first_time = previous.json()[0]["created_at"]
            second_time = batches.json()[0]["created_at"]
            query = dict(stage="qc_passed", batch_number=number, dataset_id=ref.identity.dataset_id,
                         uploaded_from=first_time, uploaded_before=second_time)
            listed = client.get(path + "/upload-candidates", params=query)
            assert listed.status_code == 200 and listed.headers["cache-control"] == "no-store"
            assert listed.json()["items"][0]["data"]["data_version_id"] == ref.data_version_id
            assert (
                client.get(
                    path + "/upload-candidates", params={**query, "task_id": "absent"}
                ).json()["total"]
                == 0
            )
        publish_schema(source, annotation)
        task = automatic.ensure_task(task_request)
        page = service.filtered_candidates(selection, auth)
        assert page.total == 1 and page.items[0].batch_numbers == (number,)

        selection = UploadExportFilter(stage="qc_passed", batch_number=number, uploaded_from=first_time, uploaded_before=second_time)
        body = RequestEpisodeDelivery(data=(ref,), export_filter=selection)
        job = service.request(body, "unannotated", auth)
        run_job(service, job)
        assert service.status(job.job_id, auth).status == "READY"
        assert service.download(job.job_id, auth).size_bytes > 0
        from hc_data_platform.episode_store.episode_bundle import build_part, bundle_files, freeze, prepare_part
        from hc_data_platform.episode_store.delivery_models import PrepareEpisodePart, DownloadEpisodeBundle
        from hc_data_platform.episode_store.models import digest
        from hc_data_platform.lerobot_imports.source_archive import stream_storage_archive
        import io
        import zipfile

        selection_many = UploadExportFilter(stage="qc_passed", batch_numbers=(number, second))
        assert service.filtered_candidates(selection_many, auth).total == 1
        assert service.filtered_candidates(UploadExportFilter(stage="qc_passed", all_batches=True), auth).total == 1
        part = PrepareEpisodePart(data=ref, mode="independent", export_filter=selection_many)
        plan = freeze(service, part, auth)
        part_id = digest(plan.model_dump(mode="json"))
        assert build_part(service, plan, part_id)  # Uses the existing READY artifact.
        ready = prepare_part(service, part, auth)
        assert ready.status == "ready" and ready.reused and ready.part_id == part_id
        bundle = DownloadEpisodeBundle(part_ids=(part_id,), export_filter=selection_many)
        from hc_data_platform.episode_store.collection_records import record_document
        bundle_records = {}
        parts = bundle_files(service, bundle, auth, records=bundle_records)
        assert parts[0].key == service.repository.get_job(job.job_id)["artifact_key"]
        outer = zipfile.ZipFile(io.BytesIO(b"".join(stream_storage_archive(service.storage, parts, record_document(bundle_records)))))
        assert len(outer.namelist()) == 2
        import json
        from hc_data_platform.episode_store.collection_records import raw_records

        with zipfile.ZipFile(io.BytesIO(outer.read(parts[0].path))) as inner:
            record = json.loads(inner.read("collection_records.json"))["records"][ref.data_version_id]
        assert json.loads(outer.read("collection_records.json"))["records"][parts[0].path] == record
        assert record["collection_description"] == "料框抓取"
        assert record["uploads"] == [dict(uploaded_on="2026-11-10", batch_numbers=[number], note="透明件补采")]
        with packages.repository.connections() as db, db.cursor() as cursor:
            assert raw_records(cursor, scope, [upload], selection)[upload].model_dump(mode="json") == record
        collection = RequestCollectionDelivery(
            members=({"data": ref},), modalities="rgbd", export_filter=selection
        )
        grouped = service.request_collection(collection, "grouped", auth)
        assert grouped.plan.members[0].annotation is None
        assert grouped.plan.members[0].collection_record.model_dump(mode="json") == record
        run_job(service, grouped)
        archive_key = service.repository.get_job(grouped.job_id)["artifact_key"]
        with zipfile.ZipFile(io.BytesIO(b"".join(service.storage.read_chunks(archive_key)))) as archive:
            assert json.loads(archive.read("collection_records.json"))["records"][ref.data_version_id] == record
        with packages.repository.connections() as db:
            register(db, scope, "oss", "oss:test", upload)
            db.execute(
                """INSERT INTO ingest.raw_ingest_jobs
                (organization_id,project_id,region_code,raw_source_id,job_id,workflow_id,job_type,
                 adapter_name,status,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,'LEROBOT_IMPORT','lerobot_v3','SUCCEEDED',now(),now())""",
                (*scope, upload, upload, upload),
            )
        monkeypatch.setattr(processing, "connections", lambda: packages.repository.connections)
        time_window = dict(uploaded_from=datetime.fromisoformat(first_time), uploaded_before=datetime.fromisoformat(second_time))
        assert [row.import_id for row in processing.list_progress(*scope, **time_window)] == [upload]
        assert processing.list_progress(*scope, batch_number=second, **time_window) == []
        assert service.filtered_candidates(wrong, auth).items[0].batch_numbers == (number, second)
        # A re-submitted upload must match the time and number on the same submission.
        assert service.filtered_candidates(
            UploadExportFilter(stage="qc_passed", batch_number=second, uploaded_before=second_time), auth
        ).total == 0
        assert service.filtered_candidates(
            UploadExportFilter(stage="qc_passed", uploaded_from=second_time), auth
        ).items[0].batch_numbers == (second,)
        missing = wrong.model_copy(update={"batch_number": second + 1})
        with pytest.raises(ProblemException, match="SELECTION_CHANGED"):
            service.request(body.model_copy(update={"export_filter": missing}), "wrong-batch", auth)
        with packages.repository.connections() as db:
            db.execute(
                "UPDATE ingest.raw_source_episodes SET quality_status='REJECT' WHERE raw_source_id=%s",
                (upload,),
            )
        assert service.filtered_candidates(selection, auth).total == 0
        with pytest.raises(ProblemException, match="SELECTION_CHANGED"):
            service.request_collection(collection, "failed-qc", auth)
        with packages.repository.connections() as db:
            db.execute(
                "UPDATE ingest.raw_source_episodes SET quality_status='PASS' WHERE raw_source_id=%s",
                (upload,),
            )
        claimed = annotation.claim(task.task_id, actor(ref.identity))
        annotation.save_draft(
            task.task_id,
            actor(ref.identity),
            (),
            expected_revision=0,
            tags=(
                AnnotationTag(
                    annotation_id="tag",
                    tag_id="label",
                    path=("label",),
                    label="test",
                    start_step=0,
                    end_step=2,
                ),
            ),
            if_match=claimed.etag,
            client_mutation_id="saved",
        )
        assert service.filtered_candidates(selection, auth).total == 1
        assert service.request_collection(collection, "draft", auth).job_id == grouped.job_id
        submitted = annotation.submit_for_review(
            task.task_id,
            actor(ref.identity),
            expected_revision=1,
            if_match=annotation.get_task(task.task_id).etag,
            idempotency_key="submit",
        )
        assert service.filtered_candidates(selection, auth).total == 1
        annotation.review(
            task.task_id,
            actor(ref.identity, "other-reviewer"),
            ReviewDecision.APPROVE,
            revision=1,
            submission_id=submitted.submission_id,
            if_match=annotation.get_task(task.task_id).etag,
            comment="Test approval",
        )
        assert service.filtered_candidates(selection, auth).items[0].data == ref
        assert service.request(body, "approved", auth).job_id == job.job_id
        legacy_filter = selection.model_copy(update={"stage": "unannotated"})
        assert service.filtered_candidates(legacy_filter, auth).total == 1
        with packages.repository.connections() as db:
            db.execute(
                "UPDATE ingest.raw_source_episodes SET quality_status='REJECT' WHERE raw_source_id=%s",
                (upload,),
            )
        assert service.filtered_candidates(selection, auth).total == 0
        with pytest.raises(ProblemException, match="SELECTION_CHANGED"):
            service.request(body, "approved-failed-qc", auth)
        with pytest.raises(ProblemException, match="SELECTION_CHANGED"):
            bundle_files(service, bundle, auth)
        with pytest.raises(ProblemException, match="SELECTION_CHANGED"):
            service.download(job.job_id, auth, selection)
        with pytest.raises(ProblemException, match="SELECTION_CHANGED"):
            prepare_part(service, part, auth)
