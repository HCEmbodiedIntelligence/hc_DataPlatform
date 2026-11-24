"""Platform HoloBrain writer over frozen, normalized selected MCAPs."""

import hashlib
import io
import json
import sqlite3
from collections import Counter
from contextlib import ExitStack, closing
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pyarrow as pa
from mcap.reader import make_reader
from PIL import Image

from hc_data_platform.alignment.timing import JOINT_MATCH_MS
from hc_data_platform.continuous_recordings.joint_normalization import (
    HANDS, capture_time, nearest_index, reference,
)
from hc_data_platform.continuous_recordings.mcap_native import decoded
from hc_data_platform.episode_store.mcap_images import get_field
from hc_data_platform.verification.ports import McapRos2DecoderProbe

from .holobrain_report import ReferenceConversionReport
from .holobrain_schema import FEATURES, JOINT_LAYOUTS, create_database, profile_name, schema, tensor
from .models import canonical, digest
from .normalized_export import frame_selection, selected_source
from .original_export import copy_original, inventory

RGB = "/observation/cameras/head/color/compressed"
DEPTH = "/observation/cameras/head/depth/compressed"
STATE = "/observation/joint_states"
ACTION = "/action/joint_states"
PARAMETERS = ("/observation/cameras/head/calibration", "/observation/cameras/head/extrinsics")


def image_bytes(value, *, depth):
    body = bytes(get_field(value, "data", b""))
    with Image.open(io.BytesIO(body)) as image:
        image.load()
        if image.size != (640, 400) or image.format != ("PNG" if depth else "JPEG"):
            raise ValueError("HOLOBRAIN_IMAGE_PROFILE_MISMATCH: requires 640x400 JPEG / uint16 PNG")
        if depth and (np.asarray(image).dtype != np.uint16 or len(image.getbands()) != 1):
            raise ValueError("HOLOBRAIN_DEPTH_DTYPE_INVALID: requires uint16")
        if not depth and image.mode != "RGB":
            raise ValueError("HOLOBRAIN_RGB_MODE_INVALID")
    return body


def camera(value, stamp, topic, calibration, extrinsics):
    rgb = calibration["rgb"]
    if rgb.get("distortion_order") != ["k1", "k2", "p1", "p2", "k3"]:
        raise ValueError("HOLOBRAIN_DISTORTION_ORDER_REQUIRED")
    resolution = calibration["resolution"]
    if any(resolution[k] != {"width": 640, "height": 400} for k in ("color", "depth")):
        raise ValueError("HOLOBRAIN_CALIBRATION_SIZE_MISMATCH")
    transforms = [t for t in extrinsics["transforms"]
                  if t["parent_frame"] == "zhi_Link" and t["child_frame"] == "camera_head"]
    if len(transforms) != 1:
        raise ValueError("HOLOBRAIN_CAMERA_POSE_REQUIRED")
    transform = transforms[0]
    quat = transform["rotation_xyzw"]
    if len(quat) != 4 or not np.isclose(np.linalg.norm(quat), 1, atol=1e-5):
        raise ValueError("HOLOBRAIN_CAMERA_QUATERNION_INVALID")
    return {
        "topic": topic, "frame_id": "eye", "image_shape": [400, 640],
        "intrinsic_matrices": tensor([rgb["fx"], 0, rgb["cx"], 0, rgb["fy"], rgb["cy"], 0, 0, 1], [1, 3, 3]),
        "transform_matrices": None,
        "distortion": {"model": "plumb_bob", "coefficients": tensor(rgb["distortion_coefficients"], [5])},
        "pose": {"xyz": tensor(transform["translation_m"], [1, 3]),
                 "quat": tensor([quat[3], *quat[:3]], [1, 4]), "timestamps": [stamp],
                 "parent_frame_id": "zhi_Link", "child_frame_id": "eye"},
        "sensor_data": [image_bytes(value, depth=topic == DEPTH)],
        "format": "png" if topic == DEPTH else "jpeg", "timestamps": [stamp],
    }


def _joint_indices(value, required, error):
    names = list(get_field(value, "name", []))
    if any(names.count(name) != 1 for name in required):
        raise ValueError(error)
    return {name: names.index(name) for name in required}


