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


def declared_synthetic_fixture(root, *, floating=False):
    """Make NEW test input with explicit oracle units, never upgrade real legacy data.

    The historical synthetic input provides numeric/time oracles. Rebuild its
    depth stream at half resolution with declared fixture-only unit evidence.
    This is a software fixture, not a driver, device or end-to-end acceptance.
    """
    from hc_data_platform.ingest.ports import crc64_ecma
    from hc_data_platform.publishing.depth_source import profile_hash

    assert json.loads((FIXTURE / "recording-complete.json").read_text())["synthetic"] is True
    shutil.copytree(FIXTURE, root)
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
            else:
                data, pixels = {}, None
            if "depth" in data:
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
