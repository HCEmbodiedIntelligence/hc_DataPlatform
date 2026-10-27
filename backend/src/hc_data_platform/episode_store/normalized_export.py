"""Frozen selected sources and real parent-frame selection for training writers."""

import json
from pathlib import Path

import pyarrow.parquet as pq

from hc_data_platform.data_formats.lerobot_layout import DATA_PATH

from .native_plan import NativeFile


def selected_source(package, *, holobrain=False):
    source = package.document("hc/source.json")
    provenance = source.get("mcap", {})
    selection = provenance.get("selected_mcap", {})
    entries = source.get("inventory", [])
    found = [e for e in entries if e.get("relative_path") == selection.get("path")]
    if len(found) != 1 or found[0]["object"]["sha256"] != selection.get("sha256"):
        from .delivery_errors import failure
        raise failure("MCAP_SELECTION_REQUIRED", "请按所选方案重处理原件后再导出。", 422)
    if holobrain:
        cfg = provenance.get("config", {})
        fields = (cfg.get("semantic_output") or {}).get("features", {})
        missing = {"observation.state", "action", "action.hand.right", "action.hand.left",
                   "observation.images.head", "observation.images.head_depth"} - set(fields)
        missing |= {"head.calibration", "head.extrinsics"} - set(cfg.get("metadata_sources", {}))
        if missing or selection.get("format") != "hc-selected-mcap/2":
            raise ValueError(f"HOLOBRAIN_DEPENDENCIES_REQUIRED: {sorted(missing)}; requires normalized 17-axis source")
        if cfg.get("normalization", {}).get("camera_mode") != "legacy_reference":
            raise ValueError("HOLOBRAIN_CALIBRATED_EVIDENCE_REQUIRED: dynamic camera pose and RGB-depth registration are unverified")
    return NativeFile.model_validate(found[0]), provenance


def frame_selection(selection, *, compact_invalid=False):
    """Select source frames, optionally keeping a task across invalid samples.

    Parent indices and acquisition times always remain original. Training writers
    assign dense output indices; exclusions and task changes still break segments.
    Raw MCAP windows retain the historical contiguous-source selection by default.
    """
    package = selection.package
    with package.file(DATA_PATH) as path:
        table = pq.read_table(path)
    if "source.timestamp_ns" in table.column_names:
        stamps = table["source.timestamp_ns"].to_pylist()
        stamps = [s[0] if isinstance(s, list) else s for s in stamps]
    else:
        stamps = table["hc.timestamp_ns"].to_pylist()
    valid = table["hc.sample_valid"].to_pylist() if "hc.sample_valid" in table.column_names else [True] * len(stamps)
    available_stamps = [t for t, ok in zip(stamps, valid, strict=True) if ok]
    if any(type(t) is not int for t in available_stamps) or any(a >= b for a, b in zip(available_stamps, available_stamps[1:])):
        raise ValueError("EXPORT_SOURCE_FRAME_MAP_REQUIRED: acquisition timestamps must increase")
    with package.file("meta/tasks.parquet") as path:
        tasks = {r["task_index"]: r["task"] for r in pq.read_table(path).to_pylist()}
    task_indexes = table["task_index"].to_pylist()
    labels = {}
    excluded = {i for i, value in enumerate(table["hc.annotation.excluded"].to_pylist()) if value} if "hc.annotation.excluded" in table.column_names else set()
    if selection.annotation_file is not None:
        import hashlib

        from hc_data_platform.annotation.subtasks import SubtaskProjection

        if hashlib.sha256(selection.annotation_file.content).hexdigest() != selection.annotation_file.file.content_sha256:
            raise ValueError("EXPORT_ANNOTATION_HASH_MISMATCH")
        annotation = json.loads(selection.annotation_file.content)
        projection = SubtaskProjection.freeze(annotation, package.receipt.data,
            selection.annotation_file.file.content_sha256, selection.track)
        for segment in projection.segments:
            for index in range(segment.start_step, segment.end_step):
                labels[index] = segment.label or segment.tag_id
        for span in annotation["effective_exclusions"]:
            if not 0 <= span["start_step"] < span["end_step"] <= len(stamps):
                raise ValueError("annotation exclusion outside parent")
            excluded.update(range(span["start_step"], span["end_step"]))
    result, segment_id, last = {}, -1, None
    source = package.document("hc/source.json")
    end = int(source.get("mcap", {}).get("source_origin_ns", 0)) + int(source.get("end_offset_ns", 0))
    for index, stamp in enumerate(stamps):
        if index in excluded:
            last = None
            continue
        task = labels.get(index, tasks[task_indexes[index]])
        if not valid[index]:
            # Bridge only unavailable samples of the same task. A task change
            # inside a discarded run must not join otherwise separate episodes.
            last = (task, index) if compact_invalid and last == (task, index - 1) else None
            continue
        if not task or task == "unknown":
            raise ValueError("EXPORT_TASK_TEXT_REQUIRED: missing real task instruction")
        key = (task, index - 1)
        if last != key:
            segment_id += 1
        next_stamp = stamps[index + 1] if index + 1 < len(stamps) else end
        result[stamp] = {"parent_frame_index": index, "task": task, "segment": segment_id,
                         "end_ns": next_stamp if type(next_stamp) is int and next_stamp > stamp else stamp + 1}
        last = (task, index)
    return result