def joints(sample, hands, missing, joint_names=JOINT_LAYOUTS["16dof"]):
    value, ref = sample
    body_joints = tuple(name for name in joint_names if name not in HANDS.values())
    indices = _joint_indices(
        value, body_joints,
        "HOLOBRAIN_NORMALIZATION_REQUIRED: requires each selected body joint exactly once",
    )
    hand_indices = {
        joint: _joint_indices(hands[joint][0], (joint,), "HOLOBRAIN_HAND_SOURCE_INVALID")[joint]
        for joint in HANDS.values() if joint in hands
    }
    result = {"names": list(joint_names), "timestamps": [ref["capture_time_ns"]]}
    for field in ("position", "velocity", "effort"):
        values = list(get_field(value, field, []))
        if (field == "position" or values) and any(i >= len(values) for i in indices.values()):
            raise ValueError("HOLOBRAIN_POSITION_REQUIRED" if field == "position"
                             else f"HOLOBRAIN_TENSOR_INVALID: incomplete {field}")
        # Only target axes participate in export and validation. Other axes may
        # be unordered, duplicated, absent from value arrays or non-finite.
        mapping = {name: values[i] for name, i in indices.items()} if values else {}
        for joint, index in hand_indices.items():
            hand_values = list(get_field(hands[joint][0], field, []))
            if field == "position" or hand_values:
                if index >= len(hand_values):
                    raise ValueError("HOLOBRAIN_HAND_COMMAND_REQUIRED" if field == "position"
                                     else f"HOLOBRAIN_TENSOR_INVALID: incomplete hand {field}")
                mapping[joint] = hand_values[index]
        if field == "position" and set(mapping) != set(joint_names):
            raise ValueError("HOLOBRAIN_HAND_COMMAND_REQUIRED")
        missing.update(field + "." + name for name in joint_names if name not in mapping)
        result[field] = tensor([mapping.get(name, 0.0) for name in joint_names], [1, len(joint_names)])
    return result


def committed_action_labels(package, frames):
    """Recover only selected, state-derived action labels from this immutable Episode."""
    import pyarrow.parquet as pq
    from hc_data_platform.data_formats.lerobot_layout import DATA_PATH

    with package.file(DATA_PATH) as path:
        parquet = pq.ParquetFile(path)
        origin_column = "hc.action_source_origins"
        if origin_column not in parquet.schema_arrow.names:
            return {}
        origins = parquet.read(columns=[origin_column])[origin_column].to_pylist()
        fills = {}
        for stamp, frame in frames.items():
            index = frame["parent_frame_index"]
            origin = json.loads(origins[index]) if origins[index] else {}
            if origin.get("origin") in {"next_frame_state", "terminal_state"}:
                fills[stamp] = (index, origin)
        if not fills:
            return {}
        table = parquet.read(columns=["action", "hc.valid.action", "hc.axis_validity.action"])

    names = package.document("meta/info.json")["features"]["action"]["names"]
    labels = {}
    for stamp, (index, origin) in fills.items():
        if (not table["hc.valid.action"][index].as_py()
                or origin.get("source_topic") != STATE
                or type(origin.get("source_timestamp_ns")) is not int):
            raise ValueError("HOLOBRAIN_INVALID_COMMITTED_ACTION")
        mask = json.loads(table["hc.axis_validity.action"][index].as_py())
        positions = table["action"][index].as_py()
        valid = [(name, value) for name, value, ok in zip(names, positions, mask, strict=True) if ok]
        sample = {"name": [name for name, _ in valid], "position": [value for _, value in valid]}
        labels[stamp] = (sample, {**origin, "capture_time_ns": origin["source_timestamp_ns"]})
    return labels


