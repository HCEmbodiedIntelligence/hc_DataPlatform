"""Offline official reader verification of an actual continuous-service output.

The source is explicitly synthetic; this measures software contracts, not a
physical camera, calibration accuracy or model depth-training support.
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

assert version("lerobot") == "0.6.1"
directory = Path(sys.argv[1])
reference = json.loads((directory / "services.json").read_text())
root = directory / "episode"
manifest = json.loads((directory / "manifest.json").read_text())
for entry in manifest["files"]:
    content = (root / entry["path"]).read_bytes()
    assert len(content) == entry["size"]
    assert hashlib.sha256(content).hexdigest() == entry["sha256"]

dataset = LeRobotDataset("local/chayi-continuous-rgbd", root=root, video_backend="pyav")
info = json.loads((root / "meta/info.json").read_text())
modalities = json.loads((root / "hc/modalities.json").read_text())
rows = pq.read_table(root / "data").to_pylist()
depths = {}
for feature, descriptor in modalities["depth"].items():
    evidence = root / descriptor["evidence_path"]
    report = json.loads((evidence / "report.json").read_text())
    profile = json.loads((evidence / "profile.json").read_text())
    source = reference["plan"]["videos"][feature]
    assert report["window"]["source_video_sha256"] == source["source"]["object"]["sha256"]
    assert report["window"]["requantized"] is False
    assert report["video_encoding"]["lossless_codes"] is True
    assert profile["parameter_unit"] == "m" and profile["qmax"] == 4095
    video = root / f"videos/{feature}/chunk-000/file-000.mp4"
    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        assert stream.codec_context.format.name == "gray12le"
        assert stream.codec_context.profile == report["video_encoding"]["codec_profile"]
        codes = [frame.to_ndarray(format="gray12le") for frame in container.decode(stream)]
    depths[feature] = dict(
        frames=pq.read_table(evidence / "frames.parquet").to_pylist(),
        masks=pq.read_table(evidence / "validity/flags.parquet").to_pylist(),
        profile=profile,
        report=report,
        codes=codes,
        max_reader_rounding_mm=0.0,
        invalid_pixels=0,
        missing_frames=0,
    )

assert len(depths) == 3
seen = 0
for batch in DataLoader(dataset, batch_size=2, num_workers=0):
    size = len(batch["frame_index"])
    for feature, details in info["features"].items():
        if details["dtype"] == "video":
            height, width, channels = details["shape"]
            assert batch[feature].shape == (size, channels, height, width)
    for feature, evidence in depths.items():
        profile = evidence["profile"]
        for local in range(size):
            index = seen + local
            frame, mask = evidence["frames"][index], evidence["masks"][index]
            assert frame["parent_frame_index"] == index + 1
            assert frame["target_timestamp_ns"] == rows[index]["hc.timestamp_ns"]
            assert json.loads(rows[index][f"hc.source_timestamps_ns.{feature}"]) == (
                [] if frame["missing"] else [frame["source_timestamp_ns"]]
            )
            with Image.open(io.BytesIO(mask["flags_png"])) as image:
                flags = np.asarray(image)
            valid = flags & 1 != 0
            # This independent decoder uses recorded parameters and no platform
            # imports. Official reader output is rounded millimetres, not metres.
            q = evidence["codes"][index].astype(np.float64) / profile["qmax"]
            low, high, shift = profile["depth_min"], profile["depth_max"], profile["shift"]
            metres = (
                np.exp(np.log(low + shift) + q * np.log((high + shift) / (low + shift))) - shift
                if profile["use_log"]
                else low + q * (high - low)
            )
            mm = batch[feature][local, 0].numpy().copy()
            error = float(np.max(np.abs(mm - np.clip(metres, low, high) * 1000)))
            assert error <= 0.502, (feature, index, error)
            evidence["max_reader_rounding_mm"] = max(evidence["max_reader_rounding_mm"], error)
            evidence["invalid_pixels"] += int((~valid).sum())
            evidence["missing_frames"] += int(frame["missing"])
            mm[~valid] = np.nan
            assert np.isnan(mm[~valid]).all()
            if not frame["missing"]:
                assert frame["depth_scale_m_per_unit"] == 0.001
    seen += size
assert seen == len(dataset) == reference["plan"]["frame_count"] == 6
assert not any(name.startswith("hc_data_platform") for name in sys.modules)
result = {
    "source": str(directory),
    "synthetic_input": True,
    "temporal_service": (directory / "temporal-history.json").exists(),
    "data_version_id": reference["ready"]["data_ref"]["data_version_id"],
    "frames": seen,
    "rgb_streams": 3,
    "depth_streams": 3,
    "network": "disabled by reader container",
    "lerobot_version": version("lerobot"),
    "official_reader_and_dataloader": True,
    "independent_hc_masks_applied": True,
    "physical_depth_unit": "mm",
    "platform_imported": False,
    "depths": {
        feature: {
            key: evidence[key]
            for key in ("max_reader_rounding_mm", "invalid_pixels", "missing_frames")
        }
        | {
            "codec_profile": evidence["report"]["video_encoding"]["codec_profile"],
            "quantization_max_error_m": evidence["report"]["quantization"]["unclipped_max_error_m"],
            "encoding_lossless_codes": True,
            "requantized": False,
        }
        for feature, evidence in depths.items()
    },
    "model_depth_training_support": "NOT_VALIDATED",
}
Path("/evidence/continuous-package-offline-reader.json").write_text(
    json.dumps(result, indent=2) + "\n"
)
print(json.dumps(result))
