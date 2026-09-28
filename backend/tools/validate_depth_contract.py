"""Offline quantization/codec cross-check with pinned official LeRobot (not E2E).

Run with deploy/chayi/compose.sh run --rm --no-deps reader /app/tools/validate_depth_contract.py.
Uses deterministic numerical probes; no real-data or full-package claim.
"""

import hashlib
import importlib.metadata
import json
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

import numpy as np
from lerobot.datasets.depth_utils import dequantize_depth, quantize_depth
from lerobot.datasets.video_utils import decode_video_frames

sys.path.insert(0, "/app/src")
# Only this comparative test imports platform code; the reader-only package
# acceptance has to run separately without /app/src or platform dependencies.
sys.path.append("/opt/venv/lib/python3.12/site-packages")
from hc_data_platform.publishing.depth_evidence import (  # noqa: E402
    DepthEvidenceVideo,
    read_validity,
)
from hc_data_platform.publishing.holobrain_depth import (  # noqa: E402
    DEFAULT_PROFILE,
    DepthProfile,
    dequantize,
    quantize,
)

assert importlib.metadata.version("lerobot") == "0.6.1"
rng = np.random.default_rng(20260928)
probes = [
    (rng.integers(0, 65536, (256, 256), dtype=np.uint16), 0.00025),
    (np.linspace(0.01, 10, 65536).reshape(256, 256).astype(np.float32), 1),
]
results = []
for profile in (DEFAULT_PROFILE, DepthProfile(depth_max=5), DepthProfile(use_log=False)):
    params = {k: v for k, v in asdict(profile).items() if k != "version"}
    for source, scale in probes:
        ours = quantize(source, scale, profile)
        mm = source.astype(np.float32) * np.float32(scale * 1000)
        mm = np.where(
            ours.valid,
            np.clip(mm, profile.depth_min * 1000, profile.depth_max * 1000),
            profile.depth_min * 1000,
        )
        official = quantize_depth(mm, input_unit="mm", video_backend=None, **params)
        np.testing.assert_array_equal(ours.codes, official)
        report = ours.error_report(profile)
        for unit in ("m", "mm"):
            expected = dequantize_depth(official, output_unit=unit, output_tensor=False, **params)
            np.testing.assert_allclose(
                dequantize(ours.codes, profile, output_unit=unit),
                expected.squeeze(0),
                atol=2e-6 if unit == "m" else 0,
                rtol=0,
            )
        results.append(
            {
                "profile_sha256": profile.sha256,
                "source_dtype": source.dtype.str,
                "source_scale": scale,
                "quantization": report,
                "official_codes_identical": True,
                "official_m_mm_match": True,
            }
        )

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    writer = DepthEvidenceVideo(root / "depth.mp4", root / "depth", 30, (256, 256))
    for i, (source, scale) in enumerate(probes):
        writer.write(
            source,
            scale,
            source_id="probe",
            source_seq=i,
            source_timestamp_ns=i * 33_333_333,
            target_timestamp_ns=i * 33_333_333,
        )
    writer.close()
    decoded = decode_video_frames(
        root / "depth.mp4", [0, 1 / 30], 1e-5, backend="pyav", is_depth=True
    )
    for i, (source, scale) in enumerate(probes):
        reference = quantize(source, scale)
        np.testing.assert_array_equal(decoded[i, 0].numpy(), reference.codes)
        physical = dequantize_depth(decoded[i], output_unit="m").numpy().squeeze(0)
        mask = read_validity(root / "depth", i)["valid"]
        physical[~mask] = np.nan
        assert np.isfinite(physical[mask]).all()
        assert np.isnan(physical[~mask]).all()
    video_report = json.loads((root / "depth/report.json").read_text())

official_file = Path(sys.modules["lerobot.datasets.depth_utils"].__file__)
result = {
    "scope": "synthetic numerical/codec compatibility; not full-package or real-data acceptance",
    "versions": {p: importlib.metadata.version(p) for p in ("lerobot", "numpy", "torch", "av")},
    "official_depth_utils_sha256": hashlib.sha256(official_file.read_bytes()).hexdigest(),
    "probes": results,
    "video": video_report,
    "official_video_decode_and_independent_hc_mask": True,
}
Path("/evidence/depth-official-crosscheck.json").write_text(json.dumps(result, indent=2) + "\n")
print(
    json.dumps(
        {
            "passed": True,
            "probe_count": len(results),
            "versions": result["versions"],
            "codec_profile": video_report["video_encoding"]["codec_profile"],
        }
    )
)
