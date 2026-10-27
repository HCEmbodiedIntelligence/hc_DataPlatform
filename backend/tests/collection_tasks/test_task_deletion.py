"""Permanent deletion exercises real immutable packages, DB fences and object storage."""

import json
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from hc_data_platform.collection_tasks.deletion import PostgresTaskDeletion, TaskObjectPurger
from hc_data_platform.collection_tasks.deletion_plan import TABLES, locators, registered_locators
from hc_data_platform.collection_tasks.models import CreateCollectionTask
from hc_data_platform.collection_tasks.postgres import PostgresCollectionTaskRepository
from hc_data_platform.collection_tasks.service import CollectionTaskService
from hc_data_platform.core.errors import ProblemException
from tests.collection_tasks.test_task_deletion_storage import alignment_row
from tests.episode_store.test_commit_services import EpisodePackageCommitter, scoped

pytest_plugins = ["tests.episode_store.test_commit_services"]


@pytest.mark.integration
def test_task_deletion_graph_tracks_schema_dependencies_and_writer_fences(services, package):
    repository, _ = services
    with scoped(package[1]), repository.connections() as connection:
        dependencies = connection.execute("""SELECT
            nc.nspname||'.'||c.relname,np.nspname||'.'||p.relname
            FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
            JOIN pg_namespace nc ON nc.oid=c.relnamespace
            JOIN pg_class p ON p.oid=k.confrelid
            JOIN pg_namespace np ON np.oid=p.relnamespace WHERE k.contype='f'""").fetchall()
        missing = {(child, parent) for child, parent in dependencies
                   if parent in TABLES and child not in TABLES}
        assert not missing, f"Review task ownership for new dependent tables: {missing}"
        # Some capture snapshots intentionally have no FK. Their task/Dataset
        # identity must still be reviewed when the schema adds another table.
        owned = connection.execute("""SELECT DISTINCT n.nspname||'.'||c.relname
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid
            WHERE c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped
              AND a.attname IN ('collection_task_id','raw_source_id','recording_id','dataset_id')
              AND n.nspname NOT IN ('pg_catalog','information_schema')""").fetchall()
        retained_tombstones = {"collection_tasks.deletions", "collection_tasks.deletion_targets"}
        assert {row[0] for row in owned} <= set(TABLES) | retained_tombstones
        fenced = connection.execute("""SELECT n.nspname||'.'||c.relname
            FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE t.tgname='collection_task_deletion_fence' AND t.tgenabled='O'""").fetchall()
        assert set(TABLES) <= {row[0] for row in fenced}


def test_unrelated_external_storage_does_not_block_scoped_object_reference_check():
    document = {"staging_uri": "file:///tmp/unrelated.arrow", "object_key": "raw/shared/file"}
    assert locators(document, "bucket", ignore_external=True) == ({"raw/shared/file"}, set())
    with pytest.raises(ProblemException) as blocked:
        locators(document, "bucket")
    assert blocked.value.problem.code == "TASK_DELETE_EXTERNAL_STORAGE"


def test_task_metadata_cannot_supply_deletion_locators():
    assert registered_locators(
        "dataset_registry.datasets",
        {
            "dataset_document": {
                "object_key": "raw/other/org/keep.mcap",
                "object_prefix": "raw/other/org/",
            }
        },
        "bucket",
    ) == (set(), set())