def synchronized(path, temporary, report, checkpoint, reference_report=None, *, joint_names=JOINT_LAYOUTS["16dof"],
                 action_labels=None, legacy_sync=False):
    """Order images by acquisition time on disk; keep numeric streams in memory."""
    joint_match_ns = (20 if legacy_sync else JOINT_MATCH_MS) * 1_000_000
    action_labels = action_labels or {}
    decoder = McapRos2DecoderProbe()
    numeric = {topic: [] for topic in (STATE, ACTION, *HANDS)}
    parameters = {topic: [] for topic in PARAMETERS}
    depth_times, depth_refs = [], []
    db = sqlite3.connect(str(Path(temporary) / "depth.sqlite"))
    try:
        db.execute("CREATE TABLE depth(id INTEGER PRIMARY KEY, data BLOB)")
        db.execute("CREATE TABLE rgb(id INTEGER PRIMARY KEY, stamp INTEGER, data BLOB, ref TEXT)")
        with Path(path).open("rb") as stream:
            for ordinal, (s, c, m) in enumerate(make_reader(stream, validate_crcs=True).iter_messages(log_time_order=False)):
                checkpoint()
                if reference_report is not None:
                    reference_report.messages[c.topic] += 1
                if c.topic not in {*numeric, *parameters, DEPTH, RGB}:
                    continue
                value = decoded(s, c, m, decoder)
                if c.topic in parameters:
                    raw = get_field(value, "data", None)
                    body = json.loads(raw) if isinstance(raw, str) else value
                    parameters[c.topic].append(body)
                    continue
                stamp = capture_time(value, m)
                ref = reference(m, stamp, ordinal)
                if c.topic == RGB:
                    db.execute("INSERT INTO rgb VALUES (?,?,?,?)",
                               (ordinal, stamp, image_bytes(value, depth=False), json.dumps(ref)))
                elif c.topic == DEPTH:
                    body = image_bytes(value, depth=True)
                    db.execute("INSERT INTO depth VALUES (?,?)", (len(depth_times), body))
                    depth_times.append(stamp)
                    depth_refs.append(ref)
                else:
                    numeric[c.topic].append((value, ref))
        for topic, values in parameters.items():
            if not values or any(v != values[0] for v in values):
                raise ValueError(f"HOLOBRAIN_CALIBRATION_CHANGED_OR_MISSING: {topic}")
        empty = [topic for topic, values in numeric.items()
                 if not values and not (topic == ACTION and action_labels)]
        if not depth_times:
            empty.append(DEPTH)
        if empty:
            raise ValueError(f"HOLOBRAIN_EMPTY_REQUIRED_MODALITY: {', '.join(empty)}")
        depth_order = sorted(range(len(depth_times)), key=lambda i: (depth_times[i], i))
        ordered_depth = [depth_times[i] for i in depth_order]
        for values in numeric.values():
            values.sort(key=lambda item: item[1]["capture_time_ns"])
        times = {topic: [r["capture_time_ns"] for _, r in values] for topic, values in numeric.items()}
        for topic, stamps in {**times, DEPTH: ordered_depth}.items():
            if len(set(stamps)) != len(stamps):
                raise ValueError(f"HOLOBRAIN_DUPLICATE_ACQUISITION_TIME: {topic}")
        report["calibration"] = {"intrinsics": parameters[PARAMETERS[0]][0], "extrinsics": parameters[PARAMETERS[1]][0]}
        if db.execute("SELECT stamp FROM rgb GROUP BY stamp HAVING COUNT(*)>1 LIMIT 1").fetchone():
            raise ValueError(f"HOLOBRAIN_DUPLICATE_ACQUISITION_TIME: {RGB}")
        for stamp, image, raw_ref in db.execute("SELECT stamp,data,ref FROM rgb ORDER BY stamp,id"):
            checkpoint()
            value, rgb_ref = {"data": image}, json.loads(raw_ref)
            report["candidate_frames"] += 1
            chosen = {topic: nearest_index(t, stamp, 150_000_000 if topic in HANDS else joint_match_ns) for topic, t in times.items()}
            depth = nearest_index(ordered_depth, stamp, 50_000_000)
            label = action_labels.get(stamp) if legacy_sync or chosen[ACTION] is None else None
            if label is not None:
                del chosen[ACTION]
            if reference_report is not None:
                reference_report.observe(stamp, {
                    **{topic: times[topic][index] if index is not None else None
                       for topic, index in chosen.items()},
                    DEPTH: ordered_depth[depth] if depth is not None else None,
                }, state_label=label is not None)
            missing = [topic for topic, index in chosen.items() if index is None] + ([DEPTH] if depth is None else [])
            if missing:
                report["sync_drops"].append({"rgb_time_ns": stamp, "missing": missing})
                continue
            samples = {topic: numeric[topic][index] for topic, index in chosen.items()}
            if label is not None:
                samples[ACTION] = label
            hand_samples = {joint: samples[topic] for topic, joint in HANDS.items()}
            di = depth_order[depth]
            depth_body = db.execute("SELECT data FROM depth WHERE id=?", (di,)).fetchone()[0]
            calibration, extrinsics = (parameters[t][0] for t in PARAMETERS)
            missing_zero = Counter()
            row = {"joints": joints(samples[STATE], hand_samples, missing_zero, joint_names),
                   "actions": joints(samples[ACTION], hand_samples, missing_zero, joint_names),
                   "head": camera(value, stamp, RGB, calibration, extrinsics),
                   "head_depth": camera({"data": depth_body}, ordered_depth[depth], DEPTH, calibration, extrinsics),
                   "timestamp_min": min(stamp, ordered_depth[depth]), "timestamp_max": max(stamp, ordered_depth[depth])}
            refs = {topic: ref for topic, (_, ref) in samples.items()}
            refs.update({RGB: rgb_ref, DEPTH: depth_refs[di]})
            trace = {"rgb_time_ns": stamp, "sources": refs, "missing_zero": dict(missing_zero),
                     "hand_state": "command_proxy", "all_modalities_min_ns": min(r["capture_time_ns"] for r in refs.values()),
                     "all_modalities_max_ns": max(r["capture_time_ns"] for r in refs.values()),
                     "image_sha256": {key: hashlib.sha256(row[key]["sensor_data"][0]).hexdigest() for key in ("head", "head_depth")}}
            yield row, trace
    finally:
        db.close()


