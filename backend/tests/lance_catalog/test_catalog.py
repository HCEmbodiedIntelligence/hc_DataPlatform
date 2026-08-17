from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest

from hc_data_platform.lance_catalog import (
    AlignedFragmentManifestV1,
    CatalogConflictError,
    CatalogIndexPendingError,
    DatasetSchemaSnapshot,
    InMemoryLanceCatalog,
    SchemaIncompatibleError,
    StepRecord,
    compute_fragment_hash,
)


def schema(
    *, project_id: str = "project-a", dataset_id: str = "dataset-a"
) -> DatasetSchemaSnapshot:
    return DatasetSchemaSnapshot.create(
        project_id=project_id,
        dataset_id=dataset_id,
        schema_snapshot_id="schema-1",
        frequency_hz=30,
        fields={"camera.front": "binary", "joint.position": "list<float32>"},
    )


def fragment(
    rollout_id: str,
    count: int = 3,
    *,
    project_id: str = "project-a",
    dataset_id: str = "dataset-a",
    snapshot: DatasetSchemaSnapshot | None = None,
) -> tuple[AlignedFragmentManifestV1, tuple[StepRecord, ...]]:
    selected_schema = snapshot or schema(project_id=project_id, dataset_id=dataset_id)
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
                "camera.front": index * 33_333_333,
                "joint.position": index * 33_333_333,
            },
            time_error_ns={"camera.front": 0, "joint.position": 0},
            valid={"camera.front": True, "joint.position": True},
            repeated={"camera.front": False, "joint.position": False},
        )
        for index in range(count)
    )
    manifest = AlignedFragmentManifestV1(
        project_id=project_id,
        dataset_id=dataset_id,
        schema_snapshot_id=selected_schema.schema_snapshot_id,
        schema_fingerprint=selected_schema.fingerprint,
        frequency_hz=selected_schema.frequency_hz,
        rollout_id=rollout_id,
        source_sha256=hashlib.sha256(rollout_id.encode()).hexdigest(),
        converter_version="aligner-1.0.0",
        attempt_id=f"attempt-{rollout_id}",
        fragment_uri=f"s3://staging/{rollout_id}.arrow",
        step_count=len(steps),
        content_hash=compute_fragment_hash(steps),
    )
    return manifest, steps


def test_commit_is_idempotent_and_steps_use_logical_identity() -> None:
    catalog = InMemoryLanceCatalog()
    catalog.register_schema(schema())
    manifest, steps = fragment("rollout-1")

    first_version, first_event = catalog.commit_fragment(manifest, steps)
    retry = manifest.model_copy(
        update={"attempt_id": "attempt-retry", "fragment_uri": "s3://retry/fragment.arrow"}
    )
    second_version, second_event = catalog.commit_fragment(retry, steps)

    assert first_version == second_version
    assert first_event == second_event
    assert len(catalog.list_versions("dataset-a")) == 1
    window = catalog.read_steps("dataset-a", "rollout-1", 1, 3)
    assert [step.step_index for step in window.steps] == [1, 2]
    assert catalog.lineage("dataset-a", "rollout-1").source_sha256 == manifest.source_sha256
    assert catalog.version_snapshot("dataset-a") == first_version


def test_idempotency_key_cannot_be_reused_for_different_content() -> None:
    catalog = InMemoryLanceCatalog()
    catalog.register_schema(schema())
    manifest, steps = fragment("rollout-1")
    catalog.commit_fragment(manifest, steps)
    changed = manifest.model_copy(update={"content_hash": "f" * 64})

    with pytest.raises(CatalogConflictError):
        catalog.commit_fragment(changed, steps)


def test_schema_mismatch_is_rejected_before_storage_commit() -> None:
    catalog = InMemoryLanceCatalog()
    catalog.register_schema(schema())
    manifest, steps = fragment("rollout-1")
    incompatible = manifest.model_copy(update={"frequency_hz": 20})

    with pytest.raises(SchemaIncompatibleError):
        catalog.commit_fragment(incompatible, steps)
    assert catalog.current_version("dataset-a") is None

    other_schema = DatasetSchemaSnapshot.create(
        project_id="project-a",
        dataset_id="dataset-a",
        schema_snapshot_id="schema-2",
        frequency_hz=30,
        fields={"joint.position": "list<float64>"},
    )
    with pytest.raises(SchemaIncompatibleError):
        catalog.register_schema(other_schema)


