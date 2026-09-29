"""Official RealSense SDK visualization of decoded depth, without camera access.

Uses the SDK software-device input API, not a reimplementation of its color map.
Only a temporary Z16 display input is reconstructed; it is never a primary file.
See librealsense/wrappers/python/examples/numpy_to_frame.py and src/proc/colorizer.cpp.
"""

from importlib.metadata import version

import numpy as np

SDK_VERSION = "2.58.4.10922"


def display_profile(depth_max):
    return {
        "implementation": "pyrealsense2.colorizer",
        "sdk_version": SDK_VERSION,
        "visual_preset": "Dynamic",
        "color_scheme": "Jet",
        "histogram_equalization_enabled": True,
        "distance_color_scale": "per_frame_histogram",
        "invalid_depth": 0,
        "display_depth_units_m": float(depth_max) / 65535,
        "display_input": "reconstructed_z16_in_memory_only",
    }


class RealSenseColorizer:
    def __init__(self, shape, *, depth_max):
        # Import only when a preview is requested/generated. There is deliberately
        # no custom palette fallback if the required official SDK is unavailable.
        import pyrealsense2 as rs

        if version("pyrealsense2") != SDK_VERSION:
            raise RuntimeError("RealSense preview SDK differs from the frozen display profile")
        self.rs, self.shape = rs, tuple(shape)
        self.units = display_profile(depth_max)["display_depth_units_m"]
        self.device = rs.software_device()
        self.sensor = self.device.add_sensor("HC offline depth display")
        stream = rs.video_stream()
        stream.type, stream.fmt = rs.stream.depth, rs.format.z16
        stream.width, stream.height = self.shape[1], self.shape[0]
        stream.fps, stream.bpp, stream.index, stream.uid = 30, 2, 0, 0
        # Colorization uses pixels and units, not camera calibration or geometry.
        self.stream = self.sensor.add_video_stream(stream)
        self.queue = rs.frame_queue(1)
        self.colorizer = rs.colorizer()
        self.colorizer.set_option(rs.option.visual_preset, 0)  # Official Dynamic preset.
        self.colorizer.set_option(rs.option.color_scheme, 0)  # Official Jet map.
        self.colorizer.set_option(rs.option.histogram_equalization_enabled, 1)
        self.index = 0
        self.sensor.open(self.stream)
        self.sensor.start(self.queue)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.sensor.stop()
        self.sensor.close()

    def colorize(self, physical_m, flags):
        if physical_m.shape != self.shape or flags.shape != self.shape:
            raise ValueError("RealSense display input/mask shape mismatch")
        valid = flags & 1 != 0
        if not np.isfinite(physical_m[valid]).all() or (physical_m[valid] <= 0).any():
            raise ValueError("valid display depth must be finite and positive")
        pixels = np.zeros(self.shape, dtype=np.uint16)
        pixels[valid] = np.clip(np.rint(physical_m[valid] / self.units), 1, 65535).astype(np.uint16)
        frame = self.rs.software_video_frame()
        frame.pixels = pixels.data
        frame.bpp, frame.stride = 2, self.shape[1] * 2
        frame.profile = self.stream.as_video_stream_profile()
        frame.depth_units = self.units
        frame.timestamp, frame.frame_number = self.index * 1000 / 30, self.index
        frame.domain = self.rs.timestamp_domain.hardware_clock
        self.index += 1
        self.sensor.on_video_frame(frame)
        depth = self.queue.wait_for_frame(1000).as_depth_frame()
        return np.asanyarray(self.colorizer.colorize(depth).get_data()).copy()
