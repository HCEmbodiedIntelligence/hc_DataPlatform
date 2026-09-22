"""Run with the dedicated Python 3.12 / lerobot[dataset]==0.6.1 environment.

No platform adapter is imported. Every sample and an episode-crossing batch are
read through the actual LeRobotDataset; publication images are compared to source.
"""

import argparse
import importlib.metadata
import json
import platform
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import torch
from lerobot.datasets.lerobot_dataset import LeRobotDataset
from torch.utils.data import DataLoader


def verify(root: Path, source: Path) -> dict:
    assert platform.python_version_tuple()[:2] == ("3", "12")
    assert importlib.metadata.version("lerobot") == "0.6.1"
    info = json.loads((root / "meta/info.json").read_text())
    source_info = json.loads((source / "meta/info.json").read_text())
    rows = pq.read_table(root / "data").to_pylist()
    original = pq.read_table(source / "data").to_pylist()
    dataset = LeRobotDataset("local/openarm-b", root=root.resolve(), video_backend="pyav")
    reference = (
        dataset
        if root.resolve() == source.resolve()
        else LeRobotDataset("local/openarm-source", root=source.resolve(), video_backend="pyav")
    )
    assert len(dataset) == len(rows) == len(original)
    assert set(dataset.meta.video_keys) == set(reference.meta.video_keys)
    max_video_error = 0.0
    for i, (row, expected) in enumerate(zip(rows, original, strict=True)):
        item = dataset[i]
        ref = reference[i]
        for key in ("action", "observation.state"):
            assert item[key].dtype == torch.float32
            assert list(item[key].shape) == info["features"][key]["shape"]
            assert info["features"][key]["names"] == source_info["features"][key]["names"]
            np.testing.assert_array_equal(item[key].numpy(), np.asarray(expected[key], np.float32))
        for key in ("episode_index", "frame_index", "index", "task_index"):
            assert item[key].item() == row[key] == expected[key]
        np.testing.assert_allclose(item["timestamp"].item(), expected["timestamp"], atol=1e-7)
        for key in dataset.meta.video_keys:
            height, width, channels = info["features"][key]["shape"]
            assert item[key].shape == (channels, height, width)
            assert torch.isfinite(item[key]).all()
            error = float((item[key] - ref[key]).abs().mean())
            max_video_error = max(max_video_error, error)
            assert error < 8 / 255, (key, i, error)
    batch = next(iter(DataLoader(dataset, batch_size=min(8, len(dataset)), num_workers=0)))
    assert batch["action"].shape == (len(batch["index"]), info["features"]["action"]["shape"][0])
    assert batch["episode_index"].tolist() == [r["episode_index"] for r in rows[:8]]
    assert batch["frame_index"].tolist() == [r["frame_index"] for r in rows[:8]]
    return {
        "root": str(root),
        "source": str(source),
        "status": "PASS",
        "python": platform.python_version(),
        "lerobot": importlib.metadata.version("lerobot"),
        "torch": torch.__version__,
        "frames": len(dataset),
        "episodes": info["total_episodes"],
        "video_keys": list(dataset.meta.video_keys),
        "max_video_mean_absolute_error": max_video_error,
        "batch_shapes": {
            key: list(value.shape) for key, value in batch.items() if torch.is_tensor(value)
        },
        "batch_episode_indexes": batch["episode_index"].tolist(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = verify(args.root, args.source)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
