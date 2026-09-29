"""Offline official collection reader; HC masks are applied independently.

Run in the pinned, network-disabled reader container on trusted writer artifacts.
The input set is a software fixture, not real-sensor or model-training acceptance.
"""

import hashlib
import io
import json
import sys
from importlib.metadata import version
from pathlib import Path

import av
import numpy as np
import pyarrow.parquet as pq
from lerobot.datasets.lerobot_dataset import LeRobotDataset
from PIL import Image
from torch.utils.data import DataLoader


def sha(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            checksum.update(block)
    return checksum.hexdigest()


def document(root, path):
    return json.loads((root / path).read_bytes())


def read(root):
    manifest = document(root, "hc/collection_manifest.json")
    inventory = document(root, "hc/collection.json")
    assert manifest["format"] == "hc-lerobot-collection/1"
    assert inventory["is_reassembled_collection"] is True
    assert inventory["requantized"] is False and inventory["transcoded"] is False
    for entry in manifest["files"]:
        path = root / entry["path"]
        assert path.stat().st_size == entry["size"] and sha(path) == entry["sha256"]
    info = document(root, "meta/info.json")
    tasks = {
        r["task_index"]: r["task"] for r in pq.read_table(root / "meta/tasks.parquet").to_pylist()
    }
    rows = pq.read_table(root / "data").to_pylist()
    frames = pq.read_table(root / "hc/collection_frames.parquet").to_pylist()
    episodes = pq.read_table(root / "meta/episodes").to_pylist()
    assert len(episodes) == len(manifest["members"]) >= 2
    assert len(rows) == len(frames) == info["total_frames"] == manifest["total_frames"]
    decoded, flags, source_maps, expected_mm = {}, {}, {}, {}
    annotation_count = 0
    subtasks = (
        [json.loads(line) for line in (root / "hc/subtasks.jsonl").read_bytes().splitlines()]
        if (root / "hc/subtasks.jsonl").exists()
        else []
    )
    for member, episode in zip(manifest["members"], episodes, strict=True):
        index = member["episode_index"]
        assert episode["episode_index"] == index
        begin, end = member["dataset_from_index"], member["dataset_to_index"]
        assert begin == episode["dataset_from_index"] and end == episode["dataset_to_index"]
        assert end - begin == member["data"]["frame_count"] == episode["length"]
        if member["annotation"]:
            path = root / member["attachments_root"] / "hc/annotation.json"
            assert sha(path) == member["annotation"]["annotation_file_sha256"]
            annotation = json.loads(path.read_bytes())
            assert annotation["data_binding"]["data_ref"] == member["data"]
            assert (
                annotation["revision"]["content_hash"]
                == member["annotation"]["revision_content_hash"]
            )
            annotation_count += len(annotation["revision"]["tags"])
            selected = set(member["annotation"]["track"]["annotation_ids"])
            tags = {t["annotation_id"]: t for t in annotation["revision"]["tags"]}
            mapped = [s for s in subtasks if s["episode_index"] == index]
            assert {s["annotation_id"] for s in mapped} == selected
            for local, row in enumerate(rows[begin:end]):
                active = [
                    s
                    for s in mapped
                    if tags[s["annotation_id"]]["start_step"]
                    <= local
                    < tags[s["annotation_id"]]["end_step"]
                ]
                assert len(active) <= 1
                assert row["subtask_index"] == (active[0]["subtask_index"] if active else -1)
        elif "subtask_index" in info["features"]:
            assert all(row["subtask_index"] == -1 for row in rows[begin:end])
        assert set(member["camera_mapping"].values()) == set(member["media"])
        for feature, media in member["media"].items():
            assert sha(root / media["path"]) == media["sha256"]
            assert episode[f"videos/{feature}/chunk_index"] == index // 1000
            assert episode[f"videos/{feature}/file_index"] == index % 1000
            assert episode[f"videos/{feature}/from_timestamp"] == 0
            assert episode[f"videos/{feature}/to_timestamp"] == (end - begin) / info["fps"]
            is_depth = info["features"][feature]["info"]["is_depth_map"]
            with av.open(str(root / media["path"])) as container:
                sequence = []
                for i, frame in enumerate(container.decode(video=0)):
                    assert abs(float(frame.pts * frame.time_base) - i / info["fps"]) < 1e-6
                    sequence.append(frame.to_ndarray(format="gray12le" if is_depth else "rgb24"))
                assert len(sequence) == end - begin
                decoded[index, feature] = sequence
            if is_depth:
                evidence = root / media["evidence_path"]
                source_maps[index, feature] = pq.read_table(evidence / "frames.parquet").to_pylist()
                flags[index, feature] = pq.read_table(
                    evidence / "validity/flags.parquet"
                ).to_pylist()
                profile = document(evidence, "profile.json")
                assert profile["feature_info"]["depth_unit"] == "mm"
                codes = np.asarray(sequence, dtype=np.float64)
                assert codes.min() >= 0 and codes.max() <= 4095
                if profile["use_log"]:
                    low = np.log(profile["depth_min"] + profile["shift"])
                    high = np.log(profile["depth_max"] + profile["shift"])
                    physical = np.exp(low + codes / 4095 * (high - low)) - profile["shift"]
                else:
                    physical = profile["depth_min"] + codes / 4095 * (
                        profile["depth_max"] - profile["depth_min"]
                    )
                expected_mm[index, feature] = physical * 1000
        if manifest["modalities"] == "rgb":
            for feature in member["excluded_depth_features"]:
                assert feature not in info["features"] and feature not in member["media"]
    dataset = LeRobotDataset("local/chayi-reassembled", root=root, video_backend="pyav")
    seen = cross_batches = invalid = 0
    max_rounding = 0.0
    physical_statistics = {}
    for batch in DataLoader(dataset, batch_size=5, num_workers=0):
        if len(set(batch["episode_index"].tolist())) > 1:
            cross_batches += 1
        for local in range(len(batch["index"])):
            index = seen + local
            row, mapping = rows[index], frames[index]
            episode_index, frame_index = row["episode_index"], row["frame_index"]
            member = manifest["members"][episode_index]
            assert row["index"] == mapping["index"] == index
            assert mapping["episode_index"] == episode_index
            assert mapping["frame_index"] == mapping["parent_frame_index"] == frame_index
            assert mapping["parent_data_version_id"] == member["data"]["data_version_id"]
            assert mapping["parent_timestamp_ns"] == row["hc.timestamp_ns"]
            assert batch["task"][local] == tasks[row["task_index"]]
            for feature, description in info["features"].items():
                if feature.startswith("hc.") or description["dtype"] in {"video", "string"}:
                    continue
                actual = batch[feature][local].numpy()
                np.testing.assert_array_equal(
                    actual, np.asarray(row[feature]).astype(actual.dtype).reshape(actual.shape)
                )
            for feature in dataset.meta.video_keys:
                actual = batch[feature][local].numpy()
                key = episode_index, feature
                if key not in flags:
                    expected = decoded[key][frame_index].transpose(2, 0, 1) / 255
                    np.testing.assert_allclose(actual, expected, atol=1e-6)
                    continue
                assert actual.shape == (1, *decoded[key][frame_index].shape)
                error = float(np.abs(actual[0] - expected_mm[key][frame_index]).max())
                assert error <= 0.51
                max_rounding = max(max_rounding, error)
                mask_record, source = flags[key][frame_index], source_maps[key][frame_index]
                assert mask_record["frame_index"] == source["frame_index"] == frame_index
                assert source["target_timestamp_ns"] == row["hc.timestamp_ns"]
                with Image.open(io.BytesIO(mask_record["flags_png"])) as image:
                    valid = np.asarray(image) & 1 != 0
                physical_statistics.setdefault(feature, []).extend(actual[0][valid].tolist())
                physical_mm = actual[0].copy()
                physical_mm[~valid] = np.nan
                assert np.isnan(physical_mm[~valid]).all()
                invalid += int((~valid).sum())
        seen += len(batch["index"])
    assert cross_batches >= 1 and seen == len(dataset) == len(rows)
    stats = document(root, "meta/stats.json")
    for feature, values in physical_statistics.items():
        np.testing.assert_allclose(
            np.asarray(stats[feature]["mean"]).item(), np.mean(values), rtol=1e-7
        )
        np.testing.assert_allclose(
            np.asarray(stats[feature]["std"]).item(), np.std(values), rtol=1e-7
        )
        assert info["features"][feature]["info"]["hc.stats_valid_pixels"] == len(values)
    for feature in (
        "index",
        "episode_index",
        "task_index",
        "action",
        "observation.state",
        "subtask_index",
    ):
        if feature in info["features"]:
            values = [r[feature] for r in rows]
            np.testing.assert_allclose(stats[feature]["mean"], np.mean(values, axis=0).reshape(-1))
            np.testing.assert_allclose(stats[feature]["std"], np.std(values, axis=0).reshape(-1))
    return {
        "root": str(root),
        "manifest_sha256": sha(root / "hc/collection_manifest.json"),
        "modalities": manifest["modalities"],
        "episodes": len(episodes),
        "frames": seen,
        "tasks": list(tasks.values()),
        "cross_episode_batches": cross_batches,
        "rgb_values_and_boundaries_checked": True,
        "rebuilt_numeric_and_depth_stats_checked": True,
        "annotation_tags_preserved": annotation_count,
        "invalid_pixels_masked": invalid,
        "hc_source_frames_checked": True,
        "hc_depth_masks_applied": bool(flags),
        "official_rounding_max_error_mm": max_rounding,
        "depth_streams": len(physical_statistics),
        "transcoded": False,
        "requantized": False,
    }


def main():
    assert version("lerobot") == "0.6.1"
    results = [read(Path(value)) for value in sys.argv[2:]]
    assert not any(name.startswith("hc_data_platform") for name in sys.modules)
    report = {
        "lerobot": version("lerobot"),
        "network": "disabled by container",
        "platform_imported": False,
        "official_reader_and_cross_episode_dataloader": True,
        "collections": results,
        "synthetic_input": True,
        "real_sensor_verified": False,
        "human_acceptance": False,
        "model_depth_training_support": "NOT_VALIDATED",
    }
    Path(sys.argv[1]).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
