from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.ingest.manifest import parse_manifest_bytes
from hc_data_platform.ingest.ports import crc64_ecma
from tests.system.wave2.cleanup import require_test_cleanup_profile
from tests.system.wave2.fixture import (
    CleanupController,
    RunArtifact,
    RunScope,
    StageStatus,
    load_catalog,
    render_manifest,
    scenario,
    validated_manifest,
)
from tests.system.wave2.generate_packages import build_files
from tests.system.wave2.http_adapter import Wave2TestJwtIssuer
from tests.system.wave2.orchestrator import (
    MainChainRun,
    StageNotRunnable,
    StageResult,
    passed,
)

DATA_ROOT = Path(__file__).resolve().parent / "wave2" / "data"
BACKEND_ROOT = Path(__file__).resolve().parents[2]


def test_generated_package_corpus_is_byte_identical_and_fully_hashed() -> None:
    generated = build_files()
    checked_in = {
        path.relative_to(DATA_ROOT).as_posix(): path.read_bytes()
        for path in DATA_ROOT.rglob("*")
        if path.is_file()
    }
    assert generated == checked_in

    expected = {
        "bad_manifest",
        "duplicate_package",
        "legal",
        "missing_frame",
        "missing_topic",
        "multi_camera",
    }
    catalog = load_catalog()
    assert {item.name for item in catalog} == expected
    for item in catalog:
        item.verify_bytes()
        assert item.payload_size == len(item.payload)
        assert item.payload_sha256 == hashlib.sha256(item.payload).hexdigest()
        assert item.manifest_size == len(item.manifest_bytes)
        assert item.manifest_sha256 == hashlib.sha256(item.manifest_bytes).hexdigest()


def test_runtime_openapi_has_public_chain_contracts_and_exposes_known_provision_gaps() -> None:
    document = yaml.safe_load((BACKEND_ROOT / "openapi.generated.yaml").read_text(encoding="utf-8"))
    paths = document["paths"]
    required_methods = {
        "/api/v1/auth/registrations": {"post"},
        "/api/v1/auth/sessions": {"post"},
        "/api/v1/auth/session/bootstrap": {"get"},
        "/api/v1/projects/{project_id}/membership-requests": {"get", "post"},
        "/api/v1/projects/{project_id}/capability-requests": {"get", "post"},
        "/api/v1/projects/{project_id}/collection-tasks": {"get", "post"},
        "/api/v1/projects/{project_id}/collection-tasks/{collection_task_id}:close": {"post"},
        "/api/v1/projects/{project_id}/regions/{region_code}/upload-manifests:preflight": {"post"},
        "/api/v1/projects/{project_id}/regions/{region_code}/upload-sessions": {
            "get",
            "post",
        },
        "/api/v1/projects/{project_id}/annotation-tasks": {"get"},
        "/api/v1/annotation-tasks/{task_id}/revisions": {"get", "post"},
        "/api/v1/annotation-tasks/{task_id}/submit": {"post"},
        "/api/v1/annotation-tasks/{task_id}/reviews": {"get", "post"},
    }
    for path, expected in required_methods.items():
        assert path in paths
        assert expected <= set(paths[path])

    # These are explicit runner blockers, not similar-name endpoint assumptions.
    http_methods = {
        "get",
        "put",
        "post",
        "delete",
        "options",
        "head",
        "patch",
        "trace",
    }
    assert (
        set(paths["/api/v1/projects/{project_id}/annotation-tasks"]) & http_methods
        == {"get"}
    )
    assert not any("start-ingest" in path or "ingest-workflow" in path for path in paths)


