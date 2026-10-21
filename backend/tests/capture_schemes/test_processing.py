import copy
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from hc_data_platform.capture_schemes.processing import validate_lerobot, validate_snapshot
from hc_data_platform.capture_schemes.schema import semantic_schema
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot
from hc_data_platform.lerobot_imports.reader import read_episode

from .processing_fixture import SOURCES, make_source, output_for, snapshot


@pytest.mark.parametrize("variant", range(4))
def test_complete_x1_definitions_and_exact_timeline(tmp_path, variant):
    info = make_source(tmp_path / "source", variant, count=167 if variant < 2 else 8)
    output = output_for(info)
    evidence = validate_lerobot(
        tmp_path / "source", 0, output, snapshot(output)["effective_adapter"]
    )
    assert evidence["frame_count"] == info["total_frames"]
    assert evidence["frequency_hz"] == 15
    assert all(evidence["presence"].values())
    profile, _, episode = read_episode(tmp_path / "source", 0, preserve_features=True)
    assert len(profile.state_names) == (29 if variant < 2 else 21)
    assert len(profile.action_names) == 19
    assert episode.relative_timestamps_ns[-1] == round((info["total_frames"] - 1) * 1e9 / 15)
    assert bool(profile.cameras) == (variant >= 2)


@pytest.mark.parametrize("change", ["action_order", "source_valid_order", "unit", "shape", "dtype"])
def test_metadata_semantics_are_not_dimension_or_client_fingerprint(tmp_path, change):
    info = make_source(tmp_path / "source", 0, count=8)
    expected = output_for(info)
    if change.endswith("order"):
        name = "action" if change == "action_order" else "observation.source_valid"
        info["features"][name]["names"].reverse()
    elif change == "unit":
        info["features"]["action"]["units"] = ["wrong"] * 19
    else:
        info["features"]["action"][change] = [18] if change == "shape" else "float64"
    info["config_fingerprint"] = "same-untrusted-client-value"
    (tmp_path / "source/meta/info.json").write_text(json.dumps(info))
    with pytest.raises(ProblemException) as error:
        validate_lerobot(tmp_path / "source", 0, expected, snapshot(expected)["effective_adapter"])
    assert error.value.problem.code == "CAPTURE_STRUCTURE_MISMATCH"


@pytest.mark.parametrize("corruption", ["aux_dtype", "aux_shape", "validity", "timeline"])
def test_actual_parquet_columns_are_checked_even_when_metadata_matches(tmp_path, corruption):
    info = make_source(tmp_path / "source", 0, count=8)
    path = tmp_path / "source/data/chunk-000/file-000.parquet"
    table = pq.read_table(path)
    key = "observation.source_valid" if corruption == "validity" else "observation.source_age"
    if corruption == "timeline":
        key = "timestamp"
        values = table[key].to_pylist()
        values[3] = values[2]
        replacement = pa.array(values, type=pa.float32())
    elif corruption == "aux_dtype":
        replacement = pa.array(table[key].to_pylist(), type=pa.list_(pa.float64(), 9))
    else:
        values = table[key].to_pylist()
        if corruption == "aux_shape":
            values[2] = values[2][:-1]
        else:
            values[2][0] = 0.5
        replacement = pa.array(values, type=pa.list_(pa.float32()))
    table = table.set_column(table.schema.get_field_index(key), key, replacement)
    pq.write_table(table, path)
    output = output_for(info)
    with pytest.raises(ValueError):
        validate_lerobot(tmp_path / "source", 0, output, snapshot(output)["effective_adapter"])


