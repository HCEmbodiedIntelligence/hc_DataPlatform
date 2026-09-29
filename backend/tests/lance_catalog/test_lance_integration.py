from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from hc_data_platform.lance_catalog import (
    AlignedFragmentManifestV1,
    CatalogIndexPendingError,
    DatasetSchemaSnapshot,
    InMemoryCatalogRepository,
    InMemoryDatasetWriterLock,
    LanceAdapter,
    LanceCatalogService,
    StepRecord,
    compute_fragment_hash,
)
from hc_data_platform.lance_catalog.audit import InMemoryLanceCatalogAuditRecorder
from hc_data_platform.lance_catalog.router import (
    configure_lance_catalog,
    configure_lance_catalog_audit_recorder,
)
from hc_data_platform.lance_catalog.router import (
    router as lance_catalog_router,
)
from hc_data_platform.lance_catalog.service import CatalogVersionConflict
from hc_data_platform.security.auth import AuthContext

pytest.importorskip("pyarrow")
lance = pytest.importorskip("lance")


def _schema() -> DatasetSchemaSnapshot:
    return DatasetSchemaSnapshot.create(
        project_id="project-a",
        dataset_id="dataset-a",
        schema_snapshot_id="schema-1",
        frequency_hz=30,
        fields={"camera.front": "binary", "joint.position": "list<float32>"},
    )


def _fragment(
    snapshot: DatasetSchemaSnapshot, rollout_id: str, count: int = 2
) -> tuple[AlignedFragmentManifestV1, tuple[StepRecord, ...]]:
    steps = tuple(
        StepRecord(
            rollout_id=rollout_id,
            step_index=index,
            timestamp_ns=index * 33_333_333,
            modalities={
                "camera.front": f"frame-{index}".encode(),
                "joint.position": [float(index)],
            },
            source_timestamps_ns={
                "camera.front": (index * 33_333_333,),
                "joint.position": (index * 33_333_333,),
            },
            time_error_ns={"camera.front": 0, "joint.position": 0},
            valid={"camera.front": True, "joint.position": True},
            repeated={"camera.front": False, "joint.position": False},
        )
        for index in range(count)
    )
    manifest = AlignedFragmentManifestV1(
        project_id=snapshot.project_id,
        dataset_id=snapshot.dataset_id,
        schema_snapshot_id=snapshot.schema_snapshot_id,
        schema_fingerprint=snapshot.fingerprint,
        frequency_hz=snapshot.frequency_hz,
        rollout_id=rollout_id,
        source_sha256=hashlib.sha256(rollout_id.encode()).hexdigest(),
        converter_version="aligner-1.0.0",
        attempt_id=f"attempt-{rollout_id}",
        fragment_uri=f"s3://alignment-staging/{rollout_id}.arrow",
        step_count=count,
        content_hash=compute_fragment_hash(steps),
    )
    return manifest, steps


def _service(
    root: Path, repository: InMemoryCatalogRepository | None = None
) -> tuple[LanceCatalogService, LanceAdapter, InMemoryCatalogRepository]:
    adapter = LanceAdapter(root)
    selected_repository = repository or InMemoryCatalogRepository()
    service = LanceCatalogService(adapter, selected_repository, InMemoryDatasetWriterLock())
    service.register_schema(_schema())
    return service, adapter, selected_repository


def test_version_reservation_is_checked_under_the_storage_writer_lock(tmp_path: Path) -> None:
    from threading import Barrier

    service, adapter, _ = _service(tmp_path)
    barrier = Barrier(2)

    def commit(rollout_id: str) -> int | None:
        manifest, steps = _fragment(_schema(), rollout_id)
        barrier.wait()
        try:
            version, _ = service.commit_fragment(manifest, steps, expected_version=1)
            return version.version
        except CatalogVersionConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(commit, ("rollout-a", "rollout-b")))
    assert results.count(1) == 1 and results.count(None) == 1
    # The losing writer must not leave a second immutable storage receipt.
    assert len(adapter.list_commits(_schema())) == 1
    assert len(service.list_versions("dataset-a")) == 1


