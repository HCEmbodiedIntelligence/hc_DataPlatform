"""Offline official reader + independent HC validity/physical depth verification.

Run in the network-disabled reader image. Synthetic source data tests software
contracts; this is not camera calibration or model training acceptance.
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

directory = Path(sys.argv[1])
reference = json.loads((directory / "services.json").read_text())
manifest = json.loads((directory / "manifest.json").read_text())
root = directory / "episode"
for entry in manifest["files"]:
    body = (root / entry["path"]).read_bytes()
    assert len(body) == entry["size"]
    assert hashlib.sha256(body).hexdigest() == entry["sha256"]

assert version("lerobot") == "0.6.1"
dataset = LeRobotDataset("local/chayi-mcap-rgbd", root=root, video_backend="pyav")
info = json.loads((root / "meta/info.json").read_text())
modalities = json.loads((root / "hc/modalities.json").read_text())
rows = pq.read_table(root / "data").to_pylist()
depths = {}
for feature, descriptor in modalities["depth"].items():
    evidence = root / descriptor["evidence_path"]
    profile = json.loads((evidence / "profile.json").read_text())
    report = json.loads((evidence / "report.json").read_text())
    assert report["video_encoding"]["lossless_codes"] is True
    assert report["source_contract_required"] is True
    assert profile["parameter_unit"] == "m" and profile["qmax"] == 4095
    with av.open(str(root / f"videos/{feature}/chunk-000/file-000.mp4")) as container:
        stream = container.streams.video[0]
        assert stream.codec_context.format.name == "gray12le"
        assert stream.codec_context.profile == report["video_encoding"]["codec_profile"]
        codes = [frame.to_ndarray(format="gray12le") for frame in container.decode(stream)]
    digest = hashlib.sha256()
    for frame in codes:
        digest.update(frame.astype("<u2").tobytes())
    assert digest.hexdigest() == report["video_encoding"]["codes_sha256"]
    depths[feature] = {
        "profile": profile,
        "report": report,
        "codes": codes,
        "frames": pq.read_table(evidence / "frames.parquet").to_pylist(),
        "masks": pq.read_table(evidence / "validity/flags.parquet").to_pylist(),
        "max_reader_rounding_mm": 0.0,
        "invalid_pixels": 0,
        "max_unclipped_source_error_m": 0.0,
    }
assert len(depths) == 2
seen = 0
for batch in DataLoader(dataset, batch_size=4, num_workers=0):
    size = len(batch["frame_index"])
    for feature, description in info["features"].items():
        if description["dtype"] == "video":
            height, width, channels = description["shape"]
            assert tuple(batch[feature].shape) == (size, channels, height, width)
    for feature, evidence in depths.items():
        p = evidence["profile"]
        for local in range(size):
            index = seen + local
            frame = evidence["frames"][index]
            ref = json.loads(rows[index]["hc.source_ref." + feature])
            source = ref["source"]
            assert source["sequence"] == 0 and source["source_frame_index"] == index
            assert frame["source_seq"] == index and frame["repeated"] is False
            assert frame["missing"] is False and frame["depth_scale_m_per_unit"] == 0.00025
            assert source["log_time_ns"] == 1_700_000_000_000_000_000 + index * 1_000_000_000 // 30
            assert source["sensor_timestamp_ns"] == 5_000_000_000 + index
            assert source["publish_time_ns"] == source["log_time_ns"] - 7
            assert frame["target_timestamp_ns"] == rows[index]["hc.timestamp_ns"]
            assert frame["source_timestamp_ns"] == source["log_time_ns"]
            assert frame["source_profile_sha256"] == ref["source_profile_sha256"]
            with Image.open(io.BytesIO(evidence["masks"][index]["flags_png"])) as image:
                flags = np.asarray(image)
            valid = flags & 1 != 0
            assert not valid[0, 0] and not valid[0, 1]
            assert flags[0, 2] & 2 and flags[0, 3] & 4
            is_float = source["source_encoding"] == "32FC1"
            assert int((~valid).sum()) == (3 if is_float else 2)
            if is_float:
                assert not valid[0, 4]
            q = evidence["codes"][index].astype(np.float64) / p["qmax"]
            lo, hi, shift = p["depth_min"], p["depth_max"], p["shift"]
            metres = (
                np.exp(np.log(lo + shift) + q * np.log((hi + shift) / (lo + shift))) - shift
                if p["use_log"]
                else lo + q * (hi - lo)
            )
            mm = batch[feature][local, 0].numpy().copy()
            rounding = float(np.max(np.abs(mm - np.clip(metres, lo, hi) * 1000)))
            assert rounding <= 0.502
            expected = (4000 + index) * 0.00025
            error = float(np.max(np.abs(metres[1:] - expected)))
            assert error <= frame["unclipped_max_error_m"] + 2e-5
            mm[~valid] = np.nan
            assert np.isnan(mm[~valid]).all()
            evidence["max_reader_rounding_mm"] = max(rounding, evidence["max_reader_rounding_mm"])
            evidence["max_unclipped_source_error_m"] = max(
                error, evidence["max_unclipped_source_error_m"]
            )
            evidence["invalid_pixels"] += int((~valid).sum())
    seen += size
assert seen == len(dataset) == 30
assert not any(name.startswith("hc_data_platform") for name in sys.modules)
result = {
    "source": str(directory),
    "synthetic_input": True,
    "frames": seen,
    "rgb_streams": 1,
    "depth_streams": 2,
    "verified_package_files": len(manifest["files"]),
    "data_version_id": reference["ready"]["data"]["data_version_id"],
    "official_reader_and_dataloader": True,
    "lerobot_version": version("lerobot"),
    "network": "disabled by reader container",
    "platform_imported": False,
    "independent_hc_masks_applied": True,
    "physical_depth_unit": "mm",
    "depths": {
        key: {
            name: value[name]
            for name in ("max_reader_rounding_mm", "invalid_pixels", "max_unclipped_source_error_m")
        }
        | {
            "codec_profile": value["report"]["video_encoding"]["codec_profile"],
            "source_units": list(value["report"]["source_profiles"].values()),
            "encoding_lossless_codes": True,
        }
        for key, value in depths.items()
    },
    "model_depth_training_support": "NOT_VALIDATED",
    "real_sensor_and_calibration_acceptance": False,
}
Path(sys.argv[2]).write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
