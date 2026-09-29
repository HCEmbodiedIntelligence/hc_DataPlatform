"""Pixel comparison against the SDK's default colorizer on a software Z16 frame."""

import io

import numpy as np
import pyrealsense2 as rs
from PIL import Image

from hc_data_platform.episode_store.realsense_colorizer import RealSenseColorizer, display_profile


def official_default(pixels, units):
    device = rs.software_device()
    sensor = device.add_sensor("Official example offline frame")
    stream = rs.video_stream()
    stream.type, stream.fmt = rs.stream.depth, rs.format.z16
    stream.width, stream.height = pixels.shape[1], pixels.shape[0]
    stream.fps, stream.bpp = 30, 2
    profile = sensor.add_video_stream(stream)
    queue = rs.frame_queue(1)
    colorizer = rs.colorizer()  # Official defaults, without any project settings.
    assert colorizer.get_option(rs.option.visual_preset) == 0
    assert colorizer.get_option(rs.option.color_scheme) == 0
    assert colorizer.get_option(rs.option.histogram_equalization_enabled) == 1
    sensor.open(profile)
    sensor.start(queue)
    try:
        frame = rs.software_video_frame()
        frame.pixels, frame.bpp, frame.stride = pixels.data, 2, pixels.shape[1] * 2
        frame.profile, frame.depth_units = profile.as_video_stream_profile(), units
        sensor.on_video_frame(frame)
        depth = queue.wait_for_frame(1000).as_depth_frame()
        return np.asanyarray(colorizer.colorize(depth).get_data()).copy()
    finally:
        sensor.stop()
        sensor.close()


def test_colour_pixels_match_official_dynamic_defaults_and_invalid_mask():
    values = np.array([[np.nan, 0.01, 0.3, 0.5], [1.0, 2.0, 3.0, 10.0]], dtype=np.float32)
    flags = np.ones(values.shape, dtype=np.uint8)
    flags[0, 0] = 0
    flags[1, 1] = 0  # HC invalidity excludes even a positive stored depth.
    units = display_profile(10)["display_depth_units_m"]
    pixels = np.zeros(values.shape, dtype=np.uint16)
    valid = flags & 1 != 0
    pixels[valid] = np.rint(values[valid] / units).clip(1, 65535).astype(np.uint16)
    expected = official_default(pixels, units)
    with RealSenseColorizer(values.shape, depth_max=10) as colorizer:
        actual = colorizer.colorize(values, flags)
        np.testing.assert_array_equal(actual, expected)
        np.testing.assert_array_equal(actual[~valid], 0)
        # The official implementation, including its all-missing frame behavior,
        # is shared by still/video previews without an alternative local palette.
        np.testing.assert_array_equal(colorizer.colorize(values, np.zeros_like(flags)), 0)
    output = io.BytesIO()
    Image.fromarray(actual).save(output, format="PNG")
    with Image.open(io.BytesIO(output.getvalue())) as image:
        np.testing.assert_array_equal(np.asarray(image), expected)
