from __future__ import annotations

import json
from pathlib import Path

import pytest

from hc_data_platform.lance_catalog import (
    DatasetSchemaSnapshot,
    SchemaCompilationError,
    compile_arrow_schema,
    compute_schema_fingerprint,
)

pytest.importorskip("pyarrow")


def test_schema_fingerprint_matches_pre_decoupling_bytes() -> None:
    archived = json.loads(
        (Path(__file__).parent / "fixtures/schema-fingerprint-v1.json").read_text()
    )
    assert compute_schema_fingerprint(archived["fields"]) == archived["sha256"]
    snapshot = DatasetSchemaSnapshot.create(
        project_id="p",
        dataset_id="d",
        schema_snapshot_id="s",
        frequency_hz=30,
        fields=archived["fields"],
    )
    assert (
        compile_arrow_schema(snapshot).metadata[b"hc.schema.fingerprint"].decode()
        == archived["sha256"]
    )


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


def test_json_modality_has_an_explicit_logical_type_and_physical_encoding() -> None:
    snapshot = DatasetSchemaSnapshot.create(
        project_id="project-a",
        dataset_id="dataset-a",
        schema_snapshot_id="schema-json",
        frequency_hz=30,
        fields={"joint.dynamic": "json"},
    )

    compiled = compile_arrow_schema(snapshot)

    assert str(compiled.field("modalities").type.field("joint.dynamic").type) == "string"
    assert compiled.metadata[b"hc.schema.json_modalities"] == b'["joint.dynamic"]'


def test_unsupported_arrow_type_is_rejected() -> None:
    with pytest.raises(SchemaCompilationError, match="unsupported"):
        DatasetSchemaSnapshot.create(
            project_id="project-a",
            dataset_id="dataset-a",
            schema_snapshot_id="schema-1",
            frequency_hz=30,
            fields={"bad": "not-a-real-arrow-type"},
        )
