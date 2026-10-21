"""Browser session retries retain the server receipt and never reselect a plan.

Task preparation uses real PostgreSQL and synthetic publication evidence. Object
storage uses the in-memory adapter; this is not media decoder acceptance.
"""

import json
from copy import deepcopy
from io import BytesIO

import psycopg
import pytest
from fastapi.testclient import TestClient

from hc_data_platform.capture_schemes import plans
from hc_data_platform.capture_schemes.processing import resolve_raw_snapshot
from hc_data_platform.capture_schemes.references import AddParticipants
from hc_data_platform.capture_schemes.transactions import bind_raw_snapshot
from hc_data_platform.capture_schemes.web_uploads import PostgresWebUploadPlans
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.ingest.ports import InMemoryObjectStorage
from hc_data_platform.ingest.raw_sources import (
    CommittedRawSourceGraph,
    InMemoryRawSourceRepository,
    PostgresRawSourceRepository,
    RawIngestJob,
    RawIngestJobType,
    RawSource,
)
from hc_data_platform.lerobot_imports.models import (
    CommitLeRobotImportV1,
    CompleteLeRobotAssetV1,
    CreateLeRobotImportV1,
)
from hc_data_platform.lerobot_imports.router import get_service
from hc_data_platform.lerobot_imports.service import LeRobotWebUploadService
from hc_data_platform.security.auth import AuthContext
from tests.lerobot_imports.test_lerobot_web_upload import _app

from .test_transactions import associate, environment, publish, selection  # noqa: F401


def test_browser_preparation_replays_saved_revision_and_checks_exact_target(environment):  # noqa: F811
    dsn, scope, tasks = environment
    with psycopg.connect(dsn) as db:
        ref = publish(db, scope[0])
        associate(db, scope[0], "robot-1", ref)
        associate(db, scope[0], "robot-2", ref)
        plans.save_plan(db, scope, tasks[0], selection(ref, "robot-1"), "tester")
    port = PostgresWebUploadPlans(lambda: psycopg.connect(dsn))
    args = dict(
        dataset_id="dataset_capture_" + tasks[0],
        task_id=tasks[0],
        robot_id="robot-1",
        source_format="LEROBOT_V3",
        actor="browser-user",
    )
    first = port.prepare(scope, "web-import-1", **args)
    assert first["schema_version"] == "capture-recording-snapshot/v1"
    assert first["plan_revision"] == 1 and first["output"]["frequency_hz"] == 15
    with psycopg.connect(dsn) as db:
        plans.add_participants(
            db,
            scope,
            tasks[0],
            AddParticipants(
                expected_revision=1, participants=[{"robot_id": "robot-2", "mapping_version": 1}]
            ),
            "tester",
        )
    assert port.prepare(scope, "web-import-1", **args) == first
    assert port.prepare(scope, "web-import-2", **args)["plan_revision"] == 2
    wrong = port.prepare(scope, "web-import-1", **{**args, "dataset_id": "wrong"})
    assert wrong["error"] == "CAPTURE_VERSION_MISMATCH" and wrong["retryable"] is False
    missing = port.prepare(scope, "web-import-3", **{**args, "task_id": tasks[1]})
    assert missing["error"] == "CAPTURE_PLAN_TARGET_MISMATCH"
    missing = port.prepare(
        scope,
        "web-import-4",
        **{**args, "task_id": tasks[1], "dataset_id": "dataset_capture_" + tasks[1]},
    )
    assert missing["error"] == "CAPTURE_PLAN_REQUIRED"
    with psycopg.connect(dsn) as db:
        assert (
            db.execute(
                "SELECT count(*) FROM ingest.capture_recording_snapshots WHERE organization_id=%s",
                (scope[0],),
            ).fetchone()[0]
            == 2
        )


class Captures:
    def __init__(self, receipt):
        self.receipt = receipt
        self.calls = []

    def prepare(self, scope, upload_id, **kwargs):
        self.calls.append((scope, upload_id, kwargs))
        return deepcopy(self.receipt)


class RawSources(InMemoryRawSourceRepository):
    def register_committed(self, graph):
        self.graph = super().register_committed(graph)
        return self.graph


