"""Compare actual HEVC with PyAV and the separately installed FFmpeg decoder."""

import hashlib
import json
import subprocess

import av
import numpy as np
import pytest

from hc_data_platform.publishing.holobrain_depth import DepthVideo


@pytest.mark.parametrize(
    "shape", [(16, 16), (16, 128), (128, 16), (20, 24), (32, 32), (64, 64), (400, 640)]
)
def test_missing_and_full_range_codes_survive_both_decoders(tmp_path, shape):
    rng = np.random.default_rng(28971)
    for attempt in range(6):
        path = tmp_path / f"{attempt}.mp4"
        writer = DepthVideo(path, 30, shape)
        expected = []
        for index in range(12):
            # A full zero frame in the middle reproduced intermittent corruption
            # with the old tiny-frame inter encoder. Keep abrupt valid transitions
            # and a high-entropy full 12-bit frame in the same regression sequence.
            pixels = np.full(shape, 755 + index + attempt, dtype=np.uint16)
            pixels[0, :4] = [0, 0, 0, 4095]
            if index in {2, 7}:
                pixels[:] = 0
            elif index == 5:
                pixels[:] = 4095
            elif index == 9:
                pixels[:] = rng.integers(0, 4096, shape, dtype=np.uint16)
            expected.append(pixels)
            writer.write_codes(pixels)
        writer.close()
        reference = np.stack(expected)
        with av.open(str(path)) as source:
            stream = source.streams.video[0]
            assert (stream.codec_context.name, stream.codec_context.format.name) == (
                "hevc",
                "gray12le",
            )
            actual = [frame.to_ndarray(format="gray12le") for frame in source.decode(stream)]
        np.testing.assert_array_equal(actual, reference)
        result = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
                "-f",
                "rawvideo",
                "-pix_fmt",
                "gray12le",
                "-",
            ],
            capture_output=True,
            check=True,
            timeout=30,
        )
        np.testing.assert_array_equal(
            np.frombuffer(result.stdout, dtype="<u2").reshape(reference.shape), reference
        )
        metadata = writer.encoding_metadata()
        sha = metadata.pop("encoder_configuration_sha256")
        assert (
            sha
            == hashlib.sha256(
                json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )
        assert "keyint=1" in metadata["encoder_options"]["x265-params"]