def seed_capture_dependencies(connection, scope, task, raw_id, scheme_id, robot_id, key):
    org, project, region = scope
    identity = (org, project, task.collection_task_id)
    raw_scope = (*scope, raw_id)
    dataset_scope = (*scope, task.dataset_id)
    connection.execute("""INSERT INTO collection_tasks.capture_plans
        (organization_id,project_id,collection_task_id,region_code,revision,
         scheme_id,scheme_version,document,content_sha256)
        VALUES (%s,%s,%s,%s,1,%s,1,'{}',%s)""", (*identity, region, scheme_id, "a" * 64))
    connection.execute("""INSERT INTO collection_tasks.capture_plan_heads
        (organization_id,project_id,collection_task_id,revision)
        VALUES (%s,%s,%s,1)""", identity)
    connection.execute("""INSERT INTO ingest.capture_recording_snapshots
        (organization_id,robot_id,recording_id,project_id,region_code,collection_task_id,
         plan_revision,document,content_sha256)
        VALUES (%s,%s,%s,%s,%s,%s,1,'{}',%s)""",
        (org, robot_id, raw_id, project, region, task.collection_task_id, "a" * 64))
    connection.execute("""INSERT INTO ingest.raw_sources
        (organization_id,project_id,region_code,raw_source_id,upload_id,dataset_id,
         collection_task_id,robot_id,source_format,source_format_version,manifest_key,
         storage_prefix,content_hash,file_count,total_bytes,raw_status,processing_status,
         created_at,committed_at,updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'MCAP','1',%s,%s,%s,1,2,
                'COMMITTED','READY',now(),now(),now())""",
        (*raw_scope, raw_id, task.dataset_id, task.collection_task_id, robot_id,
         key, key.rsplit("/", 1)[0] + "/", "a" * 64))
    connection.execute("""INSERT INTO ingest.raw_capture_snapshots
        (organization_id,project_id,region_code,raw_source_id,document,content_sha256)
        VALUES (%s,%s,%s,%s,'{}',%s)""", (*raw_scope, "a" * 64))
    connection.execute("""INSERT INTO ingest.capture_reprocess_requests
        (organization_id,project_id,region_code,raw_source_id,request_id,
         plan_revision,document,content_hash)
        VALUES (%s,%s,%s,%s,'retry',1,'{}',%s)""", (*raw_scope, "a" * 64))
    connection.execute("""INSERT INTO ingest.recording_source_links
        (organization_id,project_id,region_code,raw_source_id,canonical_episode_id,evidence)
        VALUES (%s,%s,%s,%s,'episode-1','{}')""", raw_scope)
    connection.execute("""INSERT INTO ingest.robot_task_data_requirements
        (organization_id,project_id,collection_task_id,requires_rgb,actor)
        VALUES (%s,%s,%s,false,'test')""", identity)
    connection.execute("""INSERT INTO episode_store.dataset_capture_contracts
        (organization_id,project_id,region_code,dataset_id,contract_sha256,document)
        VALUES (%s,%s,%s,%s,%s,'{}')""", (*dataset_scope, "a" * 64))
    connection.execute("""INSERT INTO episode_store.dataset_capture_formats
        (organization_id,project_id,region_code,dataset_id,format_version,content_hash,document)
        VALUES (%s,%s,%s,%s,'format-1',%s,'{}')""", (*dataset_scope, "a" * 64))
    connection.execute("""INSERT INTO episode_store.dataset_schema_versions
        (organization_id,project_id,region_code,dataset_id,schema_snapshot_id,content_hash,document)
        VALUES (%s,%s,%s,%s,'schema-1',%s,'{}')""", (*dataset_scope, "a" * 64))


