"""Lossless, episode-owned native messages shared by MCAP and HoloBrain exports."""

import copy
import hashlib
import json
from collections import Counter
from pathlib import Path

from mcap.reader import make_reader
from mcap.writer import Writer

from .models import FileEntry, canonical

MCAP = "hc/recording/native.mcap"
AUDIT = "hc/recording/native-normalization.json"
MANIFEST = "hc/recording/native.json"
VERSION = "episode-native-capture/1"


def file_entry(root, path):
    h = hashlib.sha256()
    with (root / path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            h.update(block)
    return FileEntry(
        path=path,
        size=(root / path).stat().st_size,
        sha256=h.hexdigest(),
        media_type="application/mcap" if path.endswith(".mcap") else "application/json",
    )


def build_native(
    root, source, provenance, start_ns, end_ns, *, audit=None, checkpoint=lambda: None
):
    from hc_data_platform.continuous_recordings.joint_normalization import capture_time
    from hc_data_platform.continuous_recordings.mcap_contracts import McapProcessingConfig
    from hc_data_platform.continuous_recordings.mcap_native import decoded
    from hc_data_platform.continuous_recordings.mcap_selection import selected_topics
    from hc_data_platform.verification.ports import McapRos2DecoderProbe

    if end_ns <= start_ns:
        raise ValueError("native capture requires a nonempty episode window")
    root = Path(root)
    target = root / MCAP
    target.parent.mkdir(parents=True, exist_ok=True)
    cfg = McapProcessingConfig.model_validate(provenance["config"])
    parameters, allowed = set(cfg.metadata_sources.values()), selected_topics(cfg)
    cameras = {camera.topic for camera in cfg.cameras if camera.kind == "rgb"}
    primary = cfg.primary_camera or cfg.state_topic
    decoder, carry, counts, references = McapRos2DecoderProbe(), {}, Counter(), set()
    with Path(source).open("rb") as incoming, target.open("xb") as outgoing:
        writer = Writer(outgoing, enable_crcs=True, enable_data_crcs=True)
        writer.start(profile="ros2", library=VERSION)
        schemas, channels = {}, {}

        def emit(s, c, m):
            if s is None:
                raise ValueError("native message has no schema")
            if s.id not in schemas:
                schemas[s.id] = writer.register_schema(s.name, s.encoding, s.data)
            if c.id not in channels:
                channels[c.id] = writer.register_channel(
                    c.topic, c.message_encoding, schemas[s.id], c.metadata
                )
            writer.add_message(channels[c.id], m.log_time, m.data, m.publish_time, m.sequence)
            counts[c.topic] += 1
            references.add((c.topic, m.log_time, m.sequence))

        for s, c, m in make_reader(incoming, validate_crcs=True).iter_messages():
            checkpoint()
            if c.topic not in allowed:
                continue
            stamp = (
                capture_time(decoded(s, c, m, decoder), m)
                if cfg.normalization and c.topic not in parameters
                else m.log_time
            )
            if c.topic in parameters:
                if stamp < start_ns:
                    carry[c.topic] = (s, c, m)
                elif stamp < end_ns:
                    emit(s, c, m)
                continue
            tolerance = 0 if c.topic in cameras or c.topic == primary else 150_000_000
            if start_ns - tolerance <= stamp < end_ns + tolerance:
                emit(s, c, m)
        for s, c, m in carry.values():
            emit(s, c, m)
        writer.finish()
    if not counts[primary]:
        raise ValueError("native capture contains no episode frames")
    entry = file_entry(root, MCAP)
    result = copy.deepcopy(provenance)
    parent = result["selected_mcap"]
    result["selected_mcap"] = {
        **parent,
        "path": MCAP,
        "sha256": entry.sha256,
        "size_bytes": entry.size,
        "message_counts": dict(counts),
        "retention": "episode-owned/v1",
        "parent_selected_sha256": parent["sha256"],
    }
    result["selected_mcap"].pop("object_key", None)
    entries = [entry]
    if parent.get("normalization"):
        if audit is None:
            raise ValueError("native normalized capture requires its audit")
        body = Path(audit).read_bytes()
        if hashlib.sha256(body).hexdigest() != parent["normalization"]["audit"]["sha256"]:
            raise ValueError("native capture normalization audit mismatch")
        document = json.loads(body)
        document["messages"] = [
            row
            for row in document["messages"]
            if (
                row["topic"],
                row.get("action", row.get("message"))["log_time_ns"],
                row.get("action", row.get("message"))["sequence"],
            )
            in references
        ]
        document["summary_scope"] = (
            "parent normalization; messages restricted to episode and boundary context"
        )
        (root / AUDIT).write_bytes(canonical(document))
        audit_entry = file_entry(root, AUDIT)
        result["selected_mcap"]["normalization"]["audit"] = {
            "path": AUDIT,
            "sha256": audit_entry.sha256,
        }
        entries.append(audit_entry)
    envelope = {
        "format": VERSION,
        "start_ns": start_ns,
        "end_ns": end_ns,
        "provenance": result,
        "files": [e.model_dump(mode="json") for e in entries],
    }
    (root / MANIFEST).write_bytes(canonical(envelope))
    return tuple([*entries, file_entry(root, MANIFEST)])
