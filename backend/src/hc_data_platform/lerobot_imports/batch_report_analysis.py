"""Format only evidence responsible for a terminal failure in the official report."""

import re
from ast import literal_eval
from collections import defaultdict
from itertools import groupby

from hc_data_platform.continuous_recordings.quality_checks import RECORDING_CHECKS


def frame_ranges(numbers):
    ranges = []
    for _, group in groupby(enumerate(sorted(set(numbers))), lambda pair: pair[1] - pair[0]):
        ns = [n for _, n in group]
        ranges.append(str(ns[0]) if len(ns) == 1 else f"{ns[0]}–{ns[-1]}")
    return "、".join(ranges)


def source_topic(cfg, topic):
    for camera in cfg.get("cameras", []):
        if topic == f"/camera/{camera['camera_id']}/image":
            return camera["topic"]
    if topic in {"/action", "/humanoid/action"}:
        return cfg.get("action_topic") or topic
    if topic in {"/observation/state", "/humanoid/observation/state"}:
        return cfg.get("state_topic") or topic
    return topic or ""


def action_axes(capture, cfg):
    """Prefer the axes required by this recorded QC result over today's defaults."""
    validity = capture.get("axis_validity", {}).get("action", {})
    feature = (cfg.get("semantic_output") or {}).get("features", {}).get("action", {})
    declared = feature.get("names") or cfg.get("action_joint_names") or cfg.get("joint_names") or []
    required = validity.get("required_axes")
    if required is None:
        required = feature.get("required_axes")
    if required is None:
        required = cfg.get("action_required_axes")
    if required is None:
        return list(declared)
    return list(dict.fromkeys([n for n in declared if n in required] + list(required)))


def interior_items(capture, cfg):
    names = action_axes(capture, cfg)
    groups = defaultdict(list)
    for gap in capture.get("interior_action_gaps", []):
        if isinstance(gap.get("frame"), int):
            groups[gap.get("origin")].append(gap["frame"])
    topic = source_topic(cfg, "/action")
    if not groups:
        yield "录制中段缺少实录关节动作指令", topic, ""
    for origin, frames in groups.items():
        where = f"第 {frame_ranges(frames)} 帧（共 {len(set(frames))} 帧）"
        if origin == "missing":
            message = f"{where}未匹配到整条关节动作消息"
            if names:
                message += f"，缺少 {len(names)} 个关节的动作位置（position）：{'、'.join(names)}"
        else:
            message = f"{where}缺少实录关节动作指令"
            if names:
                message += f"（实录动作关节：{'、'.join(names)}）"
            source = {"next_frame_state": "下一帧状态补写", "terminal_state": "末帧状态补写",
                      "unfilled_state": "无可用状态补写"}.get(origin)
            if source:
                message += f"，{source}"
        yield message, topic, ""


def missing_axis_counts(capture, cfg):
    for key, label, topic in (("action", "动作位置", "/action"),
                              ("observation.state", "实测关节位置", "/observation/state")):
        validity = capture.get("axis_validity", {}).get(key, {})
        counts = validity.get("missing_rows_by_axis", {})
        affected = [f"{n}（{counts[n]} 帧）" for n in validity.get("required_axes", []) if counts.get(n)]
        if affected:
            yield "质检窗口内缺少" + label + "：" + "、".join(affected), source_topic(cfg, topic), ""

def rejection(report, cfg):
    capture = report.get("capture_quality") or {}
    quality = report.get("quality_report", report)
    codes = report.get("finding_codes", [])
    # Some legacy profiles explicitly reject the recording on a RISK verdict.
    severity = "warning" if "QUALITY_RISK" in codes or (quality is report and quality.get("status") == "RISK") else "error"
    findings = [f for f in quality.get("findings", []) if f.get("severity") == severity]
    for f in findings:
        label = "缺少必需话题" if f["code"] == "QC_REQUIRED_TOPIC_MISSING" else f.get("message") or f["code"]
        detail = []
        for key, name in (("observed", "实测"), ("threshold", "阈值")):
            if f.get(key) is not None:
                detail.append(f"{name} {f[key]}")
        if f.get("start_ns") is not None and quality.get("start_ns") is not None:
            start = (f["start_ns"] - quality["start_ns"]) / 1e9
            end = (f.get("end_ns", f["start_ns"]) - quality["start_ns"]) / 1e9
            detail.append(f"{start:.3f}–{end:.3f} 秒")
        text = f"{label}（{'，'.join(detail)}）" if detail else label
        yield text, source_topic(cfg, f.get("topic")), ""
    for code in dict.fromkeys([*codes, *capture.get("reject_codes", [])]):
        if findings and (code in {"QUALITY_REJECT", "QUALITY_RISK"} or any(f["code"] == code for f in findings)):
            continue
        topic = ""
        label = RECORDING_CHECKS.get(code, code)
        if code == "MCAP_INTERIOR_ACTION_MISSING":
            yield from interior_items(capture, cfg)
            continue
        elif code == "CAPTURE_VALID_RATIO_LOW" and capture.get("valid_ratio") is not None:
            label += f"（有效 {capture['valid_rows']}/{capture['total_rows']} 帧，占 {capture['valid_ratio']:.2%}）"
        elif code == "CAPTURE_INTERRUPTION_TOO_LONG" and capture.get("longest_invalid_run") is not None:
            label += f"（连续 {capture['longest_invalid_run']} 帧）"
        yield label, topic, ""
        if code == "CAPTURE_VALID_RATIO_LOW":
            yield from missing_axis_counts(capture, cfg)


def failure_items(stage, report, error, cfg):
    if stage == "QC_FAILED":
        items = list(rejection(report or {}, cfg or {}))
        return [(f"质检未通过 · {reason}", topic, note) for reason, topic, note in items] or [
            (f"质检未通过 · {error or '未记录具体检查项'}", "", "")
        ]
    cfg = cfg or {}
    match = re.fullmatch(r"MCAP_FOLLOWING_STATE_UNAVAILABLE:\s*(\S+)\s+capture_time_ns=(\d+)", error or "")
    if match:
        topic, stamp = match.groups()
        reason = "处理失败 · 动作之后无可匹配的关节状态"
        rules = (cfg.get("postprocessing_rules") or {}).get("rules", [])
        for rule in rules:
            if rule.get("enabled") and rule.get("kind") == "action_from_state" and rule.get("axes"):
                reason += "，无法读取这些关节的实测位置（position）：" + "、".join(rule["axes"])
                break
        note = f"动作采集时间 {stamp} ns，缺后续 state"
        if cfg.get("state_topic"):
            note += f"（动作话题 {topic}）"
        return [(reason, cfg.get("state_topic") or topic, note)]
    match = re.fullmatch(r"MCAP_JOINT_LAYOUT_MISMATCH: (\S+) missing=(\[.*\]) at (\d+)", error or "")
    if match:
        topic, raw_names, stamp = match.groups()
        try:
            names = literal_eval(raw_names)
        except (SyntaxError, ValueError):
            names = None
        if isinstance(names, list) and names and all(isinstance(n, str) for n in names):
            return [(f"处理失败 · 关节消息缺少位置（position）：{'、'.join(names)}（采集时间 {stamp} ns）", topic, "")]
    return [(f"处理失败 · {error or '未记录具体错误'}", "", "")]
