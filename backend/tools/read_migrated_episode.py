"""Network-disabled official reader plus independent migration logical-hash check.

No platform imports. Use the complete committed fixture copy and result.json from
the service exercise; this is not acceptance of real historical recordings.
"""

import hashlib
import importlib.util
import json
import sys
from importlib.metadata import version
from pathlib import Path

import pyarrow.parquet as pq

spec = importlib.util.spec_from_file_location(
    "complete_package_check", Path(__file__).with_name("read_complete_delivery.py")
)
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


def document(path):
    return json.loads(path.read_bytes())


def check(root):
    reference = document(root / "result.json")
    plan, receipt, report = (reference[k] for k in ("plan", "receipt", "report"))
    plan_hash = hashlib.sha256(
        json.dumps(plan, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    package = root / "episode"
    result = reader.read_package(package, receipt)
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
        assert report["media"][feature]["transcoded"] is False
        assert report["media"][feature]["requantized"] is False
    result.update(
        plan_sha256=plan_hash,
        legacy=plan["legacy"],
        independent_logical_sha256=logical_hash,
        original_video_file_hashes_verified=True,
        rgb_and_depth_original_file_hashes_verified=bool(plan["required_depth_features"])
        and len(plan["asset_contract"]["videos"]) > len(plan["required_depth_features"]),
        review_automatically_transferred=False,
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
