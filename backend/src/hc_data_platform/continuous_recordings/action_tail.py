"""Audit a contiguous command tail against the final measured state."""
from mcap.reader import make_reader

from hc_data_platform.alignment.timing import JOINT_MATCH_NS

MAX_ACTION_TAIL_NS = 100_000_000


def summarize_tail(path, cfg, sources, dropped, progress):
    tail = [row for row in dropped if "last_state_capture_time_ns" in row]
    first, last = tail[0], tail[-1]
    end = first["last_state_capture_time_ns"]
    gap = last["capture_time_ns"] - end
    summary = {
        "first_action_number": first["action_number"], "last_action_number": last["action_number"],
        "first_capture_time_ns": first["capture_time_ns"], "last_capture_time_ns": last["capture_time_ns"],
        "last_state_capture_time_ns": end, "action_count": len(tail), "tail_delta_ns": gap,
        "limit_ns": MAX_ACTION_TAIL_NS,
    }
    rgb_detail = ""
    if cfg.normalization and cfg.time_source == "RGB_LOG_TIME":
        from hc_data_platform.verification.ports import McapRos2DecoderProbe
        from .mcap_native import capture_stamp

        topics = [topic for topic, selected in sources.items() if selected == cfg.primary_camera]
        decoder = McapRos2DecoderProbe()
        outside = set()
        with path.open("rb") as stream:
            for schema, channel, message in make_reader(stream, validate_crcs=True).iter_messages(topics=topics):
                progress()
                stamp = capture_stamp(schema, channel, message, decoder, cfg)
                if stamp > end + JOINT_MATCH_NS:
                    outside.add(stamp)
        summary["rgb_after_state_window"] = len(outside)
        rgb_detail = f" rgb_after_state_window={len(outside)}"
    if gap > MAX_ACTION_TAIL_NS:
        raise ValueError(
            f"{first['reason']} last_state_ns={end} tail_actions={len(tail)} "
            f"tail_delta_ns={gap} limit_ns={MAX_ACTION_TAIL_NS}{rgb_detail}"
        )
    return summary
