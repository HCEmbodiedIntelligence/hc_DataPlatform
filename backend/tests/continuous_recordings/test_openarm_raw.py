import hashlib
import json
import shutil
import struct
from pathlib import Path

import av
import numpy as np
import pyarrow.parquet as pq
import pytest
from mcap.reader import make_reader
from mcap.writer import Writer

from hc_data_platform.continuous_recordings.openarm_raw.prepare import load_index, prepare, rows
from hc_data_platform.robot_ingest.recording_contract import OpenArmRecordingComplete

FIXTURE = Path(__file__).parents[1] / "fixtures/openarm-raw-v1"


def test_unpaired_original_archive_is_accepted_without_becoming_training_frames(tmp_path):
    from hc_data_platform.continuous_recordings.openarm_raw.config import config_hash, validate
    from hc_data_platform.ingest.ports import crc64_ecma

    root = tmp_path / "input"
    declared_synthetic_fixture(root, exposure="measured")
    archive = root / "originals.mcap"
    with archive.open("wb") as stream:
        writer = Writer(stream)
        writer.start(profile="ros2")
        # A deliberately unpaired raw evidence stream has no normalized sample
        # or preview binding. It must not be mistaken for a training frame.
        channel = writer.register_channel("/camera_left/camera/color/metadata", "json", 0)
        writer.add_message(channel, 100, b'{"frame_number":99999}', 100, 0)
        writer.finish()
    payload = archive.read_bytes()
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["committed"].append({
        "path": "originals.mcap", "kind": "original_ros", "closed": True, "fsynced": True,
    })
    (root / "manifest.json").write_text(json.dumps(manifest))
    snapshot = json.loads((root / "session.json").read_text())
    snapshot["config"]["sources"]["camera_left"]["original_topics"] = {
        "rgb": "/camera_left/camera/color/image_raw",
        "depth": "/camera_left/camera/depth/image_rect_raw",
        "rgb_meta": "/camera_left/camera/color/metadata",
        "depth_meta": "/camera_left/camera/depth/metadata",
        "rgb_info": "/camera_left/camera/color/camera_info",
        "depth_info": "/camera_left/camera/depth/camera_info",
    }
    snapshot["config"] = validate(snapshot["config"])
    snapshot["config_hash"] = config_hash(snapshot["config"])
    (root / "session.json").write_text(json.dumps(snapshot))
    marker = json.loads((root / "recording-complete.json").read_text())
    marker["recording_upload"]["assets"].append({
        "path": "originals.mcap", "role": "AUXILIARY", "camera_id": None,
        "media_type": "application/mcap", "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(), "crc64": crc64_ecma(payload),
        "part_count": 1,
    })
    seal = OpenArmRecordingComplete.model_validate(marker)
    destination = tmp_path / "prepared"
    config, _ = prepare(root, destination, seal.recording_upload)
    assert len(config["cameras"]) == 6
    assert archive.read_bytes() == payload
    for line in (destination / "source/frames.jsonl").read_text().splitlines():
        for image in json.loads(line)["row"]["images"].values():
            assert image["rgb"].get("source_seq") != 99999


