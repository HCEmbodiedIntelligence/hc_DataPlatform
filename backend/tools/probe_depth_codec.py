"""Offline synthetic regression probe for the pinned gray12le codec build.

Writes only temporary videos and the requested JSON report. Compares PyAV with
system FFmpeg. The previous option set is diagnostic only; it is never published.
This measures this probe's file sizes, not real recording compression ratios.
"""

import argparse
import json
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import av
import numpy as np

from hc_data_platform.data_formats.holobrain_depth import DepthVideo


def probe(root, shape, previous):
    path = root / "probe.mp4"
    writer = DepthVideo(path, 30, shape)
    if previous:
        writer.encoder_options["x265-params"] = writer.encoder_options["x265-params"].replace(
            ":keyint=1", ""
        )
        writer.stream.options = dict(writer.encoder_options)
    expected = []
    started = time.monotonic()
    for index in range(12):
        pixels = np.full(shape, 755 + index, dtype=np.uint16)
        pixels[0, :4] = [0, 0, 0, 4095]
        if index in {2, 7}:
            pixels[:] = 0
        elif index == 5:
            pixels[:] = 4095
        expected.append(pixels)
        writer.write_codes(pixels)
    writer.close()
    elapsed = time.monotonic() - started
    reference = np.stack(expected)
    with av.open(str(path)) as source:
        stream = source.streams.video[0]
        profile = stream.codec_context.profile
        actual = np.stack([frame.to_ndarray(format="gray12le") for frame in source.decode(stream)])
    process = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "gray12le", "-"],
        capture_output=True,
        check=True,
        timeout=30,
    )
    separate = np.frombuffer(process.stdout, dtype="<u2").reshape(reference.shape)
    return {
        "shape": shape,
        "options": writer.encoder_options,
        "configuration": "previous-inter" if previous else "hc-hevc-gray12-lossless/2",
        "codec_profile": profile,
        "pyav_differing_codes": int(np.count_nonzero(actual != reference)),
        "system_ffmpeg_differing_codes": int(np.count_nonzero(separate != reference)),
        "encoded_bytes": path.stat().st_size,
        "encode_seconds": elapsed,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repeats", type=int, default=6, choices=range(1, 51))
    args = parser.parse_args()
    results = []
    with TemporaryDirectory(prefix="hc-depth-codec-") as temporary:
        for shape in ((16, 16), (32, 32), (64, 64), (400, 640)):
            for previous in (True, False):
                for _ in range(args.repeats):
                    results.append(probe(Path(temporary), shape, previous))
    report = {
        "scope": "synthetic codec regression, not real sample/full G1 acceptance",
        "pyav_version": av.__version__,
        "ffmpeg_libraries": av.library_versions,
        "separate_decoder": subprocess.check_output(["ffmpeg", "-version"], text=True).splitlines()[
            0
        ],
        "results": results,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    for configuration in ("previous-inter", "hc-hevc-gray12-lossless/2"):
        values = [row for row in results if row["configuration"] == configuration]
        failures = sum(
            row["pyav_differing_codes"] != 0 or row["system_ffmpeg_differing_codes"] != 0
            for row in values
        )
        print(
            json.dumps({"configuration": configuration, "runs": len(values), "failures": failures})
        )
        if configuration != "previous-inter" and failures:
            raise SystemExit("current codec configuration failed the lossless check")


if __name__ == "__main__":
    main()