@pytest.mark.parametrize("source_format", ["MCAP", "LEROBOT_V3"])
def test_browser_session_and_raw_commit_keep_problem_receipt_after_configuration_changes(
    source_format,
):
    scope = dict(organization_id="org-web", project_id="project-web", region_code="cn-east")
    auth = AuthContext(
        subject_id="browser-user",
        organization_ids=frozenset({scope["organization_id"]}),
        project_ids=frozenset({scope["project_id"]}),
        region_codes=frozenset({scope["region_code"]}),
        organization_scope_triples=frozenset({tuple(scope.values())}),
        capabilities=frozenset({"upload.manage"}),
    )
    receipt = {
        "error": "CAPTURE_PLAN_REQUIRED",
        "detail": "未绑定任务方案",
        "details": {},
        "source": "TASK_PLAN_UNRESOLVED",
        "retryable": False,
    }
    captures = Captures(receipt)
    storage, raw = InMemoryObjectStorage(), RawSources()
    info = {
        "codebase_version": "v3.0",
        "total_episodes": 1,
        "fps": 15,
        "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
        "features": {"observation.state": {"dtype": "float32", "shape": [1]}},
    }
    bodies = (
        {"sample.mcap": b"raw-test-fixture"}
        if source_format == "MCAP"
        else {
            "meta/info.json": json.dumps(info).encode(),
            "meta/episodes/chunk-000/file-000.parquet": b"metadata-fixture",
            "data/chunk-000/file-000.parquet": b"frame-fixture",
        }
    )
    manifest = CreateLeRobotImportV1(
        dataset_id="dataset-web",
        collection_task_id="task-web",
        robot_id="robot-web",
        processing_mode="STORE_ONLY",
        source_format=source_format,
        info=info if source_format == "LEROBOT_V3" else {},
        files=[{"path": name, "size": len(body), "part_count": 1} for name, body in bodies.items()],
    )
    service = LeRobotWebUploadService(storage, raw_sources=raw, capture_plans=captures)
    grant = service.begin(auth=auth, **scope, manifest=manifest)
    captures.receipt = {"error": "SHOULD_NOT_RESELECT"}

    def unavailable_target(*_args):
        raise AssertionError("An accepted upload resumes without new-task validation")

    service = LeRobotWebUploadService(
        storage, raw_sources=raw, capture_plans=captures, target_validator=unavailable_target
    )
    assert service.begin(auth=auth, **scope, manifest=manifest) == grant
    assert len(captures.calls) == 1
    for asset in grant.assets:
        body = bodies[asset.path]
        service.upload_part(
            auth=auth,
            **scope,
            import_id=grant.import_id,
            dataset_id=manifest.dataset_id,
            path=asset.path,
            multipart_upload_id=asset.multipart_upload_id,
            part_number=1,
            body=BytesIO(body),
            size=len(body),
        )
        service.complete_asset(
            auth=auth,
            **scope,
            import_id=grant.import_id,
            command=CompleteLeRobotAssetV1(
                dataset_id=manifest.dataset_id,
                path=asset.path,
                multipart_upload_id=asset.multipart_upload_id,
                size=len(body),
                part_count=1,
            ),
        )
    command = CommitLeRobotImportV1(manifest=manifest)
    first = service.commit(auth=auth, **scope, import_id=grant.import_id, command=command)
    assert raw.graph.capture_snapshot == receipt
    assert (
        json.loads(storage.objects[raw.graph.source.manifest_key])["processing_config_snapshot"]
        == receipt
    )
    assert service.commit(auth=auth, **scope, import_id=grant.import_id, command=command) == first
    assert len(captures.calls) == 1
    changed = raw.graph.model_copy(update={"capture_snapshot": {"error": "changed"}})
    with pytest.raises(ProblemException, match="RAW_SOURCE_IDENTITY_CONFLICT"):
        raw.register_committed(changed)

    # An old accepted session without the extension must not acquire today's plan.
    session_key = next(key for key in storage.objects if key.endswith("/upload-session.json"))
    legacy = json.loads(storage.objects[session_key])
    legacy.pop("capture_snapshot")
    storage.put_json(session_key, legacy, if_none_match=False)
    legacy_raw = RawSources()
    legacy_service = LeRobotWebUploadService(
        storage, raw_sources=legacy_raw, capture_plans=captures
    )
    legacy_service.commit(auth=auth, **scope, import_id=grant.import_id, command=command)
    assert legacy_raw.graph.capture_snapshot["error"] == "CAPTURE_PLAN_REQUIRED"
    assert (
        legacy_raw.graph.capture_snapshot["details"]["compatibility"]
        == "LEGACY_WEB_UPLOAD_WITHOUT_SNAPSHOT"
    )
    assert len(captures.calls) == 1