def test_mixed_rgbd_and_rgb_only_prepares_five_real_video_streams(tmp_path):
    from hc_data_platform.continuous_recordings.openarm_raw.config import config_hash, validate

    root = tmp_path / "input"
    declared_synthetic_fixture(root, exposure="measured")
    snapshot = json.loads((root / "session.json").read_text())
    snapshot["config"]["sources"]["camera_hand"].update(depth_enabled=False, transport="envelope")
    snapshot["config"] = validate(snapshot["config"])
    snapshot["config_hash"] = config_hash(snapshot["config"])
    (root / "session.json").write_text(json.dumps(snapshot))
    old = root / "raw-before.mcap"
    (root / "raw.mcap").rename(old)
    with old.open("rb") as stream, (root / "raw.mcap").open("wb") as output:
        writer = Writer(output)
        writer.start()
        channels = {}
        for _, channel, message in make_reader(stream, validate_crcs=True).iter_messages(
            log_time_order=False
        ):
            if channel.message_encoding == "humanoid-depth" and channel.topic.endswith(
                "/camera_hand"
            ):
                continue
            if channel.id not in channels:
                channels[channel.id] = writer.register_channel(
                    channel.topic, channel.message_encoding, 0
                )
            payload = message.data
            if channel.message_encoding == "json" and channel.topic.endswith("/camera_hand"):
                data = json.loads(payload)
                data.pop("depth", None)
                payload = json.dumps(data).encode()
            writer.add_message(
                channels[channel.id],
                message.log_time,
                payload,
                message.publish_time,
                message.sequence,
            )
        writer.finish()
    seal = OpenArmRecordingComplete.model_validate_json(
        (root / "recording-complete.json").read_bytes()
    )
    destination = tmp_path / "prepared"
    config, roles = prepare(root, destination, seal.recording_upload)
    assert {c["camera_id"] for c in config["cameras"]} == {
        "camera_left",
        "camera_left_depth",
        "camera_right",
        "camera_right_depth",
        "head",
    }
    assert "videos/camera_hand_depth.mp4" not in roles
    assert not (destination / "videos/camera_hand_depth.mp4").exists()
    # Exercise the package consumer as well as the raw producer. A version
    # mismatch here used to pass QC but fail every valid slice at alignment.
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from hc_data_platform.continuous_recordings.asset_models import (
        RecordingAssetRole, RecordingConfigurationV1,
    )
    from hc_data_platform.continuous_recordings.package import source_context

    assets = [SimpleNamespace(relative_path=path, role=RecordingAssetRole(role),
                              camera_id=camera) for path, (role, camera, _) in roles.items()]
    assets.append(SimpleNamespace(relative_path="source/original/manifest.json",
        role=RecordingAssetRole.AUXILIARY, camera_id=None,
        content_sha256=hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()))
    request = SimpleNamespace(recording_config=RecordingConfigurationV1.model_validate(config),
                              assets=assets)
    upload = SimpleNamespace(command=SimpleNamespace(capture_started_at=datetime.now(timezone.utc)))
    origin, _, media = source_context(request, upload, {path: destination / path for path in roles})
    assert origin == json.loads((root / "manifest.json").read_text())["t0_ns"]
    assert set(media) == {"camera_left_depth", "camera_right_depth"}
    assert {item["kind"] for item in media.values()} == {"depth"}
    rows = [
        json.loads(line) for line in (destination / "source/frames.jsonl").read_text().splitlines()
    ]
    assert any(row["valid"] for row in rows)
    for row in rows:
        image = row["row"]["images"].get("camera_hand")
        if image:
            assert "depth" not in image
            assert set(image["state_at_exposure"]) == {"rgb"}


def test_retained_original_rgb_does_not_replace_preview_frame_bindings(tmp_path):
    import sqlite3

    shutil.copytree(FIXTURE, tmp_path, dirs_exist_ok=True)
    with (
        (FIXTURE / "raw.mcap").open("rb") as original,
        (tmp_path / "raw.mcap").open("wb") as output,
    ):
        writer = Writer(output)
        writer.start()
        channels = {}
        for _, channel, message in make_reader(original).iter_messages(log_time_order=False):
            if channel.id not in channels:
                channels[channel.id] = writer.register_channel(
                    channel.topic, channel.message_encoding, 0
                )
            writer.add_message(
                channels[channel.id],
                message.log_time,
                message.data,
                message.publish_time,
                message.sequence,
            )
        rgb = writer.register_channel("/humanoid/session/rgb/camera_left", "humanoid-rgb", 0)
        metadata = json.dumps({"rgb": {"shape": [1, 1, 3]}}).encode()
        writer.add_message(
            rgb, 1, struct.pack("<I", len(metadata)) + metadata + b"\xff\x00\x80", publish_time=1
        )
        writer.finish()
    with sqlite3.connect(":memory:") as before, sqlite3.connect(":memory:") as after:
        load_index(FIXTURE, before)
        load_index(tmp_path, after)
        for table in ("raw_samples", "frames", "depths", "pointclouds", "events"):
            assert (
                before.execute(f"SELECT * FROM {table}").fetchall()
                == after.execute(f"SELECT * FROM {table}").fetchall()
            )


