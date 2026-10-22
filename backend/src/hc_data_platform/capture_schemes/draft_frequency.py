"""Resolve optional draft rates from verified samples before freezing a version."""

import copy
import math
import statistics
from pathlib import Path

from mcap.reader import make_reader

from .common import Document


def with_frequency(document: Document, frequency: float) -> Document:
    result = copy.deepcopy(document)
    if result["output"].get("frequency_hz") is None:
        result["output"]["frequency_hz"] = frequency
    for adapter in result["adapters"]:
        if (
            adapter["source_format"] in {"MCAP", "CAPTURE_BUNDLE"}
            and adapter["configuration"].get("fps") is None
        ):
            adapter["configuration"]["fps"] = result["output"]["frequency_hz"]
        if (
            adapter["source_format"] in {"MCAP", "CAPTURE_BUNDLE"}
            and adapter["configuration"].get("semantic_output") is not None
        ):
            adapter["configuration"]["semantic_output"] = copy.deepcopy(result["output"])
        if (
            adapter["source_format"] in {"MCAP", "CAPTURE_BUNDLE"}
            and adapter["configuration"]["fps"] != result["output"]["frequency_hz"]
        ):
            raise ValueError("MCAP 配置帧率与样本原始帧率不一致")
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
    frequency = round(observed)
    # Permit nanosecond timestamp rounding, never turn e.g. 29.97 into 30 Hz.
    if not 1 <= frequency <= 240 or not math.isclose(observed, frequency, rel_tol=1e-6):
        raise ValueError("MCAP 样本帧率无法确认为 1–240 Hz 整数，请核对原始时间来源")
    return frequency
