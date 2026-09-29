"""Offline LeRobot reader/DataLoader + independent masks over a service output.

The reference explicitly describes a synthetic source and a selected depth window.
This does not validate a real sensor recording, an upload entry, or model training.
"""

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

assert version("lerobot") == "0.6.1"
reference = json.loads(Path("/app/.g1-artifacts/depth-window-package.json").read_text())
root = Path(reference["offline_package"])
dataset = LeRobotDataset("local/chayi-depth-window", root=root, video_backend="pyav")
depth, rgb = "observation.images.head_depth", "observation.images.head_rgb"
info = json.loads((root / "meta/info.json").read_text())
modalities = json.loads((root / "hc/modalities.json").read_text())
evidence = root / modalities["depth"][depth]["evidence_path"]
frames = pq.read_table(evidence / "frames.parquet").to_pylist()
masks = pq.read_table(evidence / "validity/flags.parquet").to_pylist()
rows = pq.read_table(root / "data").to_pylist()
report = json.loads((evidence / "report.json").read_text())
assert report["window"]["requantized"] is False
assert report["video_encoding"]["lossless_codes"] is True
assert report["video_encoding"]["encoder_profile"] == "hc-hevc-gray12-lossless/2"
assert report["window"]["source_video_sha256"] == reference["depth_source_sha256"]
assert (
    hashlib.sha256((root / f"videos/{rgb}/chunk-000/file-000.mp4").read_bytes()).hexdigest()
    == reference["rgb_source_sha256"]
)
seen, missing, repeated, maximum_error = 0, 0, 0, 0.0
for batch in DataLoader(dataset, batch_size=2, num_workers=0):
    assert batch[depth].shape[1:] == (1, 16, 16)
    assert batch[rgb].shape[1:] == (3, 16, 16)
    for offset in range(len(batch[depth])):
        index = seen + offset
        row = frames[index]
        assert row["parent_frame_index"] == reference["selected_frames"][index]
        assert row["source_seq"] == reference["source_seq"][index]
        assert row["target_timestamp_ns"] == rows[index]["hc.timestamp_ns"]
        expected_time = [] if row["missing"] else [row["source_timestamp_ns"]]
        assert json.loads(rows[index][f"hc.source_timestamps_ns.{depth}"]) == expected_time
        assert json.loads(rows[index][f"hc.time_error_ns.{depth}"]) == row["time_error_ns"]
        assert rows[index][f"hc.repeated.{depth}"] == row["repeated"]
        with Image.open(io.BytesIO(masks[index]["flags_png"])) as image:
            flags = np.asarray(image)
        decoded = batch[depth][offset, 0].numpy().copy()
        valid = flags & 1 != 0
        # Official reader treats code 0 as depth_min, including absent pixels.
        assert (decoded[~valid] == 10).all()
        decoded[~valid] = np.nan
        assert np.isnan(decoded[~valid]).all()
        if row["missing"]:
            assert not valid.any() and row["depth_scale_m_per_unit"] is None
            missing += 1
        else:
            assert row["depth_scale_m_per_unit"] == reference["source_scale_m_per_unit"]
            expected_mm = (4000 + row["source_seq"]) * 0.25
            error = float(np.abs(decoded[flags == 1] - expected_mm).max())
            maximum_error = max(maximum_error, error)
            assert error <= 2
            assert (decoded[flags & 2 != 0] == 10).all()
            assert (decoded[flags & 4 != 0] == 10000).all()
        repeated += int(row["repeated"])
    seen += len(batch[depth])
assert seen == len(dataset) == 4
assert missing == repeated == 1
assert not any(name.startswith("hc_data_platform") for name in sys.modules)
result = {
    "scope": reference["scope"],
    "network": "disabled by container",
    "lerobot_version": version("lerobot"),
    "data_version_id": reference["data"]["data_version_id"],
    "frames": seen,
    "missing_frames": missing,
    "repeated_frames": repeated,
    "official_reader_and_dataloader": True,
    "independent_hc_mask_applied": True,
    "original_sensor_mapping_verified": True,
    "depth_output_unit": "mm",
    "max_interior_error_mm_including_official_rounding": maximum_error,
    "source_quantization_max_error_m": report["quantization"]["unclipped_max_error_m"],
    "selected_12bit_codes_lossless": report["video_encoding"]["lossless_codes"],
    "codec_profile": report["video_encoding"]["codec_profile"],
    "encoder_profile": report["video_encoding"]["encoder_profile"],
    "rgb_h265_byte_preserved": True,
    "model_depth_training_support": "NOT_VALIDATED",
    "platform_imported": False,
}
Path("/evidence/depth-window-offline-reader.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))