def test_full_identity_versioning_and_legacy_bytes():
    old = DatasetSchemaSnapshot.create(
        project_id="p",
        dataset_id="d",
        schema_snapshot_id="s",
        frequency_hz=15,
        fields={"topic": "json"},
    )
    assert "semantic_contract" not in old.model_dump(mode="json")
    assert DatasetSchemaSnapshot.model_validate(old.model_dump(mode="json")) == old
    outputs = [output_for(item["info"]).model_dump(mode="json") for item in SOURCES]
    identities = [semantic_schema("p", "d", output, {"topic": "json"}) for output in outputs]
    assert len({item.fingerprint for item in identities}) == 3
    assert identities[0].fingerprint != identities[1].fingerprint  # 29/19 validity axis order
    assert identities[2] == identities[3]  # Task 14 new input and Task 16 really match.


def test_snapshot_hash_and_scope_are_verified():
    output = output_for(SOURCES[0]["info"])
    frozen = snapshot(output)
    raw = {k: frozen[k] for k in ("dataset_id", "collection_task_id", "robot_id", "source_format")}
    assert validate_snapshot(frozen, ("org", "project", "global"), raw) == frozen
    changed = copy.deepcopy(frozen)
    changed["output"]["features"]["action"]["names"].reverse()
    assert (
        validate_snapshot(changed, ("org", "project", "global"), raw)["error"]
        == "CAPTURE_VERSION_MISMATCH"
    )
    assert (
        validate_snapshot(frozen, ("other", "project", "global"), raw)["error"]
        == "CAPTURE_VERSION_MISMATCH"
    )


@pytest.mark.parametrize("missing", ["plan_revision", "mapping_sha256", "scheme", "recording_id"])
def test_snapshot_with_matching_digest_still_requires_complete_lineage(missing):
    from hc_data_platform.capture_schemes.common import digest

    frozen = snapshot(output_for(SOURCES[0]["info"]))
    raw = {k: frozen[k] for k in ("dataset_id", "collection_task_id", "robot_id", "source_format")}
    del frozen[missing]
    del frozen["snapshot_sha256"]
    frozen["snapshot_sha256"] = digest(frozen)
    assert (
        validate_snapshot(frozen, ("org", "project", "global"), raw)["error"]
        == "CAPTURE_VERSION_MISMATCH"
    )


def test_frozen_feature_mapping_interprets_names_without_mutating_raw(tmp_path):
    info = make_source(tmp_path / "source", 0, count=8)
    output = output_for(info)
    path = tmp_path / "source/data/chunk-000/file-000.parquet"
    table = pq.read_table(path)
    columns = table.column_names
    columns[columns.index("action")] = "device.commands"
    pq.write_table(table.rename_columns(columns), path)
    info["features"]["device.commands"] = info["features"].pop("action")
    info_path = tmp_path / "source/meta/info.json"
    info_path.write_text(json.dumps(info))
    before = info_path.read_bytes()
    adapter = {
        **snapshot(output)["effective_adapter"],
        "feature_sources": {"action": "device.commands"},
    }
    assert validate_lerobot(tmp_path / "source", 0, output, adapter)["frame_count"] == 8
    assert info_path.read_bytes() == before


def test_media_declared_dimensions_and_decode_must_match(tmp_path):
    info = make_source(tmp_path / "source", 2, count=8)
    output = output_for(info)
    video = next((tmp_path / "source/videos").rglob("*.mp4"))
    video.write_bytes(video.read_bytes()[:80])
    with pytest.raises(ValueError):
        validate_lerobot(tmp_path / "source", 0, output, snapshot(output)["effective_adapter"])


@pytest.mark.parametrize("variant", [0, 1, 2])
def test_source_validity_uses_frozen_feature_names_not_positional_assumptions(variant):
    from hc_data_platform.episode_store.capture_validity import feature_mask

    output = output_for(SOURCES[variant]["info"]).model_dump(mode="json")
    validity = output["features"]["observation.source_valid"]
    flags = [1] * validity["shape"][0]
    flags[validity["names"].index("action")] = 0
    values = {"observation.source_valid": flags, "observation.source_available": [1] * len(flags)}
    assert not any(feature_mask("action", output, values))
    assert all(feature_mask("observation.state", output, values))
