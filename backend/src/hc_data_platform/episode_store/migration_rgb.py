"""Lossless RGB selection from a verified shared video; preserve decoded pixels.

Source timestamps must already be bound to authorized historical references.
Source frame numbers select pixels, while real decode PTS confirms the source
constant grid. Encoder output is independently decoded and compared in full.
"""

import hashlib
from fractions import Fraction

import av

from hc_data_platform.continuous_recordings.openarm_raw.video import TrainingVideo
from hc_data_platform.publishing.depth_evidence import file_hash
from hc_data_platform.publishing.export_video import inspect_video

from .models import digest
from .postgres import PackageConflict
from .prepared_assets import PreparedVideo

VERSION = "hc-native-rgb-selection/1"


def prepare_rgb_selection(media, frame_indexes, target_fps, destination):
    indexes = tuple(frame_indexes)
    try:
        fps = Fraction(str(media.feature["info"]["video.fps"]))
        target_rate = Fraction(str(target_fps))
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise PackageConflict("historical RGB selection requires explicit finite rates") from exc
    if (
        not indexes
        or len(indexes) > 100000
        or fps <= 0
        or target_rate <= 0
        or fps > 1000
        or target_rate > 1000
        or any(type(i) is not int or i < 0 for i in indexes)
        or any(b < a for a, b in zip(indexes, indexes[1:], strict=False))
        or media.path.is_symlink()
        or file_hash(media.path) != media.sha256
        or media.path.stat().st_size != media.size
        or media.feature.get("shape", [None])[-1] != 3
    ):
        raise PackageConflict("invalid or changed historical RGB selection input")
    expected, selected = hashlib.sha256(), 0
    with av.open(str(media.path)) as container:
        if len(container.streams.video) != 1 or container.streams.audio:
            raise PackageConflict("historical RGB selection requires one silent video stream")
        stream = container.streams.video[0]
        count, shape = stream.frames, media.feature["shape"]
        if (
            not 0 < count <= 1000000
            or indexes[-1] >= count
            or stream.average_rate != fps
            or stream.start_time not in (None, 0)
            or (stream.height, stream.width) != tuple(shape[:2])
            or stream.height * stream.width * 3 > 64 * 1024**2
            or stream.codec_context.pix_fmt.startswith("gray")
        ):
            raise PackageConflict("historical RGB source grid/shape/count differs")
        whole = indexes == tuple(range(count)) and fps == target_rate
        writer = None if whole else TrainingVideo(destination, target_fps, shape, lossless_rgb=True)
        original_codec = stream.codec_context.name
        try:
            if indexes[0]:
                container.seek(
                    int(Fraction(indexes[0]) / fps / stream.time_base), stream=stream, backward=True
                )
            previous = None
            for frame in container.decode(stream):
                if frame.pts is None:
                    raise PackageConflict("historical RGB source frame has no PTS")
                stamp = frame.pts * frame.time_base
                index = round(stamp * fps)
                if (
                    index < 0
                    or index >= count
                    or abs(stamp - Fraction(index) / fps) > Fraction(1, 1000000)
                    or previous is not None
                    and index != previous + 1
                    or (frame.height, frame.width) != tuple(shape[:2])
                ):
                    raise PackageConflict("historical decoded RGB source index/time differs")
                previous = index
                if index > indexes[selected]:
                    raise PackageConflict("historical RGB seek skipped a selected source frame")
                if index == indexes[selected]:
                    pixels = frame.to_ndarray(format="rgb24")
                    while selected < len(indexes) and indexes[selected] == index:
                        expected.update(pixels.tobytes())
                        if writer is not None:
                            writer.write(pixels)
                        selected += 1
                    if selected == len(indexes):
                        break
            if selected != len(indexes):
                raise PackageConflict("historical RGB source omitted selected frames")
        finally:
            if writer is not None:
                writer.close()
    output = media.path if whole else destination
    observed, frames = hashlib.sha256(), 0
    with av.open(str(output)) as encoded:
        for frame in encoded.decode(video=0):
            if (
                frame.pts is None
                or frame.pts * frame.time_base != Fraction(frames) / target_rate
                or (frame.height, frame.width) != tuple(shape[:2])
            ):
                raise PackageConflict("historical RGB output timeline/shape differs")
            observed.update(frame.to_ndarray(format="rgb24").tobytes())
            frames += 1
        actual_profile = encoded.streams.video[0].codec_context.profile
        actual_pix_fmt = encoded.streams.video[0].codec_context.pix_fmt
    if frames != len(indexes) or observed.hexdigest() != expected.hexdigest():
        raise PackageConflict("historical RGB decoded pixel equivalence failed")
    feature, _ = inspect_video(output, frame_count=frames, fps=target_fps)
    prepared = PreparedVideo(output, file_hash(output), output.stat().st_size, feature)
    configuration = {
        "version": VERSION,
        "encoder": None if whole else "libx264rgb",
        "options": None
        if whole
        else {
            "crf": "0",
            "preset": "veryfast",
            "bf": "0",
            "tune": "zerolatency",
        },
        "pyav": av.__version__,
        "libraries": {name: list(version) for name, version in av.library_versions.items()},
    }
    report = {
        "format": VERSION,
        "operation": "byte_copy" if whole else "lossless_rgb_window",
        "transcoded": not whole,
        "requantized": False,
        "source_sha256": media.sha256,
        "source_size": media.size,
        "source_codec": original_codec,
        "source_frame_count": count,
        "source_fps": float(fps),
        "target_fps": float(target_rate),
        "frame_count": frames,
        "frame_indexes_sha256": digest(list(indexes)),
        "selected_source_rgb_sha256": expected.hexdigest(),
        "output_rgb_sha256": observed.hexdigest(),
        "decoded_pixels_exact": True,
        "output_sha256": prepared.sha256,
        "output_size": prepared.size,
        "output_profile": actual_profile,
        "output_pix_fmt": actual_pix_fmt,
        "encoding": configuration,
        "encoding_sha256": digest(configuration),
    }
    return prepared, report


