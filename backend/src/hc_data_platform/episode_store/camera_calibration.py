"""Freeze robot configuration with an explicit mapping to the exported video features."""

from pydantic import Field, model_validator

from hc_data_platform.robotics.camera_calibration import (
    CalibrationModel,
    RobotCameraCalibration,
    load_camera_calibration,
)


class ExportCameraCalibration(CalibrationModel):
    robot_id: str
    configuration: RobotCameraCalibration
    configuration_sha256: str = Field(pattern="^[0-9a-f]{64}$")
    # LeRobot video feature -> configured camera and stream.
    feature_bindings: dict[str, tuple[str, str]]
    snapshot_policy: str = "robot_configuration_at_export_request"

    @model_validator(mode="after")
    def checksum(self):
        if self.configuration_sha256 != self.configuration.content_sha256:
            raise ValueError("camera calibration checksum mismatch")
        return self


def freeze_camera_calibration(package, connections, modalities):
    from .collection_export import selected_info

    robot_id = package.manifest.input.robot_id
    if robot_id == "unknown":
        return None
    configuration = load_camera_calibration(
        connections,
        package.receipt.data.identity.organization_id,
        robot_id,
    )
    if configuration is None:
        return None
    info, _, _ = selected_info(package, modalities)
    return bind_calibration(configuration, robot_id, info)


def bind_calibration(configuration, robot_id, info):
    from .delivery_repository import failure

    mapping = info.get("hc.feature_mapping", {})
    videos = {k: v for k, v in info["features"].items() if v["dtype"] == "video"}
    bindings = {}
    for camera in configuration.cameras:
        for kind in ("rgb", "depth"):
            stream = getattr(camera, kind)
            if stream is None:
                continue
            feature = (
                stream.feature_key
                if stream.feature_key in videos
                else mapping.get(stream.feature_key)
            )
            if feature not in videos:
                continue  # Config may describe sensors absent from this particular recording.
            declared = videos[feature]
            if bool(declared.get("info", {}).get("is_depth_map")) != (kind == "depth"):
                raise failure(
                    "CAMERA_CALIBRATION_MISMATCH", f"{feature} 的 RGB/深度类型与相机配置不同。", 422
                )
            shape = declared["shape"]
            if list(shape[:2]) != [stream.intrinsics.height, stream.intrinsics.width]:
                raise failure(
                    "CAMERA_CALIBRATION_MISMATCH", f"{feature} 的分辨率与相机内参不同。", 422
                )
            if feature in bindings:
                raise failure("CAMERA_CALIBRATION_MISMATCH", f"{feature} 对应多份相机内参。", 422)
            bindings[feature] = (camera.camera_id, kind)
    missing = sorted(set(videos) - bindings.keys())
    if missing:
        raise failure(
            "CAMERA_CALIBRATION_MAPPING_REQUIRED",
            "请在机器人相机配置中补充这些图像特征的映射：" + ", ".join(missing),
            422,
        )
    return ExportCameraCalibration(
        robot_id=robot_id,
        configuration=configuration,
        configuration_sha256=configuration.content_sha256,
        feature_bindings=bindings,
    )
