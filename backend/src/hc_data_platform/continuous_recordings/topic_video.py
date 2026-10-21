"""Decode timestamped RGB references in HC sensor-MCAP + MP4 capture bundles."""

import base64
import io
import json
from contextlib import ExitStack
from pathlib import Path, PurePosixPath

import av
from mcap.reader import make_reader
from mcap.writer import Writer
from PIL import Image

VERSION = "hc-topic-video/v1"


def restore(root, destination, progress=lambda: None):
    """Temporary decoding projection only; immutable uploaded files are never changed."""
    root, destination = Path(root), Path(destination)
    session = json.loads((root / "session.json").read_text())
    if session.get("schema_version") != VERSION:
        raise ValueError("unsupported topic video bundle")
    declarations = {entry["path"]: entry["frame_count"] for entry in session["videos"]}
    if not declarations or len(declarations) != len(session["videos"]):
        raise ValueError("invalid video inventory")
    counts, readers, channels, schemas = {}, {}, {}, {}
    with ExitStack() as stack:
        source = stack.enter_context((root / "raw.mcap").open("rb"))
        output = stack.enter_context(destination.open("wb"))
        reader, writer = make_reader(source, validate_crcs=True), Writer(output)
        writer.start(profile="ros2")
        for metadata in reader.iter_metadata():
            writer.add_metadata(metadata.name, metadata.metadata)
        for schema, channel, message in reader.iter_messages(log_time_order=False):
            progress()
            payload, encoding = message.data, channel.message_encoding
            if encoding == VERSION:
                binding = json.loads(payload)
                relative = binding["video_path"]
                path = PurePosixPath(relative)
                if (
                    relative not in declarations
                    or path.is_absolute()
                    or ".." in path.parts
                    or "\\" in relative
                    or path.as_posix() != relative
                    or not (root / relative).resolve().is_relative_to(root.resolve())
                ):
                    raise ValueError("video reference escapes committed bundle")
                if relative not in readers:
                    container = stack.enter_context(av.open(str(root / relative)))
                    if len(container.streams.video) != 1:
                        raise ValueError("video must contain exactly one image stream")
                    readers[relative], counts[relative] = iter(container.decode(video=0)), 0
                if (
                    type(binding["frame_index"]) is not int
                    or binding["frame_index"] != counts[relative]
                ):
                    raise ValueError("video bindings must preserve every source frame in order")
                frame = next(readers[relative], None)
                if frame is None or (frame.height, frame.width) != (
                    binding["height"],
                    binding["width"],
                ):
                    raise ValueError("video frame missing or dimensions changed")
                if frame.width * frame.height > 16 * 1024 * 1024:
                    raise ValueError("video dimensions exceed decode limit")
                buffer = io.BytesIO()
                Image.fromarray(frame.to_ndarray(format="rgb24")).save(buffer, format="PNG")
                payload = json.dumps(
                    {
                        "encoding": "png",
                        "width": frame.width,
                        "height": frame.height,
                        "header": binding.get("header"),
                        "data_base64": base64.b64encode(buffer.getvalue()).decode(),
                    }
                ).encode()
                encoding = "json"
                counts[relative] += 1
            if channel.id not in channels:
                if schema and schema.id not in schemas:
                    schemas[schema.id] = writer.register_schema(
                        schema.name, schema.encoding, schema.data
                    )
                channels[channel.id] = writer.register_channel(
                    channel.topic, encoding, schemas.get(channel.schema_id, 0), channel.metadata
                )
            writer.add_message(
                channels[channel.id],
                message.log_time,
                payload,
                message.publish_time,
                message.sequence,
            )
        if counts != declarations or any(
            next(reader, None) is not None for reader in readers.values()
        ):
            raise ValueError("video inventory differs from frame bindings")
        writer.finish()
    return destination


def prepare(root, destination, command, snapshot, progress=lambda: None):
    from hc_data_platform.capture_schemes.models import OutputContract
    from hc_data_platform.capture_schemes.processing import require_snapshot, validate_mcap_output

    from .mcap_native import McapProcessingConfig
    from .mcap_native import prepare as prepare_mcap

    snapshot = require_snapshot(
        snapshot or {"error": "CAPTURE_PLAN_REQUIRED", "detail": "原始包缺少冻结采集方案"}
    )
    session = json.loads((root / "session.json").read_text())
    if session.get("capture_snapshot") and session["capture_snapshot"] != snapshot:
        raise ValueError("CAPTURE_SNAPSHOT_MISMATCH: original bundle has a different frozen plan")
    source = restore(root, root.parent / "decoded-topic-video.mcap", progress)
    cfg = McapProcessingConfig.model_validate(snapshot["effective_adapter"]["configuration"])
    config, evidence = prepare_mcap(source, destination, cfg, progress)
    validation = validate_mcap_output(
        OutputContract.model_validate(snapshot["output"]), cfg, evidence
    )
    evidence.update(capture_snapshot=snapshot, capture_validation=validation, source_format=VERSION)
    (destination / "mcap-provenance.json").write_text(json.dumps(evidence))
    camera_files = {"videos/" + c["camera_id"] + ".mp4": c["camera_id"] for c in config["cameras"]}
    roles = {}
    for path in destination.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(destination).as_posix()
        role = (
            "RAW_VIDEO"
            if relative in camera_files
            else "SENSOR_DATA"
            if relative == "sensors/robot.mcap"
            else "RECORDING_CONFIG"
            if relative == "recording-config.json"
            else "AUXILIARY"
        )
        roles[relative] = (
            role,
            camera_files.get(relative),
            "video/mp4" if path.suffix == ".mp4" else "application/octet-stream",
        )
    return config, roles
