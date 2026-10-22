"""Actual collector encoding -> upload manifest -> platform decoding and sample validation."""

import copy
import json

import av
import pytest
from mcap.reader import make_reader

pytest.importorskip(
    "middleware.core.video_bundle", reason="cross-repository collector integration run"
)

from hc_data_platform.capture_schemes.common import digest
from hc_data_platform.capture_schemes.models import CaptureDefinition
from hc_data_platform.continuous_recordings.mcap_contracts import McapProcessingConfig
from hc_data_platform.continuous_recordings.mcap_native import prepare as prepare_mcap
from hc_data_platform.continuous_recordings.topic_video import prepare as prepare_bundle
from hc_data_platform.continuous_recordings.topic_video import restore
from tests.capture_schemes.processing_fixture import snapshot
from tests.capture_schemes.test_sample_validation import stored
from tests.capture_schemes.test_transactions import environment as environment
from tests.continuous_recordings.test_mcap_native import synthetic_mcap


def build(tmp_path, *, include_camera=True):
    # The integration run mounts the real collector source, not a reimplementation.
    from middleware.core.video_bundle import prepare

    source = synthetic_mcap(tmp_path / "capture.mcap", rgb_only=True)
    cfg = McapProcessingConfig.model_validate(
        {
            "collection_mode": "RGB",
            "time_source": "RGB_LOG_TIME",
            "fps": 30,
            "state_topic": "/joint_states",
            "action_topic": "/joint_cmd",
            "primary_camera": "/color",
            "state_joint_names": ["joint"],
            "state_joint_units": ["rad"],
            "action_joint_names": ["joint"],
            "action_joint_units": ["rad"],
            "cameras": [
                {
                    "camera_id": "head",
                    "topic": "/color",
                    "message_type": "sensor_msgs/msg/CompressedImage",
                    "kind": "rgb",
                    "required": True,
                }
            ],
        }
    )
    if not include_camera:
        from .test_mcap_processing import config, joint_source

        source = joint_source(tmp_path / "joints.mcap")
        cfg = McapProcessingConfig.model_validate(
            {
                **config(),
                "collection_mode": "JOINT_ONLY",
                "cameras": [],
            }
        )
    _, provenance = prepare_mcap(source, tmp_path / "baseline", cfg)
    from hc_data_platform.capture_schemes.models import OutputContract

    fields = {}
    for name, feature in provenance["output_features"].items():
        role = (
            "state"
            if name == "observation.state"
            else "action"
            if name == "action"
            else "rgb"
            if name.startswith("observation.images.")
            else "timestamp"
        )
        fields[name] = {
            k: v for k, v in feature.items() if k in ("dtype", "shape", "names", "units")
        }
        fields[name]["role"] = role
    output = OutputContract(frequency_hz=cfg.fps, time_source=cfg.time_source, features=fields)
    adapter = {
        "source_format": "CAPTURE_BUNDLE",
        "parser_version": "capture-bundle/1",
        "configuration": cfg.model_dump(mode="json"),
    }
    frozen = snapshot(output)
    frozen.update(source_format="CAPTURE_BUNDLE", effective_adapter=adapter)
    frozen["snapshot_sha256"] = digest({k: v for k, v in frozen.items() if k != "snapshot_sha256"})
    job = {
        "recording_id": "recording",
        "started_at": "2026-10-21T00:00:00Z",
        "ended_at": "2026-10-21T00:00:01Z",
        "video_fps": cfg.fps,
        "capture_snapshot": frozen,
        "binding": {"robot_id": "x1", "collection_task_id": "task"},
        "local_robot": {"local_robot_id": "local-x1"},
    }
    target = prepare(source, tmp_path / "bundle", job)
    return source, target, job, cfg, output, adapter


