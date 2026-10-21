"""Publication validates complete committed bytes, independently of client pass flags."""

import hashlib
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from hc_data_platform.capture_schemes.models import Sample
from hc_data_platform.capture_schemes.samples import StoredSampleValidator
from hc_data_platform.ingest.ports import InMemoryObjectStorage
from hc_data_platform.ingest.raw_sources import (
    CommittedRawSourceGraph,
    InMemoryRawSourceRepository,
    RawIngestJob,
    RawSource,
)

from .processing_fixture import make_source, output_for
from .test_mcap_processing import config, contract, joint_source


def stored(root, source_format, *, organization_id="org", robot_id="r"):
    storage, repository = InMemoryObjectStorage(), InMemoryRawSourceRepository()
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            relative, body = path.relative_to(root).as_posix(), path.read_bytes()
            storage.objects["source/" + relative] = body
            files.append(
                {"path": relative, "size": len(body), "sha256": hashlib.sha256(body).hexdigest()}
            )
    raw = RawSource(
        organization_id=organization_id,
        project_id="p",
        region_code="global",
        raw_source_id="sample",
        upload_id="sample",
        robot_id=robot_id,
        dataset_id="dataset_samples",
        source_format=source_format,
        source_format_version="native",
        content_hash="a" * 64,
        manifest_key="manifest.json",
        storage_prefix="source",
        file_count=len(files),
        total_bytes=sum(f["size"] for f in files),
    )
    storage.put_json(
        "manifest.json", {"content_hash": raw.content_hash, "files": files}, if_none_match=True
    )
    repository.register_committed(
        CommittedRawSourceGraph(
            source=raw,
            job=RawIngestJob(
                organization_id=organization_id,
                project_id="p",
                region_code="global",
                raw_source_id="sample",
                job_id="sample",
                job_type="RAW_STORAGE",
                adapter_name="raw_storage",
            ),
        )
    )
    sample = Sample(
        project_id="p",
        region_code="global",
        raw_source_id="sample",
        source_format=source_format,
        source_path="source.mcap" if source_format == "MCAP" else "meta/info.json",
    )
    return StoredSampleValidator(storage, repository), sample, storage


def lerobot_document(info):
    return {
        "output": output_for(info).model_dump(mode="json"),
        "adapters": [
            {
                "source_format": "LEROBOT_V3",
                "parser_version": "lerobot-v3/1",
                "codebase_version": "v3.0",
                "feature_sources": {},
            }
        ],
    }


@pytest.mark.parametrize("variant", [0, 2])
def test_publication_decodes_joint_and_visual_lerobot_samples(tmp_path, variant):
    root = tmp_path / "source"
    info = make_source(root, variant, count=3)
    validator, sample, _ = stored(root, "LEROBOT_V3")
    result = validator.validate("org", lerobot_document(info), sample)
    assert result["status"] == "PASSED", result
    assert result["frame_count"] == 3 and result["frequency_hz"] == 15
    assert len(result["validated_episodes"]) == 1


def test_actual_auxiliary_column_corruption_cannot_pass_matching_metadata(tmp_path):
    root = tmp_path / "source"
    info = make_source(root, 0, count=3)
    path = root / "data/chunk-000/file-000.parquet"
    table = pq.read_table(path)
    name = "action.base.velocity"
    index = table.schema.get_field_index(name)
    rows = table.column(name).to_pylist()
    rows[-1][0] = float("nan")
    table = table.set_column(
        index, table.schema.field(index), pa.array(rows, type=table.schema.field(index).type)
    )
    pq.write_table(table, path)
    validator, sample, _ = stored(root, "LEROBOT_V3")
    result = validator.validate("org", lerobot_document(info), sample)
    assert result["status"] == "FAILED" and result["error_code"] == "CAPTURE_SOURCE_INVALID"


def test_other_episode_cannot_be_omitted_from_publication_validation(tmp_path):
    root = tmp_path / "source"
    info = make_source(root, 0, count=3)
    info.update(total_episodes=2, total_frames=6)
    (root / "meta/info.json").write_text(json.dumps(info))
    path = root / "meta/episodes/chunk-000/file-000.parquet"
    first = pq.read_table(path).to_pylist()[0]
    second = {
        **first,
        "episode_index": 1,
        "data/file_index": 1,
        "dataset_from_index": 3,
        "dataset_to_index": 6,
    }
    pq.write_table(pa.Table.from_pylist([first, second]), path)
    validator, sample, _ = stored(root, "LEROBOT_V3")
    result = validator.validate("org", lerobot_document(info), sample)
    assert result["status"] == "FAILED" and result["error_code"] == "CAPTURE_SOURCE_INVALID"
    assert "manifest" in result["error_detail"]


def test_mcap_publication_uses_independent_axes_and_actual_output_dtype(tmp_path):
    joint_source(tmp_path / "source.mcap")
    validator, sample, storage = stored(tmp_path, "MCAP")
    document = {
        "output": contract().model_dump(mode="json"),
        "adapters": [
            {
                "source_format": "MCAP",
                "parser_version": "native-mcap-recording/2",
                "configuration": config(),
            }
        ],
    }
    result = validator.validate("org", document, sample)
    assert result["status"] == "PASSED", result
    assert result["frame_count"] == 167 and result["frequency_hz"] == 15
    assert result["presence"]["observation.images.head"] is False
    document["output"]["features"]["action"]["dtype"] = "float32"
    result = validator.validate("org", document, sample)
    assert result["status"] == "FAILED" and result["error_code"] == "CAPTURE_STRUCTURE_MISMATCH"
    assert any(item["path"] == "action.dtype" for item in result["differences"])
    storage.objects["source/source.mcap"] += b"changed"
    result = validator.validate("org", document, sample)
    assert result["error_code"] == "CAPTURE_SOURCE_INVALID"