def _write_arrow_batch(writer, rows, arrow_schema):
    table = pa.Table.from_pylist(rows, schema=arrow_schema)
    writer.write_table(table)
    rows.clear()
    return table.nbytes


def write_dataset(root, sources, *, checkpoint=lambda: None, compact_invalid=True,
                  reference_report=False, config_name="16dof", legacy_sync=False, single_arrow=True):
    """Write validated IPC streams/official DuckDB metadata before publication.

    Sources contain normalized path, immutable provenance, task text and optional
    timestamp -> parent-frame/segment/task selection. No raw bytes are attached.
    reference_report selects the hc_tj report layout without changing frame data.
    """
    from .episode_outcome import outcome_fields

    joint_names = JOINT_LAYOUTS[config_name]
    profile = profile_name(config_name)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    reports, episodes, tasks, shards = [], [], [], []
    reference_episodes = []
    total, logical_bytes = 0, 0
    arrow_schema = schema()
    frame_policy = (
        "drop_invalid_compact_indices_preserve_source_time/v1"
        if compact_invalid else "drop_invalid_split_at_source_gaps/v1"
    )
    writer, batch = None, []
    with (root / "alignment_provenance.jsonl").open("wb") as trace_file, ExitStack() as streams:
        for source_index, source in enumerate(sources):
            outcome = outcome_fields(source["episode_outcome"]) if "episode_outcome" in source else {}
            reference = ReferenceConversionReport(source, joint_match_ms=20 if legacy_sync else JOINT_MATCH_MS) if reference_report else None
            provenance = source["provenance"]
            policy = provenance["config"]["normalization"]
            if policy["profile"] != "hc_tj_17dof/v1" or policy["camera_mode"] != "legacy_reference":
                raise ValueError("HOLOBRAIN_PROFILE_MISMATCH")
            report = {"source_sha256": provenance["selected_mcap"]["source_sha256"],
                      "selected_sha256": provenance["selected_mcap"]["sha256"],
                      "configuration_sha256": digest(provenance["config"]),
                      "selection_sha256": digest(source.get("frames", {"task": source.get("task")})),
                      "identity": source.get("identity"),
                      "normalization": provenance["selected_mcap"].get("normalization"),
                      "candidate_frames": 0, "sync_drops": [], "missing_zero": Counter(),
                      "selected_frames": 0, "selection_excluded_frames": 0}
            reports.append(report)
            audit_path = Path(source.get("audit_path", Path(source["path"]).parent / "normalization.json"))
            expected_audit = report["normalization"]["audit"]["sha256"]
            body = audit_path.read_bytes()
            if hashlib.sha256(body).hexdigest() != expected_audit:
                raise ValueError("HOLOBRAIN_NORMALIZATION_AUDIT_MISMATCH")
            audit_rows = json.loads(body)["messages"]
            action_audit = {(r["action"]["log_time_ns"], r["action"]["sequence"]): r
                            for r in audit_rows if r["topic"] == ACTION}
            current_segment, episode = None, None
            with TemporaryDirectory(dir=root.parent) as temporary:
                for row, trace in synchronized(
                    source["path"], temporary, report, checkpoint, reference, joint_names=joint_names,
                    action_labels=source.get("action_labels"), legacy_sync=legacy_sync,
                ):
                    stamp = trace["rgb_time_ns"]
                    selected = source.get("frames")
                    frame = selected.get(stamp) if selected is not None else {"task": source["task"], "segment": 0, "parent_frame_index": report["candidate_frames"] - 1}
                    if frame is None:
                        report["selection_excluded_frames"] += 1
                        continue
                    report["missing_zero"].update(trace["missing_zero"])
                    ref = trace["sources"][ACTION]
                    if "origin" in ref:
                        trace["action_normalization"] = ref
                    else:
                        trace["action_normalization"] = action_audit[(ref["log_time_ns"], ref["sequence"])]
                    segment = frame["segment"]
                    if segment != current_segment:
                        task = frame["task"]
                        if not task or task == "unknown":
                            raise ValueError("HOLOBRAIN_TASK_TEXT_REQUIRED")
                        if task not in tasks:
                            tasks.append(task)
                        episode = {**outcome, "index": len(episodes), "task_index": tasks.index(task), "begin": total,
                                   "frames": 0, "truncated": selected is not None,
                                   "info": {**outcome, "target_profile": profile, "source_sha256": report["source_sha256"],
                                            "source_selection": source.get("identity"), "camera_mode": "legacy_reference",
                                            "hand_state": "command_proxy", "velocity_effort": "missing_zero",
                                            "frame_policy": frame_policy,
                                            "normalization": report["normalization"], "task": task,
                                            **({"annotation_id": frame["annotation_id"]}
                                               if "annotation_id" in frame else {})}}
                        episodes.append(episode)
                        if writer is None or not single_arrow:
                            if batch:
                                logical_bytes += _write_arrow_batch(writer, batch, arrow_schema)
                            streams.close()
                            path = root / f"part-{len(shards):05d}.arrow"
                            sink = streams.enter_context(path.open("wb"))
                            writer = streams.enter_context(pa.ipc.new_stream(sink, arrow_schema))
                            shards.append(path)
                        current_segment = segment
                    row.update(index=total, episode_index=episode["index"], frame_index=episode["frames"],
                               task_index=episode["task_index"], robot_index=0, instruction_index=episode["task_index"])
                    batch.append(row)
                    trace_file.write(canonical({**trace, "index": total, "episode_index": episode["index"],
                                                "frame_index": episode["frames"], "source_index": source_index,
                                                "parent_frame_index": frame["parent_frame_index"]}) + b"\n")
                    episode["frames"] += 1
                    if reference is not None:
                        reference.select(stamp, episode["index"], action_origin=ref.get("origin"))
                    report["selected_frames"] += 1
                    total += 1
                    if len(batch) >= 32:
                        logical_bytes += _write_arrow_batch(writer, batch, arrow_schema)
            if reference is not None:
                reference_episodes.extend(reference.finish())
        if batch:
            logical_bytes += _write_arrow_batch(writer, batch, arrow_schema)
    if not total:
        raise ValueError("HOLOBRAIN_NO_VALID_FRAMES")
    files = []
    for index, path in enumerate(shards):
        name = f"data-{index:05d}-of-{len(shards):05d}.arrow"
        path.rename(root / name)
        files.append({"filename": name})
    create_database(root / "meta_db.duckdb", f"hc_tj_{config_name}", tasks, episodes)
    info = {"features": FEATURES, "builder_name": "hc_tj", "config_name": config_name, "version": {"version_str": "0.1.0", "major": 0, "minor": 1, "patch": 0},
            "dataset_size": logical_bytes, "size_in_bytes": logical_bytes, "download_size": 0,
            "splits": {"train": {"name": "train", "num_bytes": logical_bytes, "num_examples": total,
                                  "shard_lengths": [total] if single_arrow else [e["frames"] for e in episodes], "dataset_name": "hc_tj"}}}
    if reference_report:
        info.update(description="", citation="", homepage="", license="", dataset_name="hc_tj")
    conversion = {"target_profile": profile, "dataset_format_version": "0.1.0", "sources": reports,
                  "frame_policy": frame_policy,
                  "tasks": tasks, "episode_frame_counts": [e["frames"] for e in episodes],
                  "frames": total, "episodes": len(episodes), "hand_proxy_values": total * 2,
                  "camera_mode": "legacy_reference", "unverified_geometry": ["static zero-head mechanical pose", "depth represented with RGB intrinsics; registration unverified"],
                  "sync": {"anchor": "RGB", "method": "nearest_earlier_tie", "state_action_ms": 20 if legacy_sync else JOINT_MATCH_MS, "depth_ms": 50, "hand_ms": 150},
                  "timestamp_min_max": "RGB/depth only", "originals_included": False}
    state = {"_data_files": files, "_fingerprint": digest(conversion), "_format_columns": None,
             "_format_kwargs": {}, "_format_type": None, "_output_all_columns": False, "_split": "train",
             "robo_orchard_state": {"dataset_format_version": "0.1.0"}}
    if reference_report:
        conversion = {"episode_count": len(reference_episodes), "episodes": reference_episodes,
                      "total_frames": total}
    for name, body in (("dataset_info.json", info), ("state.json", state), ("conversion_report.json", conversion)):
        (root / name).write_bytes(canonical(body))
    from .holobrain_validation import validate
    validation = validate(root, checkpoint=checkpoint)
    (root / "validation_report.json").write_bytes(canonical(validation))
    return inventory(root, episodes, f"hc-holobrain-collection/{2 if compact_invalid else 1}")