def test_real_bundle_roundtrip_preserves_timestamps_counts_and_sensor_bytes(tmp_path):
    from humanoid_upload.recording_bundle import manifest_for_recording

    source, target, job, cfg, output, adapter = build(tmp_path)
    original = source.read_bytes()
    assert list(target.glob("videos/*.mp4"))
    with (target / "raw.mcap").open("rb") as stream:
        refs = [(c, m) for _, c, m in make_reader(stream).iter_messages()]
    rgb = [m for c, m in refs if c.message_encoding == "hc-topic-video/v1"]
    assert len(rgb) == 12 and all(len(m.data) < 256 for m in rgb)
    decoded = restore(target, tmp_path / "restored.mcap")
    with source.open("rb") as stream:
        original_rows = [
            (c.topic, m.log_time, m.publish_time, m.sequence, m.data)
            for _, c, m in make_reader(stream).iter_messages()
        ]
    with decoded.open("rb") as stream:
        decoded_rows = [
            (c.topic, m.log_time, m.publish_time, m.sequence, m.data)
            for _, c, m in make_reader(stream).iter_messages()
        ]
    assert [r[:4] for r in original_rows] == [r[:4] for r in decoded_rows]
    assert [r for r in original_rows if r[0] != "/color"] == [
        r for r in decoded_rows if r[0] != "/color"
    ]
    with av.open(str(next(target.glob("videos/*.mp4")))) as video:
        video_times = [float(frame.pts * frame.time_base) for frame in video.decode(video=0)]
    source_times = [(r[1] - 1_000_000_000) / 1e9 for r in original_rows if r[0] == "/color"]
    assert video_times == pytest.approx(source_times, abs=0.000002)
    from mcap_ros2.decoder import DecoderFactory

    with source.open("rb") as stream:
        decoder = DecoderFactory()
        headers = [
            decoder.decoder_for(c.message_encoding, s)(m.data).header
            for s, c, m in make_reader(stream).iter_messages()
            if c.topic == "/color"
        ]
    assert [json.loads(r[4])["header"] for r in decoded_rows if r[0] == "/color"] == [
        {"stamp": {"sec": h.stamp.sec, "nanosec": h.stamp.nanosec}, "frame_id": h.frame_id}
        for h in headers
    ]
    _, _, manifest = manifest_for_recording(
        target,
        {
            "data_directory": str(tmp_path),
            "robot_id": "x1",
            "local_robot_id": "local-x1",
            "task_id": "task",
            "read_bytes_per_second": 1_000_000_000,
        },
        "00000000-0000-4000-8000-000000000001",
    )
    assert manifest["source_format"] == "CAPTURE_BUNDLE"
    assert {a["path"] for a in manifest["assets"]} == {
        str(p.relative_to(target)) for p in target.rglob("*") if p.is_file()
    }
    from hc_data_platform.robot_ingest.recording_contract import OpenArmRecordingComplete

    marker = OpenArmRecordingComplete.model_validate_json(
        (target / "recording-complete.json").read_bytes()
    )
    config, _ = prepare_bundle(
        target, tmp_path / "derived", marker.recording_upload, job["capture_snapshot"]
    )
    assert config["cameras"][0]["fps"] == 30
    assert source.read_bytes() == original
    validator, sample, _ = stored(target, "CAPTURE_BUNDLE")
    document = CaptureDefinition(name="bundle", adapters=[adapter], output=output).model_dump(
        mode="json"
    )
    result = validator.validate("org", document, sample)
    assert result["status"] == "PASSED", result
    assert result["frame_count"] == 12
    broken = copy.deepcopy(document)
    broken["output"]["features"]["action"]["units"] = ["m"]
    assert validator.validate("org", broken, sample)["status"] == "FAILED"


def test_video_frame_inventory_corruption_is_rejected(tmp_path):
    _, target, *_ = build(tmp_path)
    session = json.loads((target / "session.json").read_text())
    session["videos"][0]["frame_count"] += 1
    (target / "session.json").write_text(json.dumps(session))
    with pytest.raises(ValueError, match="inventory"):
        restore(target, tmp_path / "corrupt.mcap")