@pytest.mark.integration
def test_package_delete_is_scoped_resumable_and_fences_stale_writers(
    services, package, tmp_path, monkeypatch,
):
    repository, storage = services
    root, source = package
    scope = source.identity.scope
    with scoped(source):
        with repository.connections() as c:
            c.execute(
                "INSERT INTO registry.organization_projects VALUES (%s,%s,'Deletion test')",
                scope[:2],
            )
        tasks = CollectionTaskService(PostgresCollectionTaskRepository(repository.connections))
        task = tasks.create(
            organization_id=scope[0],
            project_id=scope[1],
            command=CreateCollectionTask(name="Delete fixture", type="capture", scenario="test"),
            idempotency_key=uuid4().hex,
        ).record
        keep = tasks.create(
            organization_id=scope[0],
            project_id=scope[1],
            command=CreateCollectionTask(name="Keep fixture", type="capture", scenario="test"),
            idempotency_key=uuid4().hex,
        ).record
        with repository.connections() as c:
            c.execute(
                "UPDATE collection_tasks.collection_tasks SET upload_region_code=%s "
                "WHERE collection_task_id=%s",
                (scope[2], task.collection_task_id),
            )
        source = source.model_copy(
            update={
                "identity": source.identity.model_copy(update={"dataset_id": task.dataset_id}),
                "task_id": task.collection_task_id,
            }
        )
        (root / "hc/source.json").write_text(
            json.dumps(
                {"identity": source.identity.model_dump(), "source_sha256": source.source_sha256}
            )
        )
        receipt = EpisodePackageCommitter(repository, storage).commit(root, source)
        key = receipt.data.package_prefix + "/meta/info.json"
        assert storage.head(key) is not None
        scheme_id, robot_id = "shared-scheme", "shared-robot"
        raw_ids = {record.collection_task_id: uuid4().hex for record in (task, keep)}
        raw_keys = {
            record.collection_task_id:
                f"raw/{scope[0]}/{scope[1]}/{record.collection_task_id}/manifest.json"
            for record in (task, keep)
        }
        local_files = {}
        selection_keys = {}
        with repository.connections() as c:
            c.execute("""INSERT INTO robotics.robot_assets
                (organization_id,robot_id,display_name,serial_no,lifecycle_status,
                 connectivity_state,etag,topology_revision)
                VALUES (%s,%s,'Shared robot','test','DRAFT','OFFLINE','v1','1')""",
                (scope[0], robot_id))
            c.execute("""INSERT INTO ingest.capture_schemes
                (organization_id,scheme_id,revision,draft) VALUES (%s,%s,1,'{}')""",
                (scope[0], scheme_id))
            c.execute("""INSERT INTO ingest.capture_scheme_versions
                (organization_id,scheme_id,version,draft_revision,content_sha256,document)
                VALUES (%s,%s,1,1,%s,'{}')""", (scope[0], scheme_id, "a" * 64))
            for record in (task, keep):
                seed_capture_dependencies(c, scope, record, raw_ids[record.collection_task_id],
                                          scheme_id, robot_id, raw_keys[record.collection_task_id])
                storage._client.put_object(
                    Bucket=storage._bucket, Key=raw_keys[record.collection_task_id], Body=b"{}",
                )
                annotation_id = uuid4().hex
                rollout_id = raw_ids[record.collection_task_id]
                c.execute("""INSERT INTO annotation.annotation_tasks
                    (organization_id,project_id,region_code,task_id,dataset_id,dataset_version,
                     rollout_id,base_lance_version,status,etag)
                    VALUES (%s,%s,%s,%s,%s,1,%s,1,'DRAFT','v1')""",
                    (*scope, annotation_id, record.dataset_id, rollout_id))
                c.execute("""INSERT INTO annotation.annotation_revisions
                    (task_id,revision,author_id,client_mutation_id,base_lance_version)
                    VALUES (%s,0,'test','initial',1)""", (annotation_id,))
                task_prefix = raw_keys[record.collection_task_id].rsplit("/", 1)[0]
                selection_key = task_prefix + "/frames.json"
                selection_keys[record.collection_task_id] = selection_key
                storage._client.put_object(Bucket=storage._bucket, Key=selection_key, Body=b"[]")
                c.execute("""INSERT INTO annotation.frame_selection_manifests
                    (organization_id,project_id,region_code,task_id,source_sha256,object_key,
                     content_sha256,size_bytes,sampling_version,camera_set,source_frame_count,
                     selected_group_count,created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,2,'1','["front"]',10,1,now())""",
                    (*scope, annotation_id, "a" * 64, selection_key, "b" * 64))
                local_files[record.collection_task_id] = []
                for attempt in ("first", "second"):
                    aligned, path = alignment_row(tmp_path / "alignment", rollout_id, attempt)
                    path.parent.mkdir(parents=True)
                    path.write_bytes(b"arrow")
                    local_files[record.collection_task_id].append(path)
                    c.execute("""INSERT INTO public.aligned_fragment_attempts
                        (organization_id,project_id,region_code,rollout_id,source_sha256,
                         converter_version,attempt_id,content_sha256,schema_sha256,row_count,
                         staging_uri,staging_format,status)
                        VALUES (%s,%s,%s,%s,%s,'test',%s,%s,%s,1,%s,'arrow-ipc/v1','READY')""",
                        (*scope, rollout_id, "a" * 64, attempt, aligned["content_sha256"],
                         "c" * 64, aligned["staging_uri"]))
            assert c.execute("SELECT used_bytes FROM annotation.frame_selection_project_quotas "
                             "WHERE organization_id=%s AND project_id=%s AND region_code=%s",
                             scope).fetchone()[0] == 4
        with pytest.raises(psycopg.Error, match="immutable"), repository.connections() as c:
            c.execute("DELETE FROM episode_store.versions WHERE dataset_id=%s", (task.dataset_id,))
        with pytest.raises(psycopg.Error, match="immutable"), repository.connections() as c:
            c.execute("DELETE FROM ingest.raw_capture_snapshots WHERE raw_source_id=%s",
                      (raw_ids[task.collection_task_id],))

        class FlakyObjects(TaskObjectPurger):
            broken = True

            def purge(self, manifest):
                if self.broken:
                    raise OSError("simulated provider outage")
                super().purge(manifest)

        objects = FlakyObjects(storage._client, storage._bucket,
                               alignment_staging_root=tmp_path / "alignment")
        service = PostgresTaskDeletion(repository.connections, objects)
        with pytest.raises(ProblemException) as stale:
            service.delete(scope, task.collection_task_id, '"v99"', "delete-fixture")
        assert stale.value.problem.status == 412
        # A retained lineage pointing at the same local bytes must block the
        # whole purge before metadata or either provider is touched.
        with repository.connections() as c:
            c.execute("""INSERT INTO public.aligned_fragment_attempts
                (organization_id,project_id,region_code,rollout_id,source_sha256,
                 converter_version,attempt_id,staging_uri,status)
                VALUES (%s,%s,%s,%s,%s,'test','borrowed',%s,'ABORTED')""",
                (*scope, raw_ids[keep.collection_task_id], "a" * 64,
                 local_files[task.collection_task_id][0].as_uri()))
        with pytest.raises(ProblemException) as shared:
            service.delete(scope, task.collection_task_id, '"v1"', "delete-fixture")
        assert shared.value.problem.code == "TASK_DELETE_SHARED_OBJECT"
        assert tasks.detail(*scope[:2], task.collection_task_id).status.value == "ACTIVE"
        assert all(path.exists() for path in local_files[task.collection_task_id])
        with repository.connections() as c:
            c.execute("DELETE FROM public.aligned_fragment_attempts "
                      "WHERE rollout_id=%s AND attempt_id='borrowed'",
                      (raw_ids[keep.collection_task_id],))
        with pytest.raises(ProblemException) as failed:
            service.delete(scope, task.collection_task_id, '"v1"', "delete-fixture")
        assert failed.value.problem.code == "TASK_DELETE_CLEANUP_PENDING"
        assert storage.head(key) is not None
        assert tasks.detail(*scope[:2], task.collection_task_id).status.value == "CANCELLED"
        with (
            pytest.raises(psycopg.Error, match="COLLECTION_TASK_DELETED"),
            repository.connections() as c,
        ):
            c.execute(
                "UPDATE collection_tasks.collection_tasks SET status='ACTIVE' "
                "WHERE collection_task_id=%s",
                (task.collection_task_id,),
            )
        objects.broken = False
        original_purge = objects.local_artifacts.purge

        def fail_after_first_file(paths):
            original_purge(list(paths)[:1])
            raise OSError("simulated local cleanup interruption")

        monkeypatch.setattr(objects.local_artifacts, "purge", fail_after_first_file)
        with pytest.raises(ProblemException) as interrupted:
            service.delete(scope, task.collection_task_id, '"v1"', "delete-fixture")
        assert interrupted.value.problem.code == "TASK_DELETE_CLEANUP_PENDING"
        assert sum(path.exists() for path in local_files[task.collection_task_id]) == 1
        assert storage.head(selection_keys[task.collection_task_id]) is None
        monkeypatch.setattr(objects.local_artifacts, "purge", original_purge)
        result = service.delete(scope, task.collection_task_id, '"v1"', "delete-fixture")
        assert result.deleted_objects > 0 and result.deleted_bytes > 0
        assert storage.head(key) is None
        assert storage.head(raw_keys[task.collection_task_id]) is None
        assert storage.head(raw_keys[keep.collection_task_id]) is not None
        assert not any(path.exists() for path in local_files[task.collection_task_id])
        assert all(path.exists() for path in local_files[keep.collection_task_id])
        assert storage.head(selection_keys[keep.collection_task_id]) is not None
        assert repository.current(source.identity) is None
        assert tasks.detail(*scope[:2], keep.collection_task_id).name == "Keep fixture"
        assert service.delete(scope, task.collection_task_id, '"v1"', "delete-fixture") == result
        with pytest.raises(ProblemException):
            tasks.detail(*scope[:2], task.collection_task_id)
        with pytest.raises(psycopg.Error, match="COLLECTION_TASK_DELETED"):
            EpisodePackageCommitter(repository, storage).commit(root, source)
        with repository.connections() as c:
            assert c.execute("SELECT used_bytes FROM annotation.frame_selection_project_quotas "
                             "WHERE organization_id=%s AND project_id=%s AND region_code=%s",
                             scope).fetchone()[0] == 2
            # Every task-owned snapshot/format/plan must be gone, including
            # tables without FKs; the shared scheme and robot stay in place.
            for table in (
                "collection_tasks.capture_plans", "collection_tasks.capture_plan_heads",
                "ingest.capture_recording_snapshots", "ingest.robot_task_data_requirements",
                "ingest.raw_capture_snapshots", "ingest.capture_reprocess_requests",
                "ingest.recording_source_links", "episode_store.dataset_capture_contracts",
                "episode_store.dataset_capture_formats", "episode_store.dataset_schema_versions",
            ):
                for record, expected in ((task, 0), (keep, 1)):
                    query = sql.SQL(
                        "SELECT count(*) FROM {} t WHERE "
                        "to_jsonb(t)->>'collection_task_id'=%s OR "
                        "to_jsonb(t)->>'raw_source_id'=%s OR "
                        "to_jsonb(t)->>'dataset_id'=%s"
                    ).format(sql.Identifier(*table.split(".")))
                    params = (record.collection_task_id,
                              raw_ids[record.collection_task_id], record.dataset_id)
                    assert c.execute(query, params).fetchone()[0] == expected
            assert c.execute(
                "SELECT count(*) FROM ingest.capture_scheme_versions "
                "WHERE organization_id=%s AND scheme_id=%s", (scope[0], scheme_id),
            ).fetchone()[0] == 1
            assert c.execute(
                "SELECT count(*) FROM robotics.robot_assets "
                "WHERE organization_id=%s AND robot_id=%s", (scope[0], robot_id),
            ).fetchone()[0] == 1
            assert (
                c.execute(
                    "SELECT count(*) FROM core.audit_events WHERE resource_id=%s "
                    "AND action='collection_task.deleted'",
                    (task.collection_task_id,),
                ).fetchone()[0]
                == 1
            )
        with (
            pytest.raises(psycopg.Error, match="COLLECTION_TASK_DELETED"),
            repository.connections() as c,
        ):
            c.execute("""INSERT INTO episode_store.dataset_capture_formats
                (organization_id,project_id,region_code,dataset_id,format_version,document,content_hash)
                VALUES (%s,%s,%s,%s,'late-format','{}',%s)""", (*scope, task.dataset_id, "a" * 64))
        with pytest.raises(psycopg.Error, match="immutable"), repository.connections() as c:
            c.execute("SELECT set_config('app.collection_task_purge_id',%s,true)",
                      (task.collection_task_id,))
            c.execute("DELETE FROM ingest.capture_scheme_versions "
                      "WHERE organization_id=%s AND scheme_id=%s", (scope[0], scheme_id))
        service.delete(scope, keep.collection_task_id, '"v1"', "cleanup-keep-fixture")
        assert storage.head(raw_keys[keep.collection_task_id]) is None
        assert storage.head(selection_keys[keep.collection_task_id]) is None
        assert not any(path.exists() for path in local_files[keep.collection_task_id])