def test_reserved_version_recovery_reuses_original_commit_after_later_appends(
    tmp_path: Path,
) -> None:
    service, adapter, _ = _service(tmp_path)
    first = _fragment(_schema(), "rollout-first")
    original, _ = service.commit_fragment(*first, expected_version=1)
    service.commit_fragment(*_fragment(_schema(), "rollout-second"), expected_version=2)
    recovered, _ = service.commit_fragment(*first, expected_version=1)
    assert recovered == original
    with pytest.raises(CatalogVersionConflict):
        service.commit_fragment(*first, expected_version=3)
    assert len(adapter.list_commits(_schema())) == 2


@pytest.mark.integration
def test_real_lance_shared_dataset_idempotency_and_compaction(tmp_path: Path) -> None:
    service, adapter, _ = _service(tmp_path)
    snapshot = _schema()
    first = _fragment(snapshot, "rollout-1")
    second = _fragment(snapshot, "rollout-2")

    first_version, _ = service.commit_fragment(*first)
    second_version, _ = service.commit_fragment(*second)
    retry = first[0].model_copy(
        update={
            "attempt_id": "retry-attempt",
            "fragment_uri": "s3://retry/rollout-1.arrow",
        }
    )
    replayed_version, _ = service.commit_fragment(retry, first[1])

    assert replayed_version == first_version
    assert not tuple((tmp_path / "_attempts").rglob("*.lance"))
    assert second_version.dataset_uri.endswith(
        "/project-a/dataset-a/schema-1/30hz/aligned_steps.lance"
    )
    assert len(adapter.list_commits(snapshot)) == 2
    assert [item.version for item in service.list_versions("dataset-a")] == [1, 2]
    before = service.read_steps("dataset-a", "rollout-1", 0, 2).steps

    compacted_lance_version = adapter.compact(snapshot, target_rows_per_fragment=1024)

    assert compacted_lance_version >= second_version.lance_version
    assert service.read_steps("dataset-a", "rollout-1", 0, 2).steps == before
    assert before == first[1]
    assert [(step.rollout_id, step.step_index) for step in before] == [
        ("rollout-1", 0),
        ("rollout-1", 1),
    ]

    third_version, _ = service.commit_fragment(*_fragment(snapshot, "rollout-3"))
    assert third_version.version == 3
    assert third_version.lance_version > compacted_lance_version
    assert service.read_steps("dataset-a", "rollout-1", 0, 2).steps == first[1]


@pytest.mark.integration
def test_real_lance_camera_projection_excludes_other_modality_columns(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)
    fragment = _fragment(_schema(), "rollout-projected")
    service.commit_fragment(*fragment)

    projected = service.read_steps(
        "dataset-a",
        "rollout-projected",
        0,
        2,
        columns=("camera.front",),
    ).steps

    assert [step.modalities for step in projected] == [
        {"camera.front": b"frame-0"},
        {"camera.front": b"frame-1"},
    ]
    assert all(set(step.valid) == {"camera.front"} for step in projected)
    assert all("joint.position" not in step.modalities for step in projected)


class _FailOnceRepository(InMemoryCatalogRepository):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next_commit = True

    def record_commit(self, receipt):  # type: ignore[no-untyped-def]
        if self.fail_next_commit:
            self.fail_next_commit = False
            raise RuntimeError("injected catalog transaction failure")
        super().record_commit(receipt)


@pytest.mark.integration
def test_real_lance_receipt_reconciles_db_failure_without_reappend(tmp_path: Path) -> None:
    repository = _FailOnceRepository()
    service, adapter, _ = _service(tmp_path, repository)
    item = _fragment(_schema(), "rollout-1")

    with pytest.raises(CatalogIndexPendingError):
        service.commit_fragment(*item)
    assert len(repository.pending_reconciliations()) == 1
    physical_versions_before = lance.dataset(adapter.dataset_uri(_schema())).versions()

    version, _ = service.commit_fragment(*item)

    physical_versions_after = lance.dataset(adapter.dataset_uri(_schema())).versions()
    assert version.version == 1
    assert len(physical_versions_after) == len(physical_versions_before) == 1
    assert repository.pending_reconciliations() == ()


@pytest.mark.integration
def test_real_lance_twenty_parallel_rollouts_use_one_writer(tmp_path: Path) -> None:
    service, adapter, _ = _service(tmp_path)
    snapshot = _schema()
    fragments = [_fragment(snapshot, f"rollout-{index}", count=1) for index in range(20)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda item: service.commit_fragment(item[0], item[1]), fragments))

    assert sorted(version.version for version, _ in results) == list(range(1, 21))
    assert len(adapter.list_commits(snapshot)) == 20
    latest = service.version_snapshot("dataset-a")
    identities = {
        (rollout_id, step.step_index)
        for rollout_id in latest.committed_rollouts
        for step in service.read_steps("dataset-a", rollout_id, 0, 1).steps
    }
    assert len(identities) == 20


