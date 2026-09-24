"""Platform-only preview encoding from original captured frame references."""

from fractions import Fraction
from pathlib import Path

import av


class FrameReader:
    def __init__(self, root):
        self.root = Path(root)
        self.container = None
        self.path = None
        self.index = -1
        self.frame = None

    def read(self, binding, seek=False):
        path = binding["video_segment_path"]
        target = binding["display_frame_index"]
        if path != self.path or target < self.index:
            self.close()
            self.container = av.open(str(self.root / path))
            self.iterator = iter(self.container.decode(video=0))
            self.path, self.index = path, -1
        expected = Fraction(binding["pts"]) * Fraction(*binding["time_base"])
        if seek and target > self.index + 30:
            stream = self.container.streams.video[0]
            self.container.seek(int(expected / stream.time_base), stream=stream, backward=True)
            self.iterator = iter(self.container.decode(video=0))
            for frame in self.iterator:
                stamp = frame.pts * frame.time_base
                if stamp == expected:
                    self.frame, self.index = frame, target
                    break
                if stamp > expected:
                    raise ValueError("预览索引对应的 PTS 不存在")
        while self.index < target:
            try:
                self.frame = next(self.iterator)
            except StopIteration as error:
                raise ValueError("已提交视频缺少映射帧: " + path) from error
            self.index += 1
        if abs(self.frame.pts * self.frame.time_base - expected) > Fraction(1, 1000000):
            raise ValueError("视频帧 PTS 与索引不一致")
        return self.frame.to_ndarray(format="rgb24")

    def close(self):
        if self.container is not None:
            self.container.close()
        self.container = None
        self.path = None


class TrainingVideo:
    def __init__(self, path, fps, shape):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.container = av.open(str(path), "w")
        self.stream = self.container.add_stream("libx264", rate=fps)
        self.stream.height, self.stream.width = shape[:2]
        self.stream.pix_fmt = "yuv420p"
        self.stream.codec_context.thread_count = 1
        self.stream.options = {"crf": "18", "preset": "veryfast", "bf": "0", "tune": "zerolatency"}
        self.stream.time_base = Fraction(1, fps)
        self.fps, self.frames = fps, 0

    def write(self, pixels):
        frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
        frame.pts = self.frames
        frame.time_base = Fraction(1, self.fps)
        for packet in self.stream.encode(frame):
            self.container.mux(packet)
        self.frames += 1

    def close(self):
        for packet in self.stream.encode(None):
            self.container.mux(packet)
        self.container.close()