def write_collection(root, selections, *, storage, modalities, checkpoint=lambda: None,
                     compact_invalid=True, reference_report=False, split_annotations=True, config_name="16dof",
                     use_committed_actions=False, legacy_sync=False, single_arrow=True):
    if modalities != "rgbd":
        raise ValueError("HOLOBRAIN_REQUIRES_RGBD")
    from .episode_outcome import selection_outcome
    from .native_sources import export_source
    from .selected_cache import materialize_selected

    def sources():
        for selected in selections:
            with TemporaryDirectory(dir=Path(root).parent) as temporary:
                outcome = selection_outcome(selected)
                selected_source(selected.package, holobrain=True)
                file, provenance, audit = export_source(selected.package, storage, checkpoint)
                path = Path(temporary) / "selected.mcap"
                materialize_selected(storage, file, provenance, path, checkpoint)
                if audit is None:
                    raise ValueError("native normalized capture has no audit")
                audit_path = Path(temporary) / "audit.json"
                copy_original(storage, audit, audit_path, checkpoint)
                identity = {**selected.package.receipt.data.model_dump(mode="json"), **outcome}
                if selected.annotation_file is not None:
                    identity.update(
                        annotation_revision=selected.annotation_file.file.revision,
                        annotation_file_sha256=selected.annotation_file.file.content_sha256,
                        subtask_track=selected.track.model_dump(mode="json"),
                    )
                frames = frame_selection(
                    selected, compact_invalid=compact_invalid, split_annotations=split_annotations
                )
                yield {
                    **outcome,
                    "path": path,
                    "audit_path": audit_path,
                    "provenance": provenance,
                    "frames": frames,
                    "action_labels": committed_action_labels(selected.package, frames)
                    if use_committed_actions else {},
                    "identity": identity,
                }

    with closing(sources()) as prepared:
        return write_dataset(root, prepared, checkpoint=checkpoint, compact_invalid=compact_invalid,
                             reference_report=reference_report, config_name=config_name, legacy_sync=legacy_sync,
                             single_arrow=single_arrow)
