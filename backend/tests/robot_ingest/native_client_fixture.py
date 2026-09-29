"""Explicit synthetic native RGB-D source sealed for the real standalone uploader.

No robot captures or claims of real sensor accuracy. The independent client's
unchanged public completion/context schemas validate this input on enqueue.
"""

import hashlib
import json
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq

from hc_data_platform.ingest.ports import crc64_ecma
from hc_data_platform.lerobot_imports.capture import canonical, verify_completion
from hc_data_platform.recording_fields import SCHEMA
from tests.lerobot_imports.native_package_fixture import make_native_rgbd


def make_robot_native_rgbd(root, *, robot_id, task_id, local_robot_id):
    source_profile = dict(
        schema_version="hc-depth-source/1",
        encoding="16UC1",
        unit="source_unit",
        scale_m_per_unit=0.00025,
        invalid_values=[0],
        invalid_nonfinite=True,
        registered_to_rgb=False,
        registration_evidence="independent synthetic grids",
        evidence=dict(kind="synthetic_fixture", reference=__name__, version="1"),
    )
    make_native_rgbd(root, depth_source_profile=source_profile, fps=30)
    info_path = root / "meta/info.json"
    info = json.loads(info_path.read_text())
    info["robot_type"] = "synthetic_two_axis"
    info["recording_field_schema"] = SCHEMA
    features = info["features"]
    features["observation.images.head"] = features.pop("observation.images.head_rgb")
    features["observation.images.head"]["info"].update(
        {"video.codec": "hevc", "video.pix_fmt": "yuv420p"}
    )
    (root / "videos/observation.images.head_rgb").rename(root / "videos/observation.images.head")
    episodes_path = root / "meta/episodes/chunk-000/file-000.parquet"
    episode = pq.read_table(episodes_path).to_pylist()[0]
    episode = {
        key.replace("videos/observation.images.head_rgb/", "videos/observation.images.head/"): value
        for key, value in episode.items()
    }
    pq.write_table(pa.Table.from_pylist([episode]), episodes_path)
    table_path = root / "data/chunk-000/file-000.parquet"
    table = pq.read_table(table_path)
    for key in ("observation.state", "action"):
        features[key].update(dtype="float32", names=["axis-0", "axis-1"])
        index = table.schema.get_field_index(key)
        table = table.set_column(index, key, table.column(index).cast(pa.list_(pa.float32(), 2)))
    pq.write_table(table, table_path)
    info_path.write_bytes(canonical(info))
    event_names = [
        "pause",
        "input_loss",
        "clutch_release",
        "rebind",
        "clock_epoch_change",
        "source_restart",
    ]
    profile = dict(
        action_stage="sent",
        command_boundary="motion_or_teleop_to_driver_request",
        axes=[dict(group="arm", name=f"axis-{i}", unit="rad") for i in range(2)],
        cameras=[dict(id="head", width=16, height=16, calibration_ref="synthetic")],
        fps=30,
        profile_version="generic-two-axis-v1",
        robot_type="synthetic_two_axis",
        validity={
            group: dict(invalidate_on=event_names, mode="periodic", max_age_ms=100)
            for group in ("arm", "gripper")
        },
    )
    export_id = str(uuid4())
    context = dict(
        contract_version="openarm-capture/v1",
        export_id=export_id,
        session_id="synthetic-r3-" + export_id,
        robot_id=robot_id,
        local_robot_id=local_robot_id,
        collection_task_id=task_id,
        synthetic=True,
        physical_sync="unknown",
        profile=profile,
        configuration_sha256=hashlib.sha256(canonical(profile)).hexdigest(),
        model_sha256=hashlib.sha256(b"synthetic-no-physical-robot").hexdigest(),
        capture_started_at="2026-09-29T00:00:00Z",
        capture_ended_at="2026-09-29T00:00:00.200Z",
        episodes=[
            dict(
                episode_index=0,
                start_frame=0,
                end_frame=6,
                source_episode_id="synthetic-r3-source",
                export_qualified=True,
                task=episode["tasks"][0],
                outcome="success",
            )
        ],
        invalid_intervals=[],
    )
    (root / "capture-context.json").write_bytes(canonical(context))
    with (root / "source_mapping.jsonl").open("wb") as stream:
        for index in range(6):
            target = round(index * 1_000_000_000 / 30)
            mapping = dict(
                episode_index=0,
                frame_index=index,
                source_frame=index,
                source_episode_id="synthetic-r3-source",
                session_id=context["session_id"],
                clock_epoch=0,
                target_ns=target,
                receive_ns=target + 1_000_000,
                action_valid=[True, True],
                state_valid=[True, True],
                action_source_ns=[target, target],
                state_brackets_ns=[[target, target], [target, target]],
                cameras={
                    "head": dict(
                        capture_ns=target,
                        receive_ns=target + 1_000_000,
                        pts=index,
                        time_base=[1, 30],
                        source_frame=index,
                    )
                },
            )
            stream.write(canonical(mapping) + b"\n")
    inventory = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            body = path.read_bytes()
            inventory.append(
                dict(
                    path=path.relative_to(root).as_posix(),
                    size_bytes=len(body),
                    sha256=hashlib.sha256(body).hexdigest(),
                    crc64=str(crc64_ecma(body)),
                )
            )
    marker = {
        key: context[key]
        for key in (
            "export_id",
            "session_id",
            "robot_id",
            "collection_task_id",
            "synthetic",
            "contract_version",
        )
    }
    marker.update(
        schema_version="openarm-export-complete/v1",
        status="COMPLETE",
        completed_at="2026-09-29T00:00:01Z",
        assets=inventory,
        content_sha256=hashlib.sha256(canonical(inventory)).hexdigest(),
    )
    (root / "export-complete.json").write_bytes(canonical(marker))
    verify_completion(root)
    return root
