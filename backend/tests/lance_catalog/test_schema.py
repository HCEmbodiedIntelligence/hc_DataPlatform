from __future__ import annotations

import pytest

from hc_data_platform.lance_catalog import (
    DatasetSchemaSnapshot,
    SchemaCompilationError,
    compile_arrow_schema,
    compute_schema_fingerprint,
)

pytest.importorskip("pyarrow")


def test_schema_compilation_is_stable_and_order_independent() -> None:
    first_fields = {
        "joint.position": "list<float32>",
        "camera.front": "binary",
        "pose": "struct<x: float64, y: float64>",
    }
    second_fields = dict(reversed(tuple(first_fields.items())))
    assert compute_schema_fingerprint(first_fields) == compute_schema_fingerprint(second_fields)

    snapshot = DatasetSchemaSnapshot.create(
        project_id="project-a",
        dataset_id="dataset-a",
        schema_snapshot_id="schema-1",
        frequency_hz=30,
        fields=first_fields,
    )
    compiled = compile_arrow_schema(snapshot)

    assert compiled.names == [
        "rollout_id",
        "step_index",
        "timestamp_ns",
        "modalities",
        "source_timestamps_ns",
        "time_error_ns",
        "valid",
        "repeated",
        "sample_valid",
    ]
    assert compiled.field("modalities").type.names == [
        "camera.front",
        "joint.position",
        "pose",
    ]
    assert compiled.metadata[b"hc.schema.fingerprint"] == snapshot.fingerprint.encode()


def test_unsupported_arrow_type_is_rejected() -> None:
    with pytest.raises(SchemaCompilationError, match="unsupported"):
        DatasetSchemaSnapshot.create(
            project_id="project-a",
            dataset_id="dataset-a",
            schema_snapshot_id="schema-1",
            frequency_hz=30,
            fields={"bad": "not-a-real-arrow-type"},
        )
