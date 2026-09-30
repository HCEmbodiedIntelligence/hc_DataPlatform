"""Network-disabled official reader plus independent migration logical-hash check.

No platform imports. Use a complete committed copy and its frozen result.json.
The fixture CLI labels synthetic input; historical callers must record their
audited provenance and supply original files for independent RGB comparison.
"""

import hashlib
import importlib.util
import json
import sys
from fractions import Fraction
from importlib.metadata import version
from pathlib import Path

import av
import pyarrow.parquet as pq

spec = importlib.util.spec_from_file_location(
    "complete_package_check", Path(__file__).with_name("read_complete_delivery.py")
)
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


def document(path):
    return json.loads(path.read_bytes())


def source_rgb_hash(path, indexes, fps):
    """Independently select original decoded source frames, without platform code."""
    checksum, selected = hashlib.sha256(), 0
    rate = Fraction(str(fps))
    with av.open(str(path)) as source:
        stream = source.streams.video[0]
        if indexes[0]:
            source.seek(
                int(Fraction(indexes[0]) / rate / stream.time_base), stream=stream, backward=True
            )
        for frame in source.decode(stream):
            index = round(frame.pts * frame.time_base * rate)
            assert abs(frame.pts * frame.time_base - Fraction(index) / rate) <= Fraction(1, 1000000)
            assert index <= indexes[selected], "source decode skipped a selected frame"
            if index == indexes[selected]:
                pixels = frame.to_ndarray(format="rgb24").tobytes()
                while selected < len(indexes) and indexes[selected] == index:
                    checksum.update(pixels)
                    selected += 1
                if selected == len(indexes):
                    break
    assert selected == len(indexes)
    return checksum.hexdigest()


def check(root, *, source_files=None, video_backend="pyav"):
    reference = document(root / "result.json")
    plan, receipt, report = (reference[k] for k in ("plan", "receipt", "report"))
    plan_hash = hashlib.sha256(
        json.dumps(plan, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    package = root / "episode"
    result = reader.read_package(package, receipt, video_backend=video_backend)
    assert document(package / "hc/migration.json") == report
    assert report["plan_sha256"] == plan_hash
    assert document(package / "hc/manifest.json")["input"]["configuration_sha256"] == plan_hash
    assert plan["legacy"] == report["legacy"]
    assert plan["legacy"]["identity"] == receipt["data"]["identity"]
    assert plan["legacy"]["frame_count"] == receipt["data"]["frame_count"]
    assert not report["annotation_history_rewritten"]
    assert not report["review_automatically_transferred"]
    info = document(package / "meta/info.json")
    rows = pq.read_table(package / "data").to_pylist()
    traces = pq.read_table(package / "hc/source_frames.parquet").to_pylist()
    logical = []
    for row, trace in zip(rows, traces, strict=True):
        assert row["hc.timestamp_ns"] == trace["timestamp_ns"]
        assert row["frame_index"] == trace["frame_index"]
        modalities = {}
        for name, feature in info["hc.feature_mapping"].items():
            if info["features"][feature]["dtype"] == "video":
                value = json.loads(row["hc.source_ref." + feature])
            else:
                value = row[feature]
                if info["hc.source_shapes"][feature] == [1]:
                    value = [value]
                encoding = info.get("hc.structured_features", {}).get(feature)
                if encoding:
                    if encoding["encoding"] == "json":
                        value = json.loads(value)
                    elif encoding["encoding"] == "vector":
                        value = {
                            encoding["value_key"]: value,
                            **{key: encoding[key] for key in ("names", "units") if key in encoding},
                        }
                    elif encoding["encoding"] == "pose":
                        value = {"position_xyz": value[:3], "orientation_wxyz": value[3:]}
                    else:
                        raise AssertionError("unsupported fixture encoding")
            modalities[name] = value
        logical.append(
            {
                "schema_version": "1",
                "rollout_id": plan["legacy"]["identity"]["rollout_id"],
                "step_index": row["frame_index"],
                "timestamp_ns": row["hc.timestamp_ns"],
                "sample_valid": row["hc.sample_valid"],
                "modalities": modalities,
                **{
                    key: json.loads(trace[key])
                    for key in ("source_timestamps_ns", "time_error_ns", "valid", "repeated")
                },
            }
        )
    logical_hash = hashlib.sha256(
        json.dumps(
            logical, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    assert logical_hash == plan["legacy"]["content_hash"] == report["converted_logical_sha256"]
    for feature, media in plan["asset_contract"]["videos"].items():
        assert reader.sha(package / f"videos/{feature}/chunk-000/file-000.mp4") == media["sha256"]
        assert report["media"][feature]["requantized"] is False
        preparation = plan["metadata"].get("source", {}).get("media_preparation", {}).get(feature)
        if preparation is None:
            assert report["media"][feature]["transcoded"] is False
        else:
            assert preparation == report["media"][feature]["source_preparation"]
            assert preparation["decoded_pixels_exact"]
            logical_name = next(
                name for name, key in info["hc.feature_mapping"].items() if key == feature
            )
            references = [row["modalities"][logical_name] for row in logical]
            indexes = [ref["frame_index"] for ref in references]
            assert (
                hashlib.sha256(json.dumps(indexes, separators=(",", ":")).encode()).hexdigest()
                == preparation["frame_indexes_sha256"]
            )
            checksum = hashlib.sha256()
            with av.open(str(package / f"videos/{feature}/chunk-000/file-000.mp4")) as video:
                for frame in video.decode(video=0):
                    checksum.update(frame.to_ndarray(format="rgb24").tobytes())
            assert (
                checksum.hexdigest()
                == preparation["output_rgb_sha256"]
                == preparation["selected_source_rgb_sha256"]
            )
            if source_files is not None:
                original = preparation["original_reference"]
                path = source_files[original["object_key"]]
                assert reader.sha(path) == original["content_sha256"]
                assert path.stat().st_size == original["size_bytes"]
                assert source_rgb_hash(path, indexes, original["fps"]) == checksum.hexdigest()
    result.update(
        plan_sha256=plan_hash,
        legacy=plan["legacy"],
        independent_logical_sha256=logical_hash,
        original_video_file_hashes_verified=True,
        rgb_and_depth_original_file_hashes_verified=bool(plan["required_depth_features"])
        and len(plan["asset_contract"]["videos"]) > len(plan["required_depth_features"]),
        review_automatically_transferred=False,
        shared_original_pixels_independently_verified=source_files is not None
        and bool(plan["metadata"].get("source", {}).get("media_preparation")),
        input_directory=str(root),
    )
    return result


def main():
    assert version("lerobot") == "0.6.1"
    results = [check(Path(path)) for path in sys.argv[2:]]
    assert not any(name.startswith("hc_data_platform") for name in sys.modules)
    report = {
        "lerobot": version("lerobot"),
        "network": "disabled by container",
        "platform_imported": False,
        "official_reader_and_dataloader": True,
        "packages": results,
        "synthetic_input": True,
        "real_historical_acceptance": False,
        "model_depth_training_support": "NOT_VALIDATED",
    }
    Path(sys.argv[1]).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
