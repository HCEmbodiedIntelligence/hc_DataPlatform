"""Offline official reader and independent parent-Tag/frame/mask verification."""

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


def document(root, name):
    return json.loads((root / name).read_bytes())


def check(root):
    commit = document(root, "hc/commit.json")
    result = reader.read_package(root, commit)
    plan = document(root, "hc/derivation/plan.json")
    parent = document(root, "hc/derivation/parent/commit.json")
    annotation_path = root / "hc/derivation/parent/annotation.json"
    annotation = json.loads(annotation_path.read_bytes())
    binding = plan["annotation"]["binding"]
    assert reader.sha(annotation_path) == binding["annotation_file_sha256"]
    assert (
        annotation["data_binding"]["data_ref"]
        == binding["data"]
        == parent["data"]
        == plan["parent"]
    )
    assert reader.sha(root / "hc/derivation/parent/manifest.json") == parent["manifest_sha256"]
    assert annotation["revision"]["revision"] == binding["revision"]
    assert annotation["revision"]["content_hash"] == binding["revision_content_hash"]
    tag = next(
        t for t in annotation["revision"]["tags"] if t["annotation_id"] == plan["annotation_id"]
    )
    assert (tag["start_step"], tag["end_step"]) == (plan["start_step"], plan["end_step"])
    data = commit["data"]
    assert data["identity"]["episode_id"] != parent["data"]["identity"]["episode_id"]
    assert data["identity"]["rollout_id"] != parent["data"]["identity"]["rollout_id"]
    assert data["frame_count"] == tag["end_step"] - tag["start_step"]
    rows = pq.read_table(root / "data").to_pylist()
    trace = pq.read_table(root / "hc/derivation/frames.parquet").to_pylist()
    source_frames = pq.read_table(root / "hc/source_frames.parquet").to_pylist()
    assert len(trace) == len(source_frames) == len(rows) == data["frame_count"]
    for index, (row, mapping, source) in enumerate(zip(rows, trace, source_frames, strict=True)):
        assert (
            index
            == row["frame_index"]
            == row["index"]
            == mapping["frame_index"]
            == source["frame_index"]
        )
        assert mapping["parent_frame_index"] == tag["start_step"] + index
        assert mapping["parent_data_version_id"] == plan["parent"]["data_version_id"]
        assert mapping["parent_timestamp_ns"] == row["hc.timestamp_ns"] == source["timestamp_ns"]
    report = document(root, "hc/conversion_report.json")
    assert report["requantized"] is False
    assert all(m["requantized"] is False for m in report["media"])
    quality = document(root, "hc/quality.json")
    assert quality["parent_decision_inherited"] is False
    assert document(root, "hc/source.json")["derivation"]["child_approvals_inherited"] is False
    configuration = json.dumps(
        plan, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    manifest = document(root, "hc/manifest.json")
    assert manifest["input"]["configuration_sha256"] == hashlib.sha256(configuration).hexdigest()
    if "quality" in plan["encoding"]:
        contract = plan["encoding"]["quality"]
        qc, evidence = quality["quality_report"], document(root, "hc/quality_rules.json")

        def quality_hash(value):
            return hashlib.sha256(
                json.dumps(
                    value,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode()
            ).hexdigest()

        for value in (qc, evidence):
            assert (
                quality_hash({k: v for k, v in value.items() if k != "content_sha256"})
                == value["content_sha256"]
            )
            assert value["rollout_id"] == data["identity"]["rollout_id"]
            assert value["source_sha256"] == manifest["input"]["source_sha256"]
            assert (value["start_ns"], value["end_ns"]) == (
                contract["start_ns"],
                contract["end_ns"],
            )
        assert quality["input_contract"] == contract
        assert evidence["profile"] == contract["profile"]
        assert quality_hash(evidence["profile"]) == qc["profile_sha256"]
        assert evidence["report_sha256"] == qc["content_sha256"]
        assert all(m["expected_frame_count"] == data["frame_count"] for m in qc["topic_metrics"])
        result["independent_qc"] = {
            "status": qc["status"],
            "scope": contract["scope"],
            "report_sha256": qc["content_sha256"],
            "evidence_sha256": evidence["content_sha256"],
            "rule_codes": sorted({rule["code"] for rule in evidence["rules"]}),
        }
    result.update(
        parent_data_version_id=plan["parent"]["data_version_id"],
        parent_annotation_revision=binding["revision"],
        annotation_id=plan["annotation_id"],
        interval=[plan["start_step"], plan["end_step"]],
        parent_tags_preserved=len(annotation["revision"]["tags"]),
        parent_frame_and_nanosecond_trace_verified=True,
        new_episode_identity=True,
        parent_approvals_inherited=False,
        requantized=False,
        primary_manifest_sha256=commit["manifest_sha256"],
    )
    return result


def main():
    assert version("lerobot") == "0.6.1"
    result = {
        "lerobot": version("lerobot"),
        "network": "disabled by container",
        "official_reader_and_dataloader": True,
        "episodes": [check(Path(p)) for p in sys.argv[2:]],
        "synthetic_input": True,
        "queue_api_browser_verified": False,
        "human_acceptance": False,
        "model_depth_training_support": "NOT_VALIDATED",
    }
    assert not any(name.startswith("hc_data_platform") for name in sys.modules)
    result["platform_imported"] = False
    Path(sys.argv[1]).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
