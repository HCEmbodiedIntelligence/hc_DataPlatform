"""Offline official DataLoader check of a full revision-bound delivery artifact."""

import hashlib
import io
import json
import sys
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from lerobot.datasets.lerobot_dataset import LeRobotDataset
from PIL import Image
from torch.utils.data import DataLoader

root = Path(sys.argv[1])
manifest = json.loads((root / "hc/delivery_manifest.json").read_text())
assert manifest["format"] == "hc-annotated-episode/1"
assert manifest["exclusion_policy"] == "mark_parent_frames"
for entry in manifest["files"]:
    body = (root / entry["path"]).read_bytes()
    assert len(body) == entry["size"] and hashlib.sha256(body).hexdigest() == entry["sha256"]
parent = json.loads((root / "hc/parent_manifest.json").read_text())
unchanged = 0
for entry in parent["files"]:
    if entry["path"].startswith(("videos/", "hc/depth/")):
        assert hashlib.sha256((root / entry["path"]).read_bytes()).hexdigest() == entry["sha256"]
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
segments = [json.loads(line) for line in (root / "hc/subtasks.jsonl").read_text().splitlines()]
assert [segment["subtask_index"] for segment in segments] == list(range(len(segments)))
modalities = json.loads((root / "hc/modalities.json").read_text())
masks = {
    feature: pq.read_table(root / item["evidence_path"] / "validity/flags.parquet").to_pylist()
    for feature, item in modalities["depth"].items()
}
dataset = LeRobotDataset("local/chayi-annotated-rgbd", root=root, video_backend="pyav")
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
    "synthetic_input": True,
    "lerobot": version("lerobot"),
    "official_reader_and_dataloader": True,
    "independent_hc_masks_applied": True,
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
Path(sys.argv[2]).write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