@pytest.mark.parametrize("include_camera", [False, True])
def test_bundle_materialization_commits_matching_command_and_reuses_receipt(
    tmp_path, include_camera
):
    from types import SimpleNamespace

    from hc_data_platform.robot_ingest.models import RobotIngestAsset
    from hc_data_platform.robot_ingest.raw_recording import materialize
    from hc_data_platform.robot_ingest.recording_contract import OpenArmRecordingComplete

    _, target, job, _, _, _ = build(tmp_path, include_camera=include_camera)
    _, _, storage = stored(target, "CAPTURE_BUNDLE")
    marker = json.loads((target / "recording-complete.json").read_text())
    command = OpenArmRecordingComplete.model_validate(marker).recording_upload
    originals = {
        item.path: RobotIngestAsset(
            asset_id=f"asset-{index}",
            path=item.path,
            role=item.role.value,
            media_type=item.media_type,
            object_key="source/" + item.path,
            multipart_upload_id="completed",
            expected_size_bytes=item.size,
            expected_sha256=item.sha256,
            expected_crc64=item.crc64,
            actual_size_bytes=item.size,
            actual_sha256=item.sha256,
            actual_crc64=item.crc64,
            state="COMPLETED",
        )
        for index, item in enumerate(command.assets)
    }
    upload = SimpleNamespace(
        target=SimpleNamespace(organization_id="org", project_id="p", region_code="global"),
        raw_source_id="bundle-materialization",
        processing_config_snapshot=job["capture_snapshot"],
    )
    derived, assets = materialize(storage, upload, marker, command, originals)
    assert derived.schema_version == (
        "continuous-recording-upload/v2" if include_camera else "continuous-recording-upload/v3"
    )
    assert bool(derived.recording_config.cameras) is include_camera
    assert {a.path for a in derived.assets} == set(assets)
    assert all(
        storage.objects["source/" + name] == (target / name).read_bytes() for name in originals
    )
    repeated, repeated_assets = materialize(storage, upload, marker, command, originals)
    assert repeated == derived and repeated_assets == assets


def test_native_package_still_validates_its_actual_axis_units(tmp_path):
    from hc_data_platform.capture_schemes.bundles import validate_prepared
    from hc_data_platform.capture_schemes.models import OutputContract
    from hc_data_platform.continuous_recordings.openarm_raw.prepare import prepare
    from hc_data_platform.core.errors import ProblemException
    from hc_data_platform.robot_ingest.recording_contract import OpenArmRecordingComplete
    from tests.continuous_recordings.test_openarm_raw import declared_synthetic_fixture

    root, destination = tmp_path / "original", tmp_path / "derived"
    declared_synthetic_fixture(root)
    marker = OpenArmRecordingComplete.model_validate_json(
        (root / "recording-complete.json").read_bytes()
    )
    cfg, _ = prepare(root, destination, marker.recording_upload)
    with (destination / "sensors/robot.mcap").open("rb") as stream:
        state = next(
            json.loads(m.data)
            for _, c, m in make_reader(stream).iter_messages()
            if c.topic == "/observation/state"
        )
    fields = {
        name: {
            "role": role,
            "dtype": "float32",
            "shape": [len(state["names"])],
            "names": state["names"],
            "units": state["units"],
        }
        for name, role in (("observation.state", "state"), ("action", "action"))
    }
    output = OutputContract(frequency_hz=30, time_source="STATE_LOG_TIME", features=fields)
    frozen = {
        "output": output.model_dump(mode="json"),
        "effective_adapter": {
            "source_format": "CAPTURE_BUNDLE",
            "configuration": {
                "collection_mode": "JOINT_ONLY",
                "time_source": "STATE_LOG_TIME",
                "fps": 30,
                "state_topic": "/observation/state",
                "action_topic": "/action",
            },
        },
    }
    assert validate_prepared(root, destination, cfg, frozen)["frame_count"] == 21
    frozen["output"]["features"]["action"]["units"][0] = "degree"
    with pytest.raises(ProblemException, match="CAPTURE_STRUCTURE_MISMATCH"):
        validate_prepared(root, destination, cfg, frozen)