@pytest.mark.integration
def test_real_lance_step_window_http_wire_is_precise_scoped_and_audited(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)
    snapshot = _schema()
    manifest, steps = _fragment(snapshot, "rollout-p06", count=2)
    version, _ = service.commit_fragment(manifest, steps)
    audit = InMemoryLanceCatalogAuditRecorder()
    configure_lance_catalog(service)
    configure_lance_catalog_audit_recorder(audit)

    app = FastAPI()
    app.include_router(lance_catalog_router)

    @app.middleware("http")
    async def install_auth(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.auth_context = AuthContext(
            subject_id="p06-real-lance-reader",
            project_ids=frozenset({"project-a"}),
            region_codes=frozenset(),
            scope_pairs=frozenset({("project-a", None)}),
            capabilities=frozenset(
                {
                    "annotation_task.read",
                    "annotation_task.claim",
                    "annotation_task.assign",
                    "annotation.edit",
                    "annotation.save",
                    "annotation.submit",
                    "dataset_version.read",
                }
            ),
        )
        return await call_next(request)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/projects/project-a/datasets/dataset-a/rollouts/rollout-p06/steps",
            params={
                "start_step": 0,
                "end_step": 2,
                "version": version.version,
                "columns": "joint.position",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["dataset_version"] == version.version
    assert "camera.front" not in payload["steps"][0]["modalities"]
    assert payload["steps"][0]["modalities"]["joint.position"] == [0.0]
    assert set(payload["steps"][0]["valid"]) == {"joint.position"}
    assert payload["steps"][1]["timestamp_ns"] == "33333333"
    assert [(event.rollout_id, event.returned_step_count) for event in audit.events] == [
        ("rollout-p06", 2)
    ]


@pytest.mark.integration
def test_real_lance_json_modality_round_trips_typed_values(tmp_path: Path) -> None:
    snapshot = DatasetSchemaSnapshot.create(
        project_id="project-a",
        dataset_id="dataset-json",
        schema_snapshot_id="schema-json",
        frequency_hz=30,
        fields={"joint.dynamic": "json", "event.dynamic": "json"},
    )
    adapter = LanceAdapter(tmp_path)
    repository = InMemoryCatalogRepository()
    service = LanceCatalogService(adapter, repository, InMemoryDatasetWriterLock())
    service.register_schema(snapshot)
    steps = (
        StepRecord(
            rollout_id="rollout-json",
            step_index=0,
            timestamp_ns=0,
            modalities={
                "joint.dynamic": [0.1, 0.2, -0.3],
                "event.dynamic": {"label": "grasp", "confidence": 0.95},
            },
            source_timestamps_ns={"joint.dynamic": (0,), "event.dynamic": (0,)},
            time_error_ns={"joint.dynamic": 0, "event.dynamic": 0},
            valid={"joint.dynamic": True, "event.dynamic": True},
            repeated={"joint.dynamic": False, "event.dynamic": False},
        ),
    )
    manifest = AlignedFragmentManifestV1(
        project_id=snapshot.project_id,
        dataset_id=snapshot.dataset_id,
        schema_snapshot_id=snapshot.schema_snapshot_id,
        schema_fingerprint=snapshot.fingerprint,
        frequency_hz=snapshot.frequency_hz,
        rollout_id="rollout-json",
        source_sha256=hashlib.sha256(b"rollout-json").hexdigest(),
        converter_version="aligner-json-1",
        attempt_id="attempt-json",
        fragment_uri="s3://alignment-staging/rollout-json.arrow",
        step_count=1,
        content_hash=compute_fragment_hash(steps),
    )

    version, _ready = service.commit_fragment(manifest, steps)

    assert service.read_steps("dataset-json", "rollout-json", 0, 1).steps == steps
    physical = lance.dataset(adapter.dataset_uri(snapshot), version=version.lance_version)
    physical_modalities = physical.to_table().to_pylist()[0]["modalities"]
    assert physical_modalities == {
        "event.dynamic": '{"confidence":0.95,"label":"grasp"}',
        "joint.dynamic": "[0.1,0.2,-0.3]",
    }
