import copy

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.episode_store.camera_calibration import bind_calibration
from hc_data_platform.robot_assets.repository import InMemoryOrganizationRobotAssetRepository
from hc_data_platform.robot_assets.router import configure_organization_robot_assets
from hc_data_platform.robot_assets.service import OrganizationRobotAssetService
from hc_data_platform.robotics.camera_calibration import RobotCameraCalibration
from tests.robotics.test_robot_directory_api import ORGANIZATION_ID, _app, _auth


def configuration():
    def stream(key, frame):
        return {
            "feature_key": key,
            "optical_frame": frame,
            "intrinsics": {
                "width": 32,
                "height": 24,
                "fx": 20,
                "fy": 21,
                "cx": 16,
                "cy": 12,
                "distortion_model": "plumb_bob",
                "distortion_coefficients": [0.1, 0.2, 0, 0, 0],
            },
        }

    return {
        "root_frames": ["wrist_link"],
        "cameras": [
            {
                "camera_id": "hand",
                "rgb": stream("rgb", "rgb_optical"),
                "depth": stream("depth", "depth_optical"),
            }
        ],
        "static_transforms": [
            {
                "parent_frame": "wrist_link",
                "child_frame": "rgb_optical",
                "translation_m": [0.01, 0.02, 0.03],
                "rotation_xyzw": [0, 0, 0, 1],
            },
            {
                "parent_frame": "rgb_optical",
                "child_frame": "depth_optical",
                "translation_m": [0.02, 0, 0],
                "rotation_xyzw": [0, 0, 0, 1],
            },
        ],
    }


@pytest.mark.parametrize(
    "bad", ["cycle", "orphan", "duplicate", "quaternion", "nan", "distortion", "frame"]
)
def test_rejects_invalid_intrinsics_and_static_tf(bad):
    value = configuration()
    edges = value["static_transforms"]
    if bad == "cycle":
        edges[0]["parent_frame"] = "depth_optical"
    if bad == "orphan":
        edges[0]["parent_frame"] = "missing"
    if bad == "duplicate":
        edges.append(copy.deepcopy(edges[0]))
    if bad == "quaternion":
        edges[0]["rotation_xyzw"] = [0, 0, 0, 0]
    if bad == "nan":
        value["cameras"][0]["rgb"]["intrinsics"]["fx"] = float("nan")
    if bad == "distortion":
        value["cameras"][0]["rgb"]["intrinsics"]["distortion_coefficients"] = [0]
    if bad == "frame":
        value["cameras"][0]["rgb"]["optical_frame"] = "absent"
    with pytest.raises(ValidationError):
        RobotCameraCalibration.model_validate(value)


def test_feature_mapping_resolution_and_rgb_depth_contract():
    config = RobotCameraCalibration.model_validate(configuration())
    info = {
        "features": {
            "observation.images.rgb": {"dtype": "video", "shape": [24, 32, 3]},
            "observation.images.depth": {
                "dtype": "video",
                "shape": [24, 32, 1],
                "info": {"is_depth_map": True},
            },
        },
        "hc.feature_mapping": {
            "rgb": "observation.images.rgb",
            "depth": "observation.images.depth",
        },
    }
    snapshot = bind_calibration(config, "robot", info)
    assert snapshot.feature_bindings["observation.images.depth"] == ("hand", "depth")
    info["features"]["observation.images.rgb"]["shape"] = [240, 320, 3]
    with pytest.raises(ProblemException, match="CALIBRATION_MISMATCH"):
        bind_calibration(config, "robot", info)
    info["features"]["observation.images.rgb"]["shape"] = [24, 32, 3]
    info["hc.feature_mapping"] = {}
    with pytest.raises(ProblemException, match="CALIBRATION_MAPPING_REQUIRED"):
        bind_calibration(config, "robot", info)


def test_robot_camera_api_permissions_concurrency_and_roundtrip():
    repository = InMemoryOrganizationRobotAssetRepository((ORGANIZATION_ID,))
    configure_organization_robot_assets(OrganizationRobotAssetService(repository))
    current = {"value": _auth(manage=True)}
    client = TestClient(_app(current))
    root = f"/api/v1/organizations/{ORGANIZATION_ID}/robots"
    headers = {"Authorization": "Bearer test", "Idempotency-Key": "calibration-robot"}
    created = client.post(
        root,
        headers=headers,
        json={
            "display_name": "Camera test",
            "serial_no": "camera-test",
            "lifecycle_status": "DRAFT",
            "connectivity_state": "OFFLINE",
        },
    )
    assert created.status_code == 201
    robot = created.json()["data"]["robot"]["id"]
    url = f"{root}/{robot}/camera-calibration"
    assert client.get(url, headers=headers).json()["configuration"] is None
    headers["If-Match"] = "unconfigured"
    saved = client.put(url, headers=headers, json=configuration())
    assert saved.status_code == 200, saved.text
    assert saved.json()["configuration"]["cameras"][0]["depth"]["optical_frame"] == "depth_optical"
    assert client.get(url, headers=headers).json() == saved.json()
    assert client.put(url, headers=headers, json=configuration()).status_code == 412
    current["value"] = _auth()
    headers["If-Match"] = saved.json()["etag"]
    assert client.put(url, headers=headers, json=configuration()).status_code == 403
    assert client.get(url.replace(ORGANIZATION_ID, "other-org"), headers=headers).status_code == 403