def test_actual_migrations_contain_every_persistent_wave2_fixture_boundary() -> None:
    migrations = BACKEND_ROOT / "migrations"
    expected_relations = {
        migrations / "security/002_access_control.sql": (
            "access_control.accounts",
            "access_control.membership_requests",
            "access_control.capability_requests",
            "access_control.audit_events",
        ),
        migrations / "collection_tasks/0001_collection_tasks.sql": (
            "collection_tasks.collection_tasks",
        ),
        migrations / "ingest/002_package_manifest_uploads.sql": (
            "ingest.manifest_discoveries",
            "data_package_id",
        ),
        migrations / "annotation/0002_tag_schema_revisions.sql": (
            "annotation.tag_schema_versions",
            "annotation.annotation_submissions",
        ),
        migrations / "ingest/003_automatic_workflow_trigger.sql": (
            "ingest.workflow_triggers",
            "workflow_id",
        ),
        migrations / "annotation/0003_automatic_tasks.sql": (
            "annotation.tag_schema_bindings",
            "annotation.annotation_task_triggers",
        ),
        migrations / "security/003_outbox_dispatch.sql": (
            "claim_token",
            "last_error_code",
        ),
    }
    for path, markers in expected_relations.items():
        sql = path.read_text(encoding="utf-8")
        for marker in markers:
            assert marker in sql


def test_manifest_templates_bind_to_an_isolated_run_and_validate_payload_integrity() -> None:
    scope = RunScope.create("repeatable-01")
    for index, name in enumerate(("legal", "multi_camera", "missing_frame", "missing_topic"), 1):
        package = scenario(name)
        manifest = validated_manifest(
            package,
            scope,
            collection_task_id="collection-task-1",
            sequence_no=index,
        )
        assert manifest.project_id == scope.project_id
        assert manifest.task_id == "collection-task-1"
        assert manifest.file_size == package.payload_size
        assert manifest.sha256 == package.payload_sha256
        assert manifest.crc64 == crc64_ecma(package.payload)
        result = parse_manifest_bytes(manifest.model_dump_json().encode())
        assert result.manifest == manifest

    missing_topic = validated_manifest(
        scenario("missing_topic"),
        scope,
        collection_task_id="collection-task-1",
        sequence_no=4,
    )
    result = parse_manifest_bytes(missing_topic.model_dump_json().encode())
    assert result.discovery.missing_expected_topics == ("/action",)


def test_bad_manifest_is_rejected_and_duplicate_package_reuses_the_same_identity() -> None:
    scope = RunScope.create("duplicate-01")
    bad = render_manifest(
        scenario("bad_manifest"),
        scope,
        collection_task_id="collection-task-1",
        sequence_no=1,
    )
    with pytest.raises(ProblemException) as captured:
        parse_manifest_bytes(json.dumps(bad).encode())
    assert captured.value.problem.code == "MANIFEST_INVALID"

    legal = render_manifest(
        scenario("legal"),
        scope,
        collection_task_id="collection-task-1",
        sequence_no=1,
    )
    replay = render_manifest(
        scenario("duplicate_package"),
        scope,
        collection_task_id="collection-task-1",
        sequence_no=1,
    )
    assert replay == legal
    assert scenario("duplicate_package").payload == scenario("legal").payload


def test_mcap_corpus_contains_two_cameras_and_one_missing_frame() -> None:
    reader_module = pytest.importorskip("mcap.reader")

    def counts(name: str) -> dict[str, int]:
        with (DATA_ROOT / scenario(name).payload_path).open("rb") as stream:
            messages = tuple(reader_module.make_reader(stream).iter_messages())
        result: dict[str, int] = {}
        for _schema, channel, _message in messages:
            result[channel.topic] = result.get(channel.topic, 0) + 1
        return result

    assert counts("multi_camera")["/camera/front/image"] == 30
    assert counts("multi_camera")["/camera/rear/image"] == 30
    assert counts("missing_frame")["/camera/front/image"] == 30
    assert counts("missing_frame")["/camera/rear/image"] == 29
    assert "/action" not in counts("missing_topic")


def test_run_namespace_is_deterministic_but_separate_runs_never_collide() -> None:
    first = RunScope.create("run-01")
    assert first == RunScope.create("run-01")
    second = RunScope.create("run-02")
    assert first.project_id != second.project_id
    assert first.foreign_project_id != second.foreign_project_id
    assert first.admin_username != second.admin_username
    assert first.raw_prefix != second.raw_prefix
    first.assert_cleanup_safe()
    with pytest.raises(ValueError):
        RunScope.create("../unsafe")


