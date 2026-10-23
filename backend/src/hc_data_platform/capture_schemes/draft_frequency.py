"""Record sample rate metadata; rates are never requirements for later uploads."""

import copy
import statistics
from pathlib import Path

from mcap.reader import make_reader

from .common import Document


def with_frequency(document: Document, frequency: float) -> Document:
    result = copy.deepcopy(document)
    for adapter in result["adapters"]:
        if (
            adapter["source_format"] in {"MCAP", "CAPTURE_BUNDLE"}
            and adapter["configuration"].get("fps") is None
        ):
            adapter["configuration"]["fps"] = max(1, round(frequency))
        if (
            adapter["source_format"] in {"MCAP", "CAPTURE_BUNDLE"}
            and adapter["configuration"].get("semantic_output") is not None
        ):
            adapter["configuration"]["semantic_output"] = copy.deepcopy(result["output"])
    return result


def mcap_frequency(path: Path, configuration: Document) -> int:
    topic = configuration.get(
        "primary_camera" if configuration["time_source"] == "RGB_LOG_TIME" else "state_topic"
    )
    if not topic:
        raise ValueError("请选择 MCAP 时间来源对应的状态话题或主相机")
    intervals: list[int] = []
    previous: int | None = None
    with path.open("rb") as stream:
        for _, _, message in make_reader(stream, validate_crcs=True).iter_messages(
            topics=[topic], log_time_order=False
        ):
            if previous is not None:
                if message.log_time <= previous:
                    raise ValueError("MCAP 时间来源存在重复或倒退时间，无法自动读取帧率")
                intervals.append(message.log_time - previous)
            previous = message.log_time
    if not intervals:
        raise ValueError("MCAP 时间来源至少需要两条消息才能读取帧率")
    observed = 1e9 / statistics.median(intervals)
    # The media encoder needs a nominal integer rate. Fractional rates and
    # ordinary clock jitter are valid; the source timestamps remain authoritative.
    return max(1, round(observed))
