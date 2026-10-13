import io
import json
import zipfile

import pytest

from hc_data_platform.episode_store import camera_calibration as module
from hc_data_platform.episode_store.camera_calibration import bind_calibration
from hc_data_platform.robotics.camera_calibration import RobotCameraCalibration
from tests.episode_store.test_collection_delivery_services import queued  # noqa: F401
from tests.episode_store.test_collection_export_services import pair  # noqa: F401
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_complete_delivery_services import run_job
from tests.robotics.test_camera_calibration import configuration


@pytest.mark.integration
def test_queued_export_freezes_calibration_and_delivers_rgb_depth_tf(queued, monkeypatch):  # noqa: F811
    service, source, body, auth = queued

    def freeze(package, connections, modalities):
        from hc_data_platform.episode_store.collection_export import selected_info

        info, _, _ = selected_info(package, modalities)
        value = configuration()
        for key, feature in info["features"].items():
            if feature["dtype"] != "video":
                continue
            kind = "depth" if feature.get("info", {}).get("is_depth_map") else "rgb"
            stream = value["cameras"][0][kind]
            stream["feature_key"] = key
            stream["intrinsics"].update(height=feature["shape"][0], width=feature["shape"][1])
        return bind_calibration(RobotCameraCalibration.model_validate(value), "robot-camera", info)

    monkeypatch.setattr(module, "freeze_camera_calibration", freeze)
    with scoped(source):
        job = service.request_collection(body, "calibration-export", auth)
        snapshots = [m.camera_calibration for m in job.plan.members]
        assert all(s is not None for s in snapshots)

        def changed(*args):
            raise AssertionError("worker must never reread current robot configuration")

        monkeypatch.setattr(module, "freeze_camera_calibration", changed)
        run_job(service, job)
        ready = service.status(job.job_id, auth)
        assert ready.status == "READY", ready
        row = service.repository.get_job(job.job_id)
        with zipfile.ZipFile(
            io.BytesIO(b"".join(service.storage.read_chunks(row["artifact_key"])))
        ) as archive:
            info = json.loads(archive.read("collection/meta/info.json"))
            index = json.loads(archive.read("collection/" + info["hc.camera_calibration"]))
            for i, entry in enumerate(index["episodes"]):
                exported = json.loads(archive.read("collection/" + entry["path"]))
                assert exported == snapshots[i].model_dump(mode="json")
                assert {v[1] for v in exported["feature_bindings"].values()} == {"rgb", "depth"}
                assert exported["configuration"]["static_transforms"][1]["translation_m"] == [
                    0.02,
                    0,
                    0,
                ]