def verify_rgb_preparation(path, report, source_references, *, frame_count, fps):
    """Recheck the frozen lossless selection and original logical frame binding."""
    from hc_data_platform.aligned_media.models import AlignedMediaFrameReferenceV1

    references = [AlignedMediaFrameReferenceV1.model_validate(ref) for ref in source_references]
    if (
        report.get("format") != VERSION
        or report.get("operation") not in {"byte_copy", "lossless_rgb_window"}
        or report.get("transcoded") != (report["operation"] == "lossless_rgb_window")
        or report.get("requantized") is not False
        or report.get("decoded_pixels_exact") is not True
        or report.get("frame_count") != frame_count
        or report.get("target_fps") != fps
        or report.get("source_references_sha256")
        != digest([ref.model_dump(mode="json") for ref in references])
        or report.get("frame_indexes_sha256") != digest([ref.frame_index for ref in references])
        or report.get("encoding_sha256") != digest(report.get("encoding"))
        or report.get("selected_source_rgb_sha256") != report.get("output_rgb_sha256")
        or report.get("output_sha256") != file_hash(path)
        or report.get("output_size") != path.stat().st_size
    ):
        raise PackageConflict("historical RGB preparation receipt differs from the frozen package")
    observed, frames = hashlib.sha256(), 0
    with av.open(str(path)) as video:
        for frame in video.decode(video=0):
            if frame.pts is None or frame.pts * frame.time_base != Fraction(frames) / Fraction(
                str(fps)
            ):
                raise PackageConflict("historical RGB prepared output clock differs")
            observed.update(frame.to_ndarray(format="rgb24").tobytes())
            frames += 1
    if frames != frame_count or observed.hexdigest() != report["selected_source_rgb_sha256"]:
        raise PackageConflict("historical RGB prepared pixel hash differs")
