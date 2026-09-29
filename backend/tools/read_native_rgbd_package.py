"""Offline official reader over the actual native-upload integration output.

No platform imports or network. The input recording is explicitly synthetic;
this verifies the input contract, not sensor accuracy or model training support.
"""

import argparse
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
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--reference", default="/app/.g1-artifacts/native-package-roundtrip.json", type=Path
)
parser.add_argument("--output", default="/evidence/native-package-offline-reader.json", type=Path)
args = parser.parse_args()
reference = json.loads(args.reference.read_text())
root = Path(reference["offline_package"])
dataset = LeRobotDataset("local/chayi-native-package-contract", root=root, video_backend="pyav")
key = "observation.images.head_depth"
metadata = json.loads((root / "hc/modalities.json").read_text())
evidence = root / metadata["depth"][key]["evidence_path"]
frames = pq.read_table(evidence / "frames.parquet").to_pylist()
masks = pq.read_table(evidence / "validity/flags.parquet").to_pylist()
source_rows = pq.read_table(root / "data").to_pylist()
seen, errors, numeric_errors, numeric_dtypes = 0, [], {}, {}
for batch in DataLoader(dataset, batch_size=4, num_workers=0):
    assert batch[key].shape[1:] == (1, 16, 16)
    assert batch[reference.get("rgb_key", "observation.images.head_rgb")].shape[1:] == (3, 16, 16)
    for offset in range(len(batch[key])):
        index = seen + offset
        frame = frames[index]
        assert frame["frame_index"] == frame["source_seq"] == index
        assert (
            frame["source_timestamp_ns"]
            == frame["target_timestamp_ns"]
            == round(index * 1e9 / reference.get("fps", 25))
        )
        assert frame["depth_scale_m_per_unit"] == 0.00025
        with Image.open(io.BytesIO(masks[index]["flags_png"])) as image:
            valid = np.asarray(image) & 1 != 0
        measured = batch[key][offset, 0].numpy().copy()
        assert measured[0, 0] == 10  # Official decoding alone does not apply HC validity.
        measured[~valid] = np.nan
        assert np.isnan(measured[0, 0]) and np.isfinite(measured[valid]).all()
        error = float(np.max(np.abs(measured[valid] - (3000 + index * 10) * 0.25)))
        assert error <= 2  # Includes official whole-mm output rounding.
        errors.append(error)
        for feature in ("observation.state", "action", "observation.extra.matrix"):
            actual = batch[feature][offset].numpy()
            expected = np.asarray(source_rows[index][feature], dtype=np.float64)
            # The pinned official HF transform constructs default float32 tensors.
            # The stored Parquet remains source-precision; report this reader cast.
            np.testing.assert_array_equal(actual, expected.astype(actual.dtype))
            numeric_dtypes[feature] = str(actual.dtype)
            numeric_errors[feature] = max(
                numeric_errors.get(feature, 0.0), float(np.max(np.abs(actual - expected)))
            )
    seen += len(batch[key])
assert seen == len(dataset) == 6
result = {
    "scope": "synthetic native upload, real services, complete downloaded package",
    "lerobot_version": version("lerobot"),
    "network": "disabled by container",
    "data_version_id": reference["data"]["data_version_id"],
    "frames": seen,
    "official_reader_and_dataloader": True,
    "independent_hc_mask_applied": True,
    "depth_output_unit": "mm",
    "source_depth_scale_m_per_unit": 0.00025,
    "source_mapping_checked": True,
    "parquet_numeric_values_exact": True,
    "official_numeric_tensor_dtypes": numeric_dtypes,
    "official_numeric_cast_max_abs_error": numeric_errors,
    "max_error_mm_by_frame": errors,
    "model_depth_training_support": "NOT_VALIDATED",
    "platform_imported": any(name.startswith("hc_data_platform") for name in sys.modules),
}
args.output.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))
