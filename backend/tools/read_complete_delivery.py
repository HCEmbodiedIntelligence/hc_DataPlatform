"""Read actual complete-download ZIPs offline with official LeRobot and independent HC masks.

Only use trusted writer-produced artifacts. This validates software fixtures, not
sensor calibration, human acceptance or an unspecified model's depth training.
"""

import hashlib
import io
import json
import sys
import tempfile
import zipfile
from importlib.metadata import version
from pathlib import Path, PurePosixPath

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


def read_package(root, member):
    manifest = json.loads((root / "hc/manifest.json").read_text())
    assert sha(root / "hc/manifest.json") == member["manifest_sha256"]
    assert json.loads((root / "hc/commit.json").read_text())["data"] == member["data"]
    for entry in manifest["files"]:
        path = root / entry["path"]
        assert path.stat().st_size == entry["size"] and sha(path) == entry["sha256"]
    rows = pq.read_table(root / "data").to_pylist()
    modalities = json.loads((root / "hc/modalities.json").read_text())
    info = json.loads((root / "meta/info.json").read_text())
    dataset = LeRobotDataset("local/chayi-complete-delivery", root=root, video_backend="pyav")
    masks, mappings, expected_depth = {}, {}, {}
    for feature, declaration in modalities["depth"].items():
        evidence = root / declaration["evidence_path"]
        masks[feature] = pq.read_table(evidence / "validity/flags.parquet").to_pylist()
        mappings[feature] = pq.read_table(evidence / "frames.parquet").to_pylist()
        profile = json.loads((evidence / "profile.json").read_text())
        decoded = []
        with av.open(str(root / f"videos/{feature}/chunk-000/file-000.mp4")) as container:
            for frame in container.decode(video=0):
                codes = frame.to_ndarray(format="gray12le").astype(np.float64)
                assert codes.min() >= 0 and codes.max() <= 4095
                if profile["use_log"]:
                    low = np.log(profile["depth_min"] + profile["shift"])
                    high = np.log(profile["depth_max"] + profile["shift"])
                    physical_m = np.exp(low + codes / 4095 * (high - low)) - profile["shift"]
                else:
                    physical_m = profile["depth_min"] + codes / 4095 * (
                        profile["depth_max"] - profile["depth_min"]
                    )
                decoded.append(physical_m * 1000)
        expected_depth[feature] = decoded
    seen = invalid = 0
    max_rounding_error = 0.0
    numeric_dtypes = {}
    for batch in DataLoader(dataset, batch_size=2, num_workers=0):
        n = len(batch["index"])
        for offset in range(n):
            index = seen + offset
            row = rows[index]
            for feature in ("index", "frame_index", "episode_index", "task_index"):
                assert batch[feature][offset].item() == row[feature]
            for feature, description in info["features"].items():
                if feature.startswith("hc.") or description["dtype"] in {"video", "string"}:
                    continue
                actual = batch[feature][offset].numpy()
                expected = np.asarray(row[feature]).astype(actual.dtype).reshape(actual.shape)
                np.testing.assert_array_equal(actual, expected)
                numeric_dtypes[feature] = str(actual.dtype)
            for feature in dataset.meta.video_keys:
                h, w, channels = info["features"][feature]["shape"]
                assert tuple(batch[feature][offset].shape) == (channels, h, w)
            for feature, mask_rows in masks.items():
                frame = mappings[feature][index]
                assert frame["target_timestamp_ns"] == row["hc.timestamp_ns"]
                with Image.open(io.BytesIO(mask_rows[index]["flags_png"])) as image:
                    flags = np.asarray(image)
                valid = flags & 1 != 0
                physical_mm = batch[feature][offset, 0].numpy().copy()
                assert np.isfinite(physical_mm).all()
                # Official reader rounds to whole millimetres; HC masking remains separate.
                error = float(np.abs(physical_mm - expected_depth[feature][index]).max())
                assert error <= 0.51, error
                max_rounding_error = max(max_rounding_error, error)
                physical_mm[~valid] = np.nan
                assert np.isnan(physical_mm[~valid]).all()
                if frame["missing"]:
                    assert not valid.any()
                invalid += int((~valid).sum())
        seen += n
    assert seen == len(dataset) == member["data"]["frame_count"]
    return {
        "data_version_id": member["data"]["data_version_id"],
        "frames": seen,
        "depth_streams": len(masks),
        "invalid_pixels_masked": invalid,
        "depth_unit": "mm",
        "official_rounding_max_error_mm": max_rounding_error,
        "numeric_reader_dtypes": numeric_dtypes,
        "independent_hc_masks_applied": True,
        "source_mapping_checked": True,
        "all_manifest_hashes_verified": True,
    }


def main():
    archive_path, output = Path(sys.argv[1]), Path(sys.argv[2])
    assert version("lerobot") == "0.6.1"
    with tempfile.TemporaryDirectory(prefix="chayi-reader-delivery-") as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(archive_path) as archive:
            for name in archive.namelist():
                path = PurePosixPath(name)
                assert not path.is_absolute() and ".." not in path.parts and "\\" not in name
            assert len(set(archive.namelist())) == len(archive.namelist())
            archive.extractall(root)
        inventory = json.loads((root / "delivery.json").read_text())
        assert inventory["format"] == "hc-episode-delivery/1"
        assert inventory["is_reassembled_collection"] is False
        members = [
            read_package(root / member["root"], member) for member in inventory["plan"]["members"]
        ]
    assert not any(name.startswith("hc_data_platform") for name in sys.modules)
    result = {
        "input_archive": str(archive_path),
        "archive_sha256": sha(archive_path),
        "archive_format": inventory["plan"]["archive_format"],
        "members": members,
        "official_reader_and_dataloader": True,
        "lerobot": version("lerobot"),
        "network": "disabled by container",
        "platform_imported": False,
        "synthetic_input": True,
        "human_acceptance": False,
        "reassembled_collection_verified": False,
        "model_depth_training_support": "NOT_VALIDATED",
    }
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