def test_artifact_records_diagnostics_without_credentials_or_signed_urls(tmp_path: Path) -> None:
    scope = RunScope.create("artifact-01")
    artifact = RunArtifact.for_scope(scope)
    artifact.add_stage(
        "register_empty_account",
        StageStatus.PASS,
        started_at=datetime.now(timezone.utc),
        resource_ids={
            "principal_id": "principal-1",
            "access_token": "hcs_never-write-this",
            "signed_url": "http://minio/object?X-Amz-Signature=never",
        },
        request_ids=("request-1",),
        audit_event_ids=("audit-1",),
    )
    artifact.cleanup.append(
        {
            "target": scope.raw_prefix,
            "status": "CLEAN",
            "password": "never-write-this",
            "authorization": "Bearer never-write-this",
        }
    )
    target = tmp_path / "artifact.json"
    artifact.write(target)
    encoded = target.read_text(encoding="utf-8")
    assert "principal-1" in encoded
    assert "request-1" in encoded
    assert "audit-1" in encoded
    assert "never-write-this" not in encoded
    assert "hcs_" not in encoded
    assert "X-Amz-Signature" not in encoded
    assert json.loads(encoded)["browser_e2e_status"] == "NOT RUN"


class RecoverableCleanup:
    def __init__(self) -> None:
        self.database_rows = 3
        self.objects = {"first": 2, "second": 1}
        self.fail_once = True
        self.prefix_calls: list[str] = []

    def delete_database_scope(self, _scope: RunScope) -> dict[str, int]:
        deleted = self.database_rows
        self.database_rows = 0
        return {"rows": deleted}

    def delete_object_prefix(self, prefix: str) -> int:
        self.prefix_calls.append(prefix)
        if self.fail_once:
            self.fail_once = False
            raise OSError("injected cleanup interruption")
        if prefix.startswith("raw/"):
            key = "first" if "p1" in prefix else "second"
        else:
            return 0
        deleted = self.objects[key]
        self.objects[key] = 0
        return deleted


def test_cleanup_continues_after_failure_and_retry_is_idempotent() -> None:
    backend = RecoverableCleanup()
    controller = CleanupController(backend)
    scope = RunScope.create("cleanup-01")
    first = controller.execute(scope)
    assert [item["status"] for item in first] == [
        "CLEAN",
        "FAILED",
        "CLEAN",
        "CLEAN",
        "CLEAN",
        "CLEAN",
        "CLEAN",
    ]
    assert len(backend.prefix_calls) == 6

    second = controller.execute(scope)
    assert all(item["status"] == "CLEAN" for item in second)
    assert second[0]["deleted"] == {"rows": 0}
    assert second[1]["deleted"] == 2
    assert second[2]["deleted"] == 0

    third = controller.execute(scope)
    assert third[0]["deleted"] == {"rows": 0}
    assert all(item["deleted"] == 0 for item in third[1:])


def test_database_cleanup_requires_explicit_test_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    scope = RunScope.create("guard-01")
    dsn = "postgresql://hc:secret@localhost:5432/hc_data"
    monkeypatch.setenv("HC_ENVIRONMENT", "production")
    monkeypatch.setenv("HC_WAVE2_CLEANUP_ENABLED", "1")
    monkeypatch.setenv("HC_WAVE2_TEST_DATABASE_ACK", "hc_data")
    with pytest.raises(RuntimeError, match="HC_ENVIRONMENT=test"):
        require_test_cleanup_profile(scope, dsn)

    monkeypatch.setenv("HC_ENVIRONMENT", "test")
    monkeypatch.delenv("HC_WAVE2_CLEANUP_ENABLED")
    with pytest.raises(RuntimeError, match="HC_WAVE2_CLEANUP_ENABLED"):
        require_test_cleanup_profile(scope, dsn)

    monkeypatch.setenv("HC_WAVE2_CLEANUP_ENABLED", "1")
    monkeypatch.setenv("HC_WAVE2_TEST_DATABASE_ACK", "wrong")
    with pytest.raises(RuntimeError, match="exactly name"):
        require_test_cleanup_profile(scope, dsn)