def test_raw_graph_rolls_back_when_snapshot_writer_cannot_persist(environment):  # noqa: F811
    dsn, scope, tasks = environment
    source = RawSource(
        organization_id=scope[0],
        project_id=scope[1],
        region_code=scope[2],
        raw_source_id="web-raw-rollback",
        upload_id="web-raw-rollback",
        dataset_id="dataset_capture_" + tasks[0],
        collection_task_id=tasks[0],
        robot_id="robot-1",
        source_format="MCAP",
        source_format_version="native",
        manifest_key=f"raw/{scope[0]}/manifest.json",
        storage_prefix=f"raw/{scope[0]}/source",
        content_hash="a" * 64,
        file_count=1,
        total_bytes=100,
    )
    graph = CommittedRawSourceGraph(
        source=source,
        job=RawIngestJob(
            organization_id=scope[0],
            project_id=scope[1],
            region_code=scope[2],
            raw_source_id=source.raw_source_id,
            job_id="web-rollback-job",
            job_type=RawIngestJobType.RAW_STORAGE,
            adapter_name="raw_storage",
        ),
        capture_snapshot={"error": "CAPTURE_PLAN_REQUIRED", "retryable": False},
    )
    calls = []

    def failing_writer(db, actual_scope, raw_id, snapshot):
        calls.append((actual_scope, raw_id, snapshot))
        assert db.execute(
            "SELECT 1 FROM ingest.raw_sources WHERE organization_id=%s AND raw_source_id=%s",
            (scope[0], raw_id),
        ).fetchone()
        bind_raw_snapshot(db, actual_scope, raw_id, snapshot)
        raise RuntimeError("snapshot storage failure")

    repository = PostgresRawSourceRepository(
        lambda: psycopg.connect(dsn), capture_snapshot_writer=failing_writer
    )
    with pytest.raises(RuntimeError, match="snapshot storage failure"):
        repository.register_committed(graph)
    assert calls == [(scope, source.raw_source_id, graph.capture_snapshot)]
    with psycopg.connect(dsn) as db:
        for table in ("raw_sources", "raw_ingest_jobs", "raw_capture_snapshots"):
            assert (
                db.execute(
                    f"SELECT count(*) FROM ingest.{table} WHERE organization_id=%s",
                    (scope[0],),
                ).fetchone()[0]
                == 0
            )
    with pytest.raises(ProblemException, match="CAPTURE_STORE_UNAVAILABLE"):
        PostgresRawSourceRepository(lambda: psycopg.connect(dsn)).register_committed(graph)

    # The integrated owner persists the receipt atomically; retries cannot rewrite it.
    repository = PostgresRawSourceRepository(
        lambda: psycopg.connect(dsn), capture_snapshot_writer=bind_raw_snapshot
    )
    repository.register_committed(graph)
    repository.register_committed(graph)
    with psycopg.connect(dsn) as db:
        assert resolve_raw_snapshot(db, scope, source.raw_source_id) == graph.capture_snapshot
    with pytest.raises(ProblemException, match="CAPTURE_VERSION_MISMATCH"):
        repository.register_committed(
            graph.model_copy(update={"capture_snapshot": {"error": "changed"}})
        )
    with psycopg.connect(dsn) as db:
        assert resolve_raw_snapshot(db, scope, source.raw_source_id) == graph.capture_snapshot


def test_browser_http_services_are_owned_by_the_current_application():
    stores = [InMemoryObjectStorage(), InMemoryObjectStorage()]
    apps = [_app(LeRobotWebUploadService(storage)) for storage in stores]
    for app, storage in zip(apps, stores, strict=True):
        app.dependency_overrides.pop(get_service)
        app.state.lerobot_uploads = LeRobotWebUploadService(storage)
    path = "/api/v1/projects/project-a/regions/cn-hz/lerobot-imports"
    command = {
        "dataset_id": "dataset-a",
        "source_format": "MCAP",
        "files": [{"path": "original.mcap", "size": 1, "part_count": 1}],
    }
    headers = {"X-Organization-Id": "org-a"}
    with TestClient(apps[0]) as first, TestClient(apps[1]) as second:
        result = first.post(path, json=command, headers=headers)
        assert result.status_code == 201, result.text
        assert stores[0].objects and not stores[1].objects
        command["capture_snapshot"] = {"error": "client-supplied"}
        assert second.post(path, json=command, headers=headers).status_code == 422
        command.pop("capture_snapshot")
        assert second.post(path, json=command, headers=headers).status_code == 201
        assert stores[1].objects
        apps[0].state.lerobot_uploads = None
        assert first.post(path, json=command, headers=headers).status_code == 503