def windows(selection, *, compact_invalid=False):
    rows = frame_selection(selection, compact_invalid=compact_invalid)
    ordered = sorted(rows.items())
    spans = []
    for stamp, row in ordered:
        next_stamp = row["end_ns"]
        if spans and spans[-1]["segment"] == row["segment"]:
            spans[-1]["end_ns"] = next_stamp
        else:
            spans.append({**row, "start_ns": stamp, "end_ns": next_stamp})
    return spans


def write_mcap_collection(root, selections, *, storage, checkpoint=lambda: None,
                          compact_invalid=True):
    from tempfile import TemporaryDirectory

    from mcap.reader import make_reader
    from mcap.writer import Writer

    from hc_data_platform.continuous_recordings.joint_normalization import capture_time
    from hc_data_platform.continuous_recordings.mcap_contracts import McapProcessingConfig
    from hc_data_platform.continuous_recordings.mcap_native import decoded
    from hc_data_platform.continuous_recordings.mcap_selection import selected_topics
    from hc_data_platform.verification.ports import McapRos2DecoderProbe

    from .models import canonical
    from .original_export import copy_original, inventory

    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    members = []
    with TemporaryDirectory(dir=root.parent) as temporary:
        for selected in selections:
            file, provenance = selected_source(selected.package)
            cfg = McapProcessingConfig.model_validate(provenance["config"])
            local = Path(temporary) / "selected.mcap"
            if local.exists():
                local.unlink()
            copy_original(storage, file, local, checkpoint)
            audit = None
            if provenance["selected_mcap"].get("normalization"):
                entries = selected.package.document("hc/source.json")["inventory"]
                ref = next(e for e in entries if e["relative_path"] == "source/normalization.json")
                audit_path = Path(temporary) / "audit.json"
                if audit_path.exists():
                    audit_path.unlink()
                copy_original(storage, NativeFile.model_validate(ref), audit_path, checkpoint)
                audit = json.loads(audit_path.read_bytes())
            kept = frame_selection(selected, compact_invalid=compact_invalid)
            for span in windows(selected, compact_invalid=compact_invalid):
                target = root / f"episode-{len(members):06d}.mcap"
                decoder = McapRos2DecoderProbe()
                allowed = selected_topics(cfg)
                parameter_topics = set(cfg.metadata_sources.values())
                carry = {}
                counts = {}
                edge_before, edge_after, context_messages = {}, {}, []
                with local.open("rb") as incoming, target.open("wb") as outgoing:
                    reader = make_reader(incoming, validate_crcs=True)
                    writer = Writer(outgoing, enable_crcs=True, enable_data_crcs=True)
                    writer.start(profile="ros2", library="hc-selected-export/2")
                    schemas, channels = {}, {}

                    def emit(schema, channel, message):
                        if schema.id not in schemas:
                            schemas[schema.id] = writer.register_schema(schema.name, schema.encoding, schema.data)
                        if channel.id not in channels:
                            channels[channel.id] = writer.register_channel(channel.topic, channel.message_encoding, schemas[schema.id])
                        writer.add_message(channels[channel.id], message.log_time, message.data, message.publish_time, message.sequence)
                        counts[channel.topic] = counts.get(channel.topic, 0) + 1

                    for schema, channel, message in reader.iter_messages():
                        checkpoint()
                        if channel.topic not in allowed:
                            continue
                        stamp = (capture_time(decoded(schema, channel, message, decoder), message)
                                 if cfg.normalization and channel.topic not in parameter_topics else message.log_time)
                        if channel.topic in parameter_topics and stamp < span["start_ns"]:
                            carry[channel.topic] = (schema, channel, message)
                        if (span["start_ns"] <= stamp < span["end_ns"]
                                and (not compact_invalid or channel.topic != (cfg.primary_camera or cfg.state_topic) or stamp in kept)):
                            emit(schema, channel, message)
                        elif cfg.normalization and channel.topic not in parameter_topics and channel.topic != (cfg.primary_camera or cfg.state_topic):
                            tolerance = 150_000_000 if "/hand/" in channel.topic or "_hand/" in channel.topic else 50_000_000 if "/depth/" in channel.topic else 20_000_000
                            if span["start_ns"] - tolerance <= stamp < span["start_ns"]:
                                prior = edge_before.get(channel.topic)
                                if prior is None or stamp > prior[0]:
                                    edge_before[channel.topic] = (stamp, schema, channel, message)
                            elif span["end_ns"] <= stamp <= span["end_ns"] + tolerance:
                                prior = edge_after.get(channel.topic)
                                if prior is None or stamp < prior[0]:
                                    edge_after[channel.topic] = (stamp, schema, channel, message)
                    for value in carry.values():
                        emit(*value)
                    for stamp, schema, channel, message in (*edge_before.values(), *edge_after.values()):
                        emit(schema, channel, message)
                        context_messages.append({"topic": channel.topic, "capture_time_ns": stamp,
                            "log_time_ns": message.log_time, "sequence": message.sequence})
                    document = {"format": "hc-mcap-selection/2", "span": span,
                                "data": selected.package.receipt.data.model_dump(mode="json"),
                                "output": cfg.semantic_output, "parser": provenance["format"],
                                "source_sha256": provenance["selected_mcap"]["source_sha256"],
                                "selected_sha256": file.object.sha256,
                                "annotation_revision": selected.annotation_file.file.revision if selected.annotation_file else None}
                    document["alignment_context"] = context_messages
                    writer.add_metadata("hc.capture.selection", {"document": canonical(document).decode()})
                    writer.finish()
                if audit:
                    subset = {**audit, "messages": [r for r in audit["messages"]
                        if span["start_ns"] <= r.get("action", r.get("message"))["capture_time_ns"] < span["end_ns"]
                        or any(r["topic"] == c["topic"] and r.get("action", r.get("message"))["log_time_ns"] == c["log_time_ns"]
                               and r.get("action", r.get("message"))["sequence"] == c["sequence"] for c in context_messages)]}
                    subset["summary_scope"] = "complete_parent_normalization; messages filtered to exported interval"
                    subset["export_span"] = span
                    (root / (target.stem + "-normalization.json")).write_bytes(canonical(subset))
                if compact_invalid:
                    frame_map = [{"frame_index": i, "parent_frame_index": row["parent_frame_index"],
                                  "source_timestamp_ns": stamp}
                                 for i, (stamp, row) in enumerate(
                                     (item for item in kept.items() if item[1]["segment"] == span["segment"]))]
                    (root / (target.stem + "-frames.json")).write_bytes(canonical(frame_map))
                members.append({**document, "path": target.name, "message_counts": counts,
                                "topic_policy": "frozen_scheme_whitelist_normalized"})
    if not members:
        raise ValueError("MCAP_NO_SELECTED_FRAMES")
    return inventory(root, members, f"hc-mcap-collection/{3 if compact_invalid else 2}")