class RepeatableMainChain:
    def __init__(self, *, block_worker: bool = False) -> None:
        self.block_worker = block_worker
        self.calls: list[str] = []

    def _stage(self, name: str) -> StageResult:
        self.calls.append(name)
        return StageResult(
            resource_ids={f"{name}_id": f"resource-{name}"},
            request_ids=(f"request-{name}",),
            audit_event_ids=(f"audit-{name}",),
        )

    def register_empty_accounts(self, _scope: RunScope) -> StageResult:
        return self._stage("register")

    def approve_membership_and_capabilities(self, _scope: RunScope) -> StageResult:
        return self._stage("access")

    def assert_cross_project_denied(self, _scope: RunScope) -> StageResult:
        return self._stage("idor")

    def create_collection_task(self, _scope: RunScope) -> StageResult:
        return self._stage("collection")

    def upload_and_discover_manifest(self, _scope: RunScope) -> StageResult:
        return self._stage("upload")

    def wait_for_worker_qc(self, _scope: RunScope) -> StageResult:
        if self.block_worker:
            raise StageNotRunnable("worker launch contract unavailable")
        return self._stage("worker")

    def annotate_multilevel_tags(self, _scope: RunScope) -> StageResult:
        return self._stage("annotation")

    def review_tags(self, _scope: RunScope) -> StageResult:
        return self._stage("review")

    def close_collection_task(self, _scope: RunScope) -> StageResult:
        return self._stage("close")


class EmptyCleanup:
    def delete_database_scope(self, _scope: RunScope) -> dict[str, int]:
        return {"rows": 0}

    def delete_object_prefix(self, _prefix: str) -> int:
        return 0


def test_main_chain_is_repeatable_and_keeps_browser_e2e_not_run(tmp_path: Path) -> None:
    adapter = RepeatableMainChain()
    runner = MainChainRun(adapter, CleanupController(EmptyCleanup()))
    scope = RunScope.create("two-runs-01")
    first = runner.execute(scope, artifact_path=tmp_path / "first.json")
    second = runner.execute(scope, artifact_path=tmp_path / "second.json")

    assert passed(first)
    assert passed(second)
    assert len(first.stages) == len(second.stages) == 9
    assert first.browser_e2e_status is StageStatus.NOT_RUN
    assert second.browser_e2e_status is StageStatus.NOT_RUN
    assert adapter.calls.count("close") == 2
    assert first.cleanup == second.cleanup


def test_main_chain_stops_at_exact_not_runnable_stage_and_still_cleans() -> None:
    artifact = MainChainRun(
        RepeatableMainChain(block_worker=True),
        CleanupController(EmptyCleanup()),
    ).execute(RunScope.create("blocked-01"))
    by_name = {stage.name: stage for stage in artifact.stages}
    assert by_name["upload_and_manifest_discovery"].status is StageStatus.PASS
    assert by_name["worker_manifest_qc_alignment"].status is StageStatus.NOT_RUN
    assert by_name["worker_manifest_qc_alignment"].detail == "worker launch contract unavailable"
    assert by_name["annotation_multilevel_tags"].status is StageStatus.NOT_RUN
    assert not passed(artifact)
    assert artifact.cleanup[-1]["phase"] == "after"


def test_test_jwt_issuer_is_unavailable_outside_test_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HC_ENVIRONMENT", "production")
    monkeypatch.setenv("HC_WAVE2_JWT_SIGNING_KEY", "x" * 32)
    with pytest.raises(RuntimeError, match="HC_ENVIRONMENT=test"):
        Wave2TestJwtIssuer.from_environment()


def test_real_runner_uses_persisted_locators_not_annotation_db_provisioning() -> None:
    adapter_source = (BACKEND_ROOT / "tests/system/wave2/http_adapter.py").read_text(
        encoding="utf-8"
    )
    runner_source = (BACKEND_ROOT / "tests/system/wave2/run_main_chain.py").read_text(
        encoding="utf-8"
    )
    assert 'committed.body.get("workflow")' in adapter_source
    assert 'quote(workflow_id, safe="")' in adapter_source
    assert 'f"/api/v1/jobs/{encoded_workflow_id}"' in adapter_source
    assert 'result.get("annotation_task")' in adapter_source
    assert "annotation_provisioner" not in adapter_source
    assert "PostgresAnnotationTaskProvisioner" not in runner_source
