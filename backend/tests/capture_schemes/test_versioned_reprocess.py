"""Real isolated transactions: format evolution, execution pins and concurrent retries."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest

from hc_data_platform.capture_schemes import plans, schemes, associations
from hc_data_platform.capture_schemes.common import digest
from hc_data_platform.capture_schemes.models import PrepareRecording, SchemeDraft, VersionReference
from hc_data_platform.capture_schemes.processing import resolve_raw_snapshot, save_raw_snapshot
from hc_data_platform.capture_schemes.reprocess import ReprocessMcap, execution_snapshot, request
from hc_data_platform.capture_schemes.schema import native_topic, semantic_schema
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.episode_store.postgres import PostgresEpisodeStore
from hc_data_platform.episode_store.schemas import EpisodeSchemas
from hc_data_platform.lerobot_imports.mcap_processing import record_rejection
from tests.episode_store.test_commit_services import scoped

from .test_mcap_v3 import NAMES, configuration
from .test_transactions import associate as associate_lerobot
from .test_transactions import environment, selection  # noqa: F401


def associate(db,org,robot,ref):
    associate_lerobot(db,org,robot,ref)
    link=associations.list_links(db,org,robot)[-1]
    link=next(l for l in associations.list_links(db,org,robot) if l["scheme_id"]==ref.scheme_id)
    associations.record_validation(db,org,robot,ref.scheme_id,link["mapping_version"],{
        "source_format":"MCAP","status":"PASSED","sample_robot_id":robot,"sample_sha256":"2"*64,
        "mapping_sha256":link["mapping"]["content_sha256"],"parser_version":"native-mcap-recording/3","test_fixture":True},"test")


def publish(db, org, name, *, old=False):
    cfg, output = configuration(camera=False)
    doc = dict(expected_revision=0, name=name, output=output.model_dump(mode="json"),
               adapters=[dict(source_format="MCAP",configuration=cfg.model_dump(mode="json"))])
    if old:
        doc["output"]["algorithm"]="capture-structure/v1"
        for feature in doc["output"]["features"].values():
            feature["required_axes"]=None
        doc["adapters"][0]["configuration"]["semantic_output"]=doc["output"]
    saved=schemes.save(db,org,name,SchemeDraft(**doc),"test")
    schemes.record_validation(db,org,name,saved["revision"],dict(source_format="MCAP",status="PASSED",
        parser_version="native-mcap-recording/3",sample_sha256="1"*64,draft_sha256=digest(saved["draft"]),test_fixture=True),"test")
    version=schemes.publish(db,org,name,saved["revision"],"test")
    return VersionReference(**{k:version[k] for k in ("scheme_id","version","content_sha256")})


def seed(fixture, *, intake_error=None):
    dsn,scope,tasks=fixture
    task=tasks[0]
    raw=SimpleNamespace(raw_source_id="raw-"+uuid4().hex,dataset_id="dataset_capture_"+task,storage_prefix="raw/fixture")
    manifest={"files":[dict(path="raw.mcap",size=100,sha256="a"*64)]}
    with psycopg.connect(dsn) as db:
        old=publish(db,scope[0],"old",old=True)
        associate(db,scope[0],"robot-1",old)
        plans.save_plan(db,scope,task,selection(old,"robot-1"),"test")
        original=plans.prepare_recording(db,scope,task,"robot-1",PrepareRecording(expected_plan_revision=1,recording_id="recording",source_format="MCAP"),"test")
        db.execute("""INSERT INTO ingest.raw_sources
            (organization_id,project_id,region_code,raw_source_id,upload_id,dataset_id,collection_task_id,robot_id,
             source_format,source_format_version,manifest_key,storage_prefix,content_hash,file_count,total_bytes,
             raw_status,processing_status,created_at,committed_at,updated_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s,'robot-1','MCAP','1',%s,'raw/fixture',%s,1,100,'COMMITTED','FAILED',now(),now(),now())""",
            (*scope,raw.raw_source_id,raw.raw_source_id,raw.dataset_id,task,raw.raw_source_id+"/manifest","a"*64))
        db.execute("""INSERT INTO ingest.raw_ingest_jobs
            (organization_id,project_id,region_code,raw_source_id,job_id,job_type,adapter_name,status,created_at,updated_at)
            VALUES(%s,%s,%s,%s,%s,'CONTINUOUS_RECORDING_DISCOVERY','mcap','FAILED',now(),now())""",
            (*scope,raw.raw_source_id,"job-"+raw.raw_source_id))
        if intake_error:
            from hc_data_platform.capture_schemes.ports import unresolved_receipt
            original = unresolved_receipt(intake_error, "intake preflight rejected")
        save_raw_snapshot(db,scope,raw.raw_source_id,original)
        record_rejection(db,scope,raw,manifest,SimpleNamespace(collection_task_id=task,robot_id="robot-1"),{**original,"error":"MCAP_JOINT_LAYOUT_MISMATCH"})
        new=publish(db,scope[0],"new")
        associate(db,scope[0],"robot-1",new)
        plans.save_plan(db,scope,task,selection(new,"robot-1",revision=1),"test")
    return dsn,scope,task,raw,manifest,original


def test_changed_output_prepares_and_registers_without_overwriting_old_contract(environment):  # noqa: F811
    dsn,scope,task,raw,_,original=seed(environment)
    with psycopg.connect(dsn) as db:
        snap=plans.prepare_recording(db,scope,task,"robot-1",PrepareRecording(expected_plan_revision=2,recording_id="new-recording",source_format="MCAP"),"test")
        assert snap["output"]["features"]["action"]["required_axes"]==NAMES[:14]
        assert resolve_raw_snapshot(db,scope,raw.raw_source_id)==original
        assert db.execute("SELECT document FROM episode_store.dataset_capture_contracts WHERE organization_id=%s",(scope[0],)).fetchone()[0]==original["output"]
        assert db.execute("SELECT count(*) FROM episode_store.dataset_capture_formats WHERE organization_id=%s",(scope[0],)).fetchone()[0]==1
    registry=EpisodeSchemas(PostgresEpisodeStore(psycopg_connection_factory(dsn)))
    with scoped(SimpleNamespace(identity=SimpleNamespace(organization_id=scope[0],project_id=scope[1],region_code=scope[2]))):
        for output in (original["output"],snap["output"]):
            schema=semantic_schema(scope[1],raw.dataset_id,output,{native_topic(n):"json" for n in output["features"]})
            registry.register_schema(schema)
            assert registry.schema_for(scope[1],raw.dataset_id,schema.schema_snapshot_id)==schema
        with pytest.raises(ValueError,match="multiple formats"):
            registry.schema_for(scope[1],raw.dataset_id)


def test_concurrent_reprocess_pins_exact_revision_and_preserves_old_result(environment):  # noqa: F811
    dsn,scope,task,raw,manifest,original=seed(environment)
    command=ReprocessMcap(request_id="same-request",plan_revision=2)
    def run(_):
        with psycopg.connect(dsn) as db:
            return request(db,scope,raw,manifest,command,"test")
    with ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(run,range(3)))
    assert results[0]==results[1]==results[2]
    saved=results[0]
    with psycopg.connect(dsn) as db:
        assert resolve_raw_snapshot(db,scope,raw.raw_source_id)==original
        assert execution_snapshot(db,scope,raw.raw_source_id,saved["execution_snapshot"])==saved["execution_snapshot"]
        rows=db.execute("SELECT status,config_reference FROM ingest.mcap_processing_files WHERE raw_source_id=%s ORDER BY created_at",(raw.raw_source_id,)).fetchall()
        assert [r[0] for r in rows]==["FAILED","PENDING"]
        new=VersionReference(**saved["execution_snapshot"]["scheme"])
        plans.save_plan(db,scope,task,selection(new,revision=2),"test")
        assert request(db,scope,raw,manifest,command,"test")==saved
    with pytest.raises(ProblemException),psycopg.connect(dsn) as db:
        request(db,scope,raw,manifest,ReprocessMcap(request_id="different",plan_revision=3),"test")
    with pytest.raises(ProblemException),psycopg.connect(dsn) as db:
        request(db,scope,raw,manifest,ReprocessMcap(request_id="same-request",plan_revision=3),"test")
    with pytest.raises(ProblemException),psycopg.connect(dsn) as db:
        request(db,(scope[0],"foreign",scope[2]),raw,manifest,command,"test")


def test_reprocess_rollback_leaves_no_execution_or_pending_work(environment):  # noqa: F811
    dsn,scope,_,raw,manifest,original=seed(environment)
    with pytest.raises(RuntimeError),psycopg.connect(dsn) as db:
        request(db,scope,raw,manifest,ReprocessMcap(request_id="rollback",plan_revision=2),"test")
        raise RuntimeError("simulated transaction failure")
    with psycopg.connect(dsn) as db:
        assert resolve_raw_snapshot(db,scope,raw.raw_source_id)==original
        assert db.execute("SELECT count(*) FROM ingest.capture_reprocess_requests WHERE raw_source_id=%s",(raw.raw_source_id,)).fetchone()[0]==0
        assert db.execute("SELECT status FROM ingest.mcap_processing_files WHERE raw_source_id=%s",(raw.raw_source_id,)).fetchall()==[("FAILED",)]


def test_ready_source_can_create_new_revision_without_erasing_old_result(environment):
    dsn, scope, _, raw, manifest, original = seed(environment)
    with psycopg.connect(dsn) as db:
        db.execute("UPDATE ingest.mcap_processing_files SET status='READY',recording_id='historic-recording' WHERE raw_source_id=%s", (raw.raw_source_id,))
        result = request(db, scope, raw, manifest, ReprocessMcap(request_id="upgrade-ready", plan_revision=2), "test")
        rows = db.execute("SELECT status,recording_id FROM ingest.mcap_processing_files WHERE raw_source_id=%s ORDER BY created_at", (raw.raw_source_id,)).fetchall()
        assert rows == [("READY", "historic-recording"), ("PENDING", None)]
        assert resolve_raw_snapshot(db, scope, raw.raw_source_id) == original
        assert result["original_snapshot"] == original

@pytest.mark.parametrize("intake_error", ["CAPTURE_PLAN_REQUIRED", "CAPTURE_MAPPING_UNVERIFIED"])
def test_bound_upload_pins_selected_version_and_retains_its_rejected_receipt(environment, intake_error):
    dsn, scope, task, raw, manifest, original = seed(environment, intake_error=intake_error)
    cmd = ReprocessMcap(request_id="late-bound", plan_revision=2)
    with psycopg.connect(dsn) as db:
        plans.save_plan(db, scope, task, selection(VersionReference.model_validate(
            plans.get_plan(db, scope, task)["scheme"]), revision=2), "test")
        result = request(db, scope, raw, manifest, cmd, "test")
        assert request(db, scope, raw, manifest, cmd, "test") == result
        snap = result["execution_snapshot"]
        assert snap["plan_revision"] == 2
        assert snap["robot_id"] == "robot-1"
        assert result["original_snapshot"] == resolve_raw_snapshot(db, scope, raw.raw_source_id) == original
        assert execution_snapshot(db, scope, raw.raw_source_id, snap) == snap
        with pytest.raises(ProblemException):
            execution_snapshot(db, scope, raw.raw_source_id, {**snap, "robot_id": "another"})
        with pytest.raises(ProblemException):
            execution_snapshot(db, scope, raw.raw_source_id, original)
