from dataclasses import dataclass
from io import BytesIO
from urllib.parse import unquote, urlparse

import av
import numpy as np
import pytest
from PIL import Image

from hc_data_platform.aligned_media.encoder import AlignedMediaEncodingError, FFmpegMp4Encoder
from hc_data_platform.publishing.holobrain_depth import DEPTH_INFO, codes
from tests.aligned_media.test_aligned_media_service import generation_request


@dataclass
class Frame:
    step_index: int
    timestamp_ns: int
    image: bytes | None
    valid: bool = True


def png(array):
    output = BytesIO()
    Image.fromarray(array).save(output, "PNG")
    return output.getvalue()


def test_decoded_h265_png_can_generate_rgb_preview(tmp_path):
    request = generation_request()
    request = request.model_copy(
        update={"alignment": request.alignment.model_copy(update={"row_count": 3})}
    )
    encoded = FFmpegMp4Encoder(tmp_path).encode(
        artifact_key="a" * 64,
        request=request,
        frames=[
            Frame(i, i * 33333333, png(np.full((24, 32, 3), 60 + i * 40, np.uint8)))
            for i in range(3)
        ],
    )
    with av.open(unquote(urlparse(encoded.file_uri).path)) as video:
        frames = list(video.decode(video=0))
    assert len(frames) == 3
    for i, frame in enumerate(frames):
        assert frame.to_ndarray(format="rgb24").mean() == pytest.approx(60 + i * 40, abs=4)


def test_metric_depth_media_preserves_quantized_codes_and_metadata(tmp_path):
    request = generation_request()
    request = request.model_copy(
        update={
            "profile_id": "canonical-depth-mm-hevc-v1",
            "alignment": request.alignment.model_copy(update={"row_count": 3}),
        }
    )
    pixels = np.arange(24 * 32, dtype=np.uint16).reshape(24, 32) * 10
    encoded = FFmpegMp4Encoder(tmp_path).encode(
        artifact_key="b" * 64,
        request=request,
        frames=[
            Frame(0, 1000, None, False),
            Frame(1, 33334333, png(pixels)),
            Frame(2, 66667666, png(pixels)),
        ],
    )
    assert encoded.video_info == DEPTH_INFO
    assert encoded.placeholder_count == 1
    assert encoded.first_timestamp_ns == 1000
    assert (encoded.width, encoded.height) == (32, 24)
    with av.open(unquote(urlparse(encoded.file_uri).path)) as video:
        frames = list(video.decode(video=0))
    assert len(frames) == 3
    for i, frame in enumerate(frames):
        assert frame.format.name == "gray12le"
        plane = frame.planes[0]
        actual = np.frombuffer(plane, dtype="<u2").reshape(24, plane.line_size // 2)[:, :32]
        expected = codes(pixels if i else np.zeros_like(pixels), 0.001)
        np.testing.assert_array_equal(actual, expected)


def test_depth_does_not_silently_become_rgb_without_unit_metadata(tmp_path):
    request = generation_request()
    request = request.model_copy(
        update={"alignment": request.alignment.model_copy(update={"row_count": 1})}
    )
    with pytest.raises(AlignedMediaEncodingError, match="depth-unit"):
        FFmpegMp4Encoder(tmp_path).encode(
            artifact_key="c" * 64,
            request=request,
            frames=[Frame(0, 0, png(np.ones((24, 32), np.uint16)))],
        )
