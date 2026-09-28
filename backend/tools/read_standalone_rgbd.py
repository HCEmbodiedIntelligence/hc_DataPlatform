"""Official offline reader acceptance. No platform package imports or services.

The supplied fixture is synthetic and proves the software input contract only.
An independently read HC mask is intentionally separate from official decoding.
"""

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
assert not any(name.startswith("hc_data_platform") for name in sys.modules)
root = Path(
    sys.argv[1]
    if len(sys.argv) > 1
    else Path("/app/.g1-artifacts/standalone-rgbd-latest.txt").read_text().strip()
)
key = "observation.images.head_depth"
dataset = LeRobotDataset("local/chayi-rgbd-input-contract", root=root, video_backend="pyav")
batch = next(iter(DataLoader(dataset, batch_size=4, num_workers=0)))
assert tuple(batch[key].shape) == (4, 1, 16, 16)
assert tuple(batch["observation.images.head_rgb"].shape) == (4, 3, 16, 16)
modality = json.loads((root / "hc/modalities.json").read_bytes())["depth"][key]
evidence = root / modality["evidence_path"]
frames = pq.read_table(evidence / "frames.parquet").to_pylist()
masks = pq.read_table(evidence / "validity/flags.parquet").to_pylist()
errors = []
for index, row in enumerate(masks):
    with Image.open(io.BytesIO(row["flags_png"])) as image:
        valid = np.asarray(image) & 1 != 0
    physical_mm = batch[key][index, 0].numpy().copy()
    assert physical_mm[0, 0] == 10  # Official decoder doesn't know the HC invalid mask.
    physical_mm[~valid] = np.nan
    assert np.isnan(physical_mm[0, 0])
    error = float(np.max(np.abs(physical_mm[valid] - (500 + index * 500))))
    assert error <= 3.0  # Further bounded by the per-pixel half-cell encoder validation.
    assert frames[index]["source_seq"] == index
    errors.append(error)
result = {
    "scope": "synthetic standalone RGB-D format acceptance, not real data or model training",
    "lerobot_version": version("lerobot"),
    "frames": len(dataset),
    "episodes": dataset.num_episodes,
    "depth_shape": list(batch[key].shape),
    "depth_unit": "mm",
    "rgb_shape": list(batch["observation.images.head_rgb"].shape),
    "independent_hc_mask_applied": True,
    "source_mapping_checked": True,
    "platform_imported": any(name.startswith("hc_data_platform") for name in sys.modules),
    "network": "disabled by container",
    "max_error_mm_by_frame": errors,
}
Path("/evidence/standalone-rgbd-reader.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))