def test_bundle_sample_publishes_and_freezes_a_real_task_plan(environment, tmp_path):  # noqa: F811
    from types import SimpleNamespace

    import psycopg

    from hc_data_platform.capture_schemes import associations, plans, schemes, transactions
    from hc_data_platform.capture_schemes.models import (
        AssociateScheme,
        PrepareRecording,
        SchemeDraft,
        SetTaskPlan,
        VersionReference,
    )
    from hc_data_platform.capture_schemes.references import CaptureReference

    dsn, scope, tasks = environment
    _, target, _, _, output, adapter = build(tmp_path)
    validator, sample, _ = stored(
        target, "CAPTURE_BUNDLE", organization_id=scope[0], robot_id="robot-1"
    )
    with psycopg.connect(dsn) as db:
        saved = schemes.save(
            db,
            scope[0],
            "bundle",
            SchemeDraft(
                name="MCAP and independent MP4",
                expected_revision=0,
                output=output,
                adapters=[adapter],
            ),
            "tester",
        )
        evidence = validator.validate(scope[0], saved["draft"], sample)
        assert evidence["status"] == "PASSED", evidence
        schemes.record_validation(db, scope[0], "bundle", 1, evidence, "tester")
        published = schemes.publish(db, scope[0], "bundle", 1, "tester")
        ref = VersionReference(**{k: published[k] for k in VersionReference.model_fields})
        mapping = associations.associate(
            db,
            scope[0],
            "robot-1",
            AssociateScheme(expected_revision=0, scheme=ref, make_default=True),
            "tester",
        )[0]
        associations.record_validation(
            db,
            scope[0],
            "robot-1",
            "bundle",
            1,
            {**evidence, "mapping_sha256": mapping["mapping"]["content_sha256"]},
            "tester",
        )
        plans.save_plan(
            db,
            scope,
            tasks[0],
            SetTaskPlan(
                expected_revision=0,
                scheme=ref,
                participants=[{"robot_id": "robot-1", "mapping_version": 1}],
            ),
            "tester",
        )
        frozen = plans.prepare_recording(
            db,
            scope,
            tasks[0],
            "robot-1",
            PrepareRecording(
                expected_plan_revision=1,
                recording_id="bundle-recording",
                source_format="CAPTURE_BUNDLE",
            ),
            "tester",
        )
        upload = SimpleNamespace(
            source_format="CAPTURE_BUNDLE",
            ingest_identity_id="identity",
            client_upload_id="client",
            authenticated_robot_id="robot-1",
            target=SimpleNamespace(
                organization_id=scope[0],
                project_id=scope[1],
                region_code=scope[2],
                collection_task_id=tasks[0],
                dataset_id=frozen["dataset_id"],
            ),
            manifest=SimpleNamespace(
                capture_plan=CaptureReference(
                    **{k: frozen[k] for k in CaptureReference.model_fields}
                )
            ),
        )
        assert transactions.bind_upload(db, upload) == frozen
        assert frozen["effective_adapter"]["configuration"]["primary_camera"] == "/color"
        assert plans.get_plan(db, scope, tasks[0])["locked_at"] is not None


def test_task_finalizer_local_bundle_can_bind_once_and_enqueue_actual_package(
    tmp_path, monkeypatch
):
    from middleware.core.task_recording import TaskRecorder
    from middleware.core.topic_recorder import TopicRecorder
    from test_platform_task_recording import LOCAL, choice, finish, setup

    cfg, platform = setup(tmp_path, monkeypatch)
    cfg["ros"]["recording"]["format"] = "mcap_mp4"
    platform.connect("http://platform.invalid/#robot-connect=" + "c" * 48)
    recorder = TaskRecorder(TopicRecorder(cfg["ros"]["recording"], tmp_path), platform)
    recorder.directory.mkdir(parents=True, exist_ok=True)
    source = synthetic_mcap(recorder.directory / "capture.mcap", rgb_only=True)
    job = dict(
        recording_id="local-bundle",
        path=str(source),
        state="finalizing",
        format="mcap_mp4",
        started_at="2026-10-21T00:00:00Z",
        ended_at="2026-10-21T00:00:01Z",
        binding=None,
        local_robot=LOCAL,
        upload_requested=False,
    )
    recorder.jobs[job["recording_id"]] = job
    recorder.save(job)
    try:
        recorder.finish(job["recording_id"])
        assert recorder.records()[0]["state"] == "ready", recorder.records()
        assert not platform.upload.queue.jobs()
        assert any(row["format"] == "CAPTURE_BUNDLE" for row in recorder.list_recordings())
        recorder.upload_recording("bundle:capture", choice(platform, "A"))
        finish(recorder)
        assert recorder.records()[0]["state"] == "queued", recorder.records()
        queued = platform.upload.queue.jobs()
        assert len(queued) == 1
        assert queued[0]["manifest"]["source_format"] == "CAPTURE_BUNDLE"
        assert any(asset["path"].endswith(".mp4") for asset in queued[0]["manifest"]["assets"])
        recorder.upload_recording("bundle:capture")
        assert len(platform.upload.queue.jobs()) == 1
    finally:
        recorder.close()