def test_content_contiguous_and_exact_schema_validation() -> None:
    catalog = InMemoryLanceCatalog()
    catalog.register_schema(schema())
    manifest, steps = fragment("rollout-1")
    invalid_steps = (steps[0], steps[2])
    invalid_manifest = manifest.model_copy(
        update={
            "step_count": 2,
            "content_hash": compute_fragment_hash(invalid_steps),
        }
    )

    with pytest.raises(CatalogConflictError, match="contiguous"):
        catalog.commit_fragment(invalid_manifest, invalid_steps)

    missing_modality = (steps[0].model_copy(update={"modalities": {"camera.front": b"x"}}),)
    missing_manifest = manifest.model_copy(
        update={
            "step_count": 1,
            "content_hash": compute_fragment_hash(missing_modality),
        }
    )
    with pytest.raises(SchemaIncompatibleError, match="modalities"):
        catalog.commit_fragment(missing_manifest, missing_modality)


def test_catalog_failure_is_reconciled_without_duplicate_append() -> None:
    catalog = InMemoryLanceCatalog()
    catalog.register_schema(schema())
    manifest, steps = fragment("rollout-1")

    with pytest.raises(CatalogIndexPendingError):
        catalog.commit_fragment(manifest, steps, simulate_catalog_failure=True)
    assert catalog.current_version("dataset-a") is None
    assert len(catalog.pending_reconciliations("dataset-a")) == 1

    version, event = catalog.commit_fragment(manifest, steps)
    assert version.version == 1
    assert event.dataset_version == 1
    assert len(catalog.list_versions("dataset-a")) == 1
    assert catalog.pending_reconciliations("dataset-a") == ()


def test_explicit_reconciliation_restores_catalog_index() -> None:
    catalog = InMemoryLanceCatalog()
    catalog.register_schema(schema())
    manifest, steps = fragment("rollout-1")
    with pytest.raises(CatalogIndexPendingError):
        catalog.commit_fragment(manifest, steps, simulate_catalog_failure=True)

    repaired = catalog.reconcile("dataset-a")

    assert [item.version for item in repaired] == [1]
    assert catalog.read_steps("dataset-a", "rollout-1", 0, 3).steps == steps


def test_twenty_parallel_rollouts_are_serialized_without_duplicate_steps() -> None:
    catalog = InMemoryLanceCatalog()
    catalog.register_schema(schema())
    fragments = [fragment(f"rollout-{index}", 5) for index in range(20)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda item: catalog.commit_fragment(item[0], item[1]), fragments))

    assert sorted(version.version for version, _ in results) == list(range(1, 21))
    current = catalog.current_version("dataset-a")
    assert current is not None
    assert current.version == 20
    assert len(current.committed_rollouts) == 20
    for index in range(20):
        window = catalog.read_steps("dataset-a", f"rollout-{index}", 0, 10, version=20)
        assert [step.step_index for step in window.steps] == [0, 1, 2, 3, 4]


def test_version_is_immutable_and_step_identity_survives_rewrite() -> None:
    catalog = InMemoryLanceCatalog()
    catalog.register_schema(schema())
    first, first_steps = fragment("rollout-1")
    second, second_steps = fragment("rollout-2")
    catalog.commit_fragment(first, first_steps)
    catalog.commit_fragment(second, second_steps)

    assert catalog.read_steps("dataset-a", "rollout-2", 0, 3, version=1).steps == ()
    before = catalog.read_steps("dataset-a", "rollout-1", 0, 3).steps
    catalog.rewrite_storage_layout("dataset-a")
    after = catalog.read_steps("dataset-a", "rollout-1", 0, 3).steps
    assert before == after == first_steps


def test_project_is_part_of_dataset_and_writer_scope() -> None:
    catalog = InMemoryLanceCatalog()
    first_schema = schema(project_id="project-a", dataset_id="shared-name")
    second_schema = schema(project_id="project-b", dataset_id="shared-name")
    catalog.register_schema(first_schema)
    catalog.register_schema(second_schema)
    first = fragment(
        "rollout-a", project_id="project-a", dataset_id="shared-name", snapshot=first_schema
    )
    second = fragment(
        "rollout-b", project_id="project-b", dataset_id="shared-name", snapshot=second_schema
    )

    assert catalog.commit_fragment(*first)[0].version == 1
    assert catalog.commit_fragment(*second)[0].version == 1
    with pytest.raises(KeyError):
        catalog.current_version("shared-name")
