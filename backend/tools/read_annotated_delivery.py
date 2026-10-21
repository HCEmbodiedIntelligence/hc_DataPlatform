"""Offline official DataLoader check of a full revision-bound delivery artifact."""

import argparse
import hashlib
import io
import json
import sys
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import torch
from lerobot.datasets.lerobot_dataset import LeRobotDataset
from PIL import Image
from torch.utils.data import DataLoader

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("root", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument("--video-backend", choices=("pyav", "torchcodec"), default="pyav")
parser.add_argument(
    "--input-kind",
    choices=("software_fixture", "existing_history_isolated_copy"),
    default="software_fixture",
)
args = parser.parse_args()
root = args.root
assert version("lerobot") == "0.6.1"
torch.set_num_threads(1)


def file_hash(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024**2), b""):
            value.update(chunk)
    return value.hexdigest()


manifest = json.loads((root / "hc/delivery_manifest.json").read_text())
assert manifest["format"] == "hc-annotated-episode/1"
assert manifest["exclusion_policy"] == "mark_parent_frames"
for entry in manifest["files"]:
    path = root / entry["path"]
    assert path.stat().st_size == entry["size"] and file_hash(path) == entry["sha256"]
parent = json.loads((root / "hc/parent_manifest.json").read_text())
unchanged = 0
for entry in parent["files"]:
    if entry["path"].startswith(("videos/", "hc/depth/")):
        assert file_hash(root / entry["path"]) == entry["sha256"]
        unchanged += 1
assert (
    hashlib.sha256((root / "hc/annotation.json").read_bytes()).hexdigest()
    == manifest["subtasks"]["annotation_file_sha256"]
)
annotation = json.loads((root / "hc/annotation.json").read_text())
assert annotation["revision"]["content_hash"] == manifest["subtasks"]["revision_content_hash"]
assert annotation["data_binding"]["data_ref"] == manifest["base_data"]
rows = pq.read_table(root / "data").to_pylist()
source_frames = pq.read_table(root / "hc/annotation_source_frames.parquet").to_pylist()
segment_file = root / "hc/subtasks.jsonl"
segments = (
    [json.loads(line) for line in segment_file.read_text().splitlines()]
    if segment_file.exists()
    else []
)
assert [segment["subtask_index"] for segment in segments] == list(range(len(segments)))
modalities = json.loads((root / "hc/modalities.json").read_text())
masks = {
    feature: pq.read_table(root / item["evidence_path"] / "validity/flags.parquet").to_pylist()
    for feature, item in modalities["depth"].items()
}
dataset = LeRobotDataset("local/chayi-annotated-rgbd", root=root, video_backend=args.video_backend)
seen, invalid = 0, 0
for batch in DataLoader(dataset, batch_size=2, num_workers=0):
    indices = batch["subtask_index"].reshape(-1).tolist()
    excluded = batch["hc.annotation.excluded"].reshape(-1).tolist()
    for local, index in enumerate(indices):
        row = rows[seen + local]
        frame = source_frames[seen + local]
        expected = [
            segment["subtask_index"]
            for segment in segments
            if segment["source_start_step"]
            <= frame["source_frame_index"]
            < segment["source_end_step"]
        ]
        assert len(expected) <= 1 and index == (expected[0] if expected else -1)
        assert index == row["subtask_index"] and excluded[local] == row["hc.annotation.excluded"]
        assert frame["source_timestamp_ns"] == row["hc.timestamp_ns"]
        for feature, mask_rows in masks.items():
            with Image.open(io.BytesIO(mask_rows[seen + local]["flags_png"])) as image:
                valid = np.asarray(image) & 1 != 0
            physical_mm = batch[feature][local, 0].numpy().copy()
            physical_mm[~valid] = np.nan
            assert np.isnan(physical_mm[~valid]).all()
            invalid += int((~valid).sum())
    seen += len(indices)
assert seen == len(dataset) == manifest["base_data"]["frame_count"]
assert not any(name.startswith("hc_data_platform") for name in sys.modules)
result = {
    "source": str(root),
    "input_kind": args.input_kind,
    "synthetic_input": args.input_kind == "software_fixture",
    "sensor_provenance_independently_verified": False,
    "human_acceptance": False,
    "lerobot": version("lerobot"),
    "official_reader_and_dataloader": True,
    "independent_hc_masks_applied": bool(masks),
    "video_backend_requested": args.video_backend,
    "depth_video_backend": "pyav" if masks else None,
    "frames": seen,
    "segments": len(segments),
    "all_tags": len(annotation["revision"]["tags"]),
    "invalid_pixels_masked": invalid,
    "media_and_depth_evidence_byte_reused": unchanged,
    "depth_streams": len(masks),
    "base_data_version": manifest["base_data"]["data_version_id"],
    "revision": manifest["subtasks"]["revision"],
    "review": manifest["review"],
    "network": "disabled by reader container",
    "platform_imported": False,
    "queue_api_browser_verified": False,
    "model_depth_training_support": "NOT_VALIDATED",
}
args.output.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