def test_robot_config_profile_accepts_new_layout_without_changing_legacy_profile():
    from copy import deepcopy

    from hc_data_platform.continuous_recordings.openarm_raw.profile import validate_capture_config

    cfg = json.loads((FIXTURE / "session.json").read_text())["config"]
    cfg["capture"]["profile"]["profile_version"] = "robot-config-v1"
    cfg["capture"]["profile"]["cameras"] = [cfg["capture"]["profile"]["cameras"][0]]
    cfg["capture"]["profile"]["axes"] = [cfg["capture"]["profile"]["axes"][0]]
    camera_id = cfg["capture"]["profile"]["cameras"][0]["id"]
    axis = cfg["capture"]["profile"]["axes"][0]
    cfg["sources"] = {
        key: value
        for key, value in cfg["sources"].items()
        if key == camera_id or value.get("named_axes", [{}])[0].get("name") == axis["name"]
    }
    for source in cfg["sources"].values():
        if "named_axes" in source:
            source["named_axes"] = source["named_axes"][:1]
    validate_capture_config(cfg)
    legacy = deepcopy(cfg)
    legacy["capture"]["profile"]["profile_version"] = "openarmx-v1"
    with pytest.raises(ValueError, match="frozen"):
        validate_capture_config(legacy)


def declared_synthetic_fixture(root, *, floating=False, exposure=None):
    """Make NEW test input with explicit oracle units, never upgrade real legacy data.

    The historical synthetic input provides numeric/time oracles. Rebuild its
    depth stream at half resolution with declared fixture-only unit evidence.
    This is a software fixture, not a driver, device or end-to-end acceptance.
    """
    from hc_data_platform.ingest.ports import crc64_ecma
    from hc_data_platform.publishing.depth_source import profile_hash

    assert json.loads((FIXTURE / "recording-complete.json").read_text())["synthetic"] is True
    shutil.copytree(FIXTURE, root)
    if exposure is not None:
        from hc_data_platform.continuous_recordings.openarm_raw.config import config_hash, validate

        snapshot = json.loads((root / "session.json").read_text())
        cfg = snapshot["config"]
        cfg["alignment"]["state_reference"] = "primary_exposure_midpoint"
        cfg["alignment"]["camera_timing_reference"] = "per_camera_rgb"
        if exposure == "d435":
            cfg["sources"]["camera_hand"]["rgbd_max_midpoint_skew_ms"] = 2
        for source in cfg["sources"].values():
            if source["kind"] == "rgbd":
                source["require_exposure_metadata"] = True
        snapshot["config"] = validate(cfg)
        snapshot["config_hash"] = config_hash(snapshot["config"])
        (root / "session.json").write_text(json.dumps(snapshot))
    profile = {
        "schema_version": "hc-depth-source/1",
        "encoding": "32FC1" if floating else "16UC1",
        "unit": "m" if floating else "mm",
        "scale_m_per_unit": 1.0 if floating else 0.001,
        "invalid_values": [0],
        "invalid_nonfinite": True,
        "registered_to_rgb": False,
        "registration_evidence": "synthetic independent half-resolution depth grid",
        "evidence": {
            "kind": "synthetic_fixture",
            "reference": __name__ + ".declared_synthetic_fixture",
            "version": "1",
        },
    }
    with (FIXTURE / "raw.mcap").open("rb") as original, (root / "raw.mcap").open("wb") as output:
        writer = Writer(output)
        writer.start(profile="synthetic-r2-depth-source/1")
        schemas, channels = {}, {}
        for schema, channel, message in make_reader(original, validate_crcs=True).iter_messages():
            if schema and schema.id not in schemas:
                schemas[schema.id] = writer.register_schema(
                    schema.name, schema.encoding, schema.data
                )
            if channel.id not in channels:
                channels[channel.id] = writer.register_channel(
                    channel.topic,
                    channel.message_encoding,
                    schemas.get(channel.schema_id, 0),
                    channel.metadata,
                )
            payload = message.data
            if channel.message_encoding == "humanoid-depth":
                size = struct.unpack("<I", payload[:4])[0]
                data = json.loads(payload[4 : 4 + size])
                depth = data["depth"]
                pixels = np.frombuffer(payload[4 + size :], dtype=depth["dtype"]).reshape(
                    depth["shape"]
                )[::2, ::2]
                if floating:
                    pixels = pixels.astype("<f4") * 0.001
            elif channel.message_encoding == "json":
                data = json.loads(payload)
                pixels = None
            elif channel.message_encoding == "humanoid-pointcloud":
                size = struct.unpack("<I", payload[:4])[0]
                data = json.loads(payload[4 : 4 + size])
                pixels = None
            else:
                data, pixels = {}, None
            if "depth" in data:
                if exposure is not None and "rgb" in data:
                    for name in ("rgb", "depth"):
                        image = data[name]
                        stamp = image["capture_time_ns"] + 500_000
                        if (
                            exposure == "d435"
                            and data["source_id"] == "camera_hand"
                            and name == "depth"
                        ):
                            stamp -= 1_949_000
                        image.update(
                            capture_time_ns=stamp,
                            header_timestamp_ns=stamp,
                            exposure_midpoint_ns=stamp,
                            timestamp_semantics="exposure_midpoint",
                            actual_exposure_us=3900,
                            exposure_evidence="synthetic per-frame exposure fixture",
                            timestamp_evidence="synthetic exposure midpoint fixture",
                        )
                        if exposure == "missing":
                            image.pop("timestamp_evidence")
                    data.update(
                        capture_time_ns=data["rgb"]["capture_time_ns"],
                        header_timestamp_ns=data["rgb"]["capture_time_ns"],
                    )
                depth = data["depth"]
                depth.update(
                    source_profile=profile,
                    source_profile_sha256=profile_hash(profile),
                    depth_scale=profile["scale_m_per_unit"],
                    encoding=profile["encoding"],
                    invalid_value=0,
                )
                depth["shape"] = (
                    list(pixels.shape) if pixels is not None else [v // 2 for v in depth["shape"]]
                )
                depth["dtype"] = "<f4" if floating else "<u2"
                encoded = json.dumps(data).encode()
                payload = (
                    struct.pack("<I", len(encoded)) + encoded + pixels.tobytes()
                    if pixels is not None
                    else encoded
                )
            if exposure is not None and "camera_source_id" in data and "source_depth_seq" in data:
                offset = 500_000 - (
                    1_949_000
                    if exposure == "d435" and data["camera_source_id"] == "camera_hand"
                    else 0
                )
                data.update(
                    capture_time_ns=data["capture_time_ns"] + offset,
                    header_timestamp_ns=data["header_timestamp_ns"] + offset,
                )
                encoded = json.dumps(data).encode()
                payload = (
                    struct.pack("<I", len(encoded)) + encoded + payload[4 + size :]
                    if channel.message_encoding == "humanoid-pointcloud"
                    else encoded
                )
            writer.add_message(
                channels[channel.id],
                message.log_time,
                payload,
                publish_time=message.publish_time,
                sequence=message.sequence,
            )
        writer.finish()
    seal = json.loads((root / "recording-complete.json").read_text())
    for asset in seal["recording_upload"]["assets"]:
        body = (root / asset["path"]).read_bytes()
        asset.update(
            size=len(body), sha256=hashlib.sha256(body).hexdigest(), crc64=crc64_ecma(body)
        )
    seal["content_sha256"] = hashlib.sha256(
        json.dumps(
            seal["recording_upload"]["assets"], sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    (root / "recording-complete.json").write_text(json.dumps(seal))
    return profile


@pytest.mark.parametrize("evidence", ["measured", "missing", "d435"])
def test_exposure_reference_survives_raw_processing_and_missing_evidence_stays_invalid(
    tmp_path, evidence
):
    root, derived = tmp_path / "original", tmp_path / "derived"
    declared_synthetic_fixture(root, exposure=evidence)
    marker = OpenArmRecordingComplete.model_validate_json(
        (root / "recording-complete.json").read_bytes()
    )
    prepare(root, derived, marker.recording_upload)
    traces = [
        json.loads(line)["row"]
        for line in (derived / "source/frames.jsonl").read_text().splitlines()
    ]
    with (derived / "sensors/robot.mcap").open("rb") as stream:
        samples = [(c.topic, json.loads(m.data)) for _, c, m in make_reader(stream).iter_messages()]
    states = [s for t, s in samples if t == "/observation/state"]
    stamps = [s for t, s in samples if t == "source.timestamp_ns"]
    valid = [s for t, s in samples if t == "/openarm/capture_validity"]
    assert len(traces) == len(states) == len(stamps) == 21
    if evidence == "missing":
        assert all(not value["valid"] for value in valid)
        assert all(value is None for value in stamps)
        assert all(all(v is None for v in state["values"]) for state in states)
    else:
        interior = valid[1:-1] if evidence == "d435" else valid[:-1]
        assert all(value["valid"] for value in interior), valid
        if evidence == "d435":
            assert not valid[0]["valid"]  # First depth predates the first joint sample.
            assert all(
                "state_missing_invalid_or_expired" in reason for reason in valid[0]["reasons"]
            )
        for k, (trace, state, stamp) in enumerate(zip(traces, states, stamps, strict=True)):
            assert stamp == trace["association_time_ns"]
            assert trace["grid_to_exposure_ns"] == 500_000
            assert state["values"][0] == pytest.approx(0.8 + (k / 30 + 0.0005) * 0.1, abs=1e-6)
            assert all("state_at_exposure" in group for group in trace["images"].values())
        if evidence == "d435":
            import pyarrow.parquet as pq

            frames = pq.read_table(derived / "hc/depth/camera_hand/frames.parquet").to_pylist()
            assert all(frame["time_error_ns"] == -1_949_000 for frame in frames)
            assert all(
                not frame["time_threshold_exceeded"] and frame["max_time_error_ns"] == 2_000_000
                for frame in frames
            ), frames
            assert all(
                trace["images"]["camera_hand"]["rgbd_limit_ns"] == 2_000_000 for trace in traces
            )


@pytest.mark.parametrize("floating", [False, True])
def test_original_capture_is_indexed_and_aligned_only_on_platform(tmp_path, floating):
    root = tmp_path / "original"
    source_profile = declared_synthetic_fixture(root, floating=floating)
    original_hashes = {
        p.relative_to(root): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }
    marker = OpenArmRecordingComplete.model_validate_json(
        (root / "recording-complete.json").read_bytes()
    )
    config, roles = prepare(root, tmp_path / "derived", marker.recording_upload)
    assert not list(root.rglob("*.sqlite3")) and not (root / "quality.json").exists()
    assert (tmp_path / "worker-index.sqlite3").exists()
    assert len([c for c in config["cameras"] if c["codec"] == "h264"]) == 3
    with (tmp_path / "derived/sensors/robot.mcap").open("rb") as stream:
        samples = [(c.topic, json.loads(m.data)) for _, c, m in make_reader(stream).iter_messages()]
    states = [s for t, s in samples if t == "/observation/state"]
    actions = [s for t, s in samples if t == "/action"]
    validity = [s for t, s in samples if t == "/openarm/capture_validity"]
    assert len(states) == len(actions) == len(validity) == 21
    assert all(v["valid"] for v in validity[:-1]), validity
    # Stop has no post-cutoff tail: the final 20 Hz gripper bracket is absent.
    assert not validity[-1]["valid"]
    assert any("gripper" in reason for reason in validity[-1]["reasons"])
    for k, (state, action) in enumerate(zip(states, actions, strict=False)):
        # Independent synthetic generator oracle: arm states at 100 Hz;
        # actions hold the most recent actually sent source command.
        assert state["names"] == actions[k]["names"]
        assert state["units"] == ["rad"] * 7 + ["m"] + ["rad"] * 7 + ["m"]
        assert state["values"][0] == pytest.approx(0.8 + k / 30 * 0.1, abs=1e-6)
        assert action["values"][0] == pytest.approx(0.85 + (k * 100 // 30) * 0.001, abs=1e-6)
    for file, digest in original_hashes.items():
        assert hashlib.sha256((root / file).read_bytes()).hexdigest() == digest
    for camera in config["cameras"]:
        with av.open(
            str(tmp_path / "derived" / roles_to_video(roles, camera["camera_id"]))
        ) as container:
            stream = container.streams.video[0]
            assert (stream.width, stream.height) == (camera["width"], camera["height"])
        if camera["codec"] == "hevc":
            rgb = next(
                c
                for c in config["cameras"]
                if c["camera_id"] == camera["camera_id"].removesuffix("_depth")
            )
            assert (camera["width"] * 2, camera["height"] * 2) == (rgb["width"], rgb["height"])
    for report_path in (tmp_path / "derived/hc/depth").glob("*/report.json"):
        report = json.loads(report_path.read_text())
        assert report["source_contract_required"] is True
        assert list(report["source_profiles"].values()) == [source_profile]
        assert report["video_encoding"]["lossless_codes"] is True
        frame_rows = pq.read_table(report_path.parent / "frames.parquet").to_pylist()
        assert all(
            r["depth_scale_m_per_unit"] == source_profile["scale_m_per_unit"]
            for r in frame_rows
            if not r["missing"]
        )
        assert all(
            r["source_profile_sha256"] in report["source_profiles"]
            for r in frame_rows
            if not r["missing"]
        )


def roles_to_video(roles, camera_id):
    return next(
        path for path, role in roles.items() if role[0] == "RAW_VIDEO" and role[1] == camera_id
    )


def test_legacy_unknown_source_is_preserved_but_not_standardized(tmp_path):
    root = tmp_path / "original"
    shutil.copytree(FIXTURE, root)
    marker = OpenArmRecordingComplete.model_validate_json(
        (root / "recording-complete.json").read_bytes()
    )
    with pytest.raises(ValueError, match="DEPTH_SOURCE_CONTRACT_REQUIRED"):
        prepare(root, tmp_path / "derived", marker.recording_upload)
    assert not list((tmp_path / "derived").rglob("*.mp4"))
    for file in FIXTURE.rglob("*"):
        if file.is_file():
            assert (root / file.relative_to(FIXTURE)).read_bytes() == file.read_bytes()


def test_action_invalidation_is_not_repaired_into_valid_training_data(tmp_path):
    import sqlite3

    from hc_data_platform.continuous_recordings.openarm_raw.config import validate

    with sqlite3.connect(tmp_path / "index.sqlite3") as db:
        load_index(FIXTURE, db)
        snapshot = json.loads((FIXTURE / "session.json").read_text())
        manifest = json.loads((FIXTURE / "manifest.json").read_text())
        db.execute(
            "UPDATE raw_samples SET body=json_set(body,'$.validity_known',json('false')) "
            "WHERE json_extract(body,'$.kind')='action'"
        )
        aligned = list(
            rows(db, validate(snapshot["config"]), manifest["t0_ns"], manifest["end_ns"])
        )
        assert aligned and all(not row["export_qualified"] for row in aligned)


def test_raw_manifest_rejects_robot_index():
    value = json.loads((FIXTURE / "recording-complete.json").read_text())
    asset = dict(value["recording_upload"]["assets"][0], path="index.sqlite3")
    value["recording_upload"]["assets"].append(asset)
    with pytest.raises(ValueError):
        OpenArmRecordingComplete.model_validate(value)


def test_simultaneous_cameras_do_not_share_the_first_depth_message(tmp_path):
    import struct

    import numpy as np
    from mcap.writer import Writer

    from hc_data_platform.continuous_recordings.openarm_raw.prepare import read_depth_message

    path = tmp_path / "depth.mcap"
    with path.open("wb") as stream:
        writer = Writer(stream)
        writer.start()
        channel = writer.register_channel("/humanoid/session/depth", "humanoid-depth", 0)
        for camera, value in (("left", 100), ("right", 900)):
            pixels = np.full((16, 16), value, dtype="<u2")
            meta = json.dumps(
                {"source_id": camera, "source_seq": 7, "depth": {"dtype": "<u2", "shape": [16, 16]}}
            ).encode()
            writer.add_message(
                channel,
                log_time=1000,
                publish_time=1000,
                data=struct.pack("<I", len(meta)) + meta + pixels.tobytes(),
            )
        writer.finish()
    pixels, meta = read_depth_message(path, "right", 7, 1000)
    assert meta["source_id"] == "right"
    assert (pixels == 900).all()
    with pytest.raises(ValueError, match="source identity"):
        read_depth_message(path, "right", 8, 1000)
