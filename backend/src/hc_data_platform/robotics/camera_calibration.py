"""Robot-owned optical calibration and explicitly directed static frame transforms."""

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hc_data_platform.episode_store.models import digest

Frame = Annotated[str, Field(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_/.-]*$", max_length=256)]
Number = Annotated[float, Field(allow_inf_nan=False)]


class CalibrationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CameraIntrinsics(CalibrationModel):
    width: int = Field(gt=0, le=32768)
    height: int = Field(gt=0, le=32768)
    fx: Number = Field(gt=0)
    fy: Number = Field(gt=0)
    cx: Number
    cy: Number
    skew: Number = 0
    distortion_model: Literal["none", "plumb_bob", "rational_polynomial", "equidistant"]
    distortion_coefficients: tuple[Number, ...] = Field(max_length=8)

    @model_validator(mode="after")
    def distortion(self):
        expected = {"none": 0, "plumb_bob": 5, "rational_polynomial": 8, "equidistant": 4}
        if len(self.distortion_coefficients) != expected[self.distortion_model]:
            raise ValueError("distortion coefficient count does not match the selected model")
        return self


class CameraStreamCalibration(CalibrationModel):
    # Explicit LeRobot video feature or source topic; never associate by list position.
    feature_key: str = Field(min_length=1, max_length=256)
    optical_frame: Frame
    intrinsics: CameraIntrinsics


class CameraCalibration(CalibrationModel):
    camera_id: Frame
    rgb: CameraStreamCalibration | None = None
    depth: CameraStreamCalibration | None = None

    @model_validator(mode="after")
    def has_stream(self):
        if self.rgb is None and self.depth is None:
            raise ValueError("a camera requires RGB or depth calibration")
        return self


class StaticTransform(CalibrationModel):
    parent_frame: Frame
    child_frame: Frame
    translation_m: tuple[Number, Number, Number]
    rotation_xyzw: tuple[Number, Number, Number, Number]

    @model_validator(mode="after")
    def rigid(self):
        if self.parent_frame == self.child_frame:
            raise ValueError("a TF edge cannot point to itself")
        if not math.isclose(sum(x * x for x in self.rotation_xyzw), 1, abs_tol=1e-6):
            raise ValueError("rotation_xyzw must be a unit quaternion")
        return self


class RobotCameraCalibration(CalibrationModel):
    format: Literal["hc-robot-camera-calibration/1"] = "hc-robot-camera-calibration/1"
    # Robot links may move relative to one another; only the listed edges are static.
    root_frames: tuple[Frame, ...] = Field(min_length=1, max_length=128)
    cameras: tuple[CameraCalibration, ...] = Field(min_length=1, max_length=64)
    static_transforms: tuple[StaticTransform, ...] = Field(max_length=256)
    transform_convention: Literal["p_parent = R_xyzw * p_child + translation_m"] = (
        "p_parent = R_xyzw * p_child + translation_m"
    )
    optical_axes: Literal["x_right_y_down_z_forward"] = "x_right_y_down_z_forward"

    @model_validator(mode="after")
    def tree(self):
        if len(set(self.root_frames)) != len(self.root_frames):
            raise ValueError("duplicate root frame")
        if len({c.camera_id for c in self.cameras}) != len(self.cameras):
            raise ValueError("duplicate camera id")
        parents = {}
        for edge in self.static_transforms:
            if edge.child_frame in parents or edge.child_frame in self.root_frames:
                raise ValueError("each non-root frame must have exactly one parent")
            parents[edge.child_frame] = edge.parent_frame
        for frame in parents:
            visited = set()
            current = frame
            while current not in self.root_frames:
                if current in visited or current not in parents:
                    raise ValueError("TF must be acyclic and connected to a declared root")
                visited.add(current)
                current = parents[current]
        features = set()
        for camera in self.cameras:
            for stream in (camera.rgb, camera.depth):
                if stream is None:
                    continue
                if stream.feature_key in features:
                    raise ValueError("duplicate calibrated feature")
                features.add(stream.feature_key)
                if (
                    stream.optical_frame not in parents
                    and stream.optical_frame not in self.root_frames
                ):
                    raise ValueError("camera optical frame is absent from the TF tree")
        return self

    @property
    def content_sha256(self):
        return digest(self.model_dump(mode="json"))


class CameraCalibrationState(CalibrationModel):
    organization_id: str
    robot_id: str
    configuration: RobotCameraCalibration | None = None
    etag: str = "unconfigured"


def load_camera_calibration(connections, organization_id, robot_id):
    with connections() as connection:
        row = connection.execute(
            "SELECT configuration FROM robotics.robot_camera_calibrations "
            "WHERE organization_id=%s AND robot_id=%s",
            (organization_id, robot_id),
        ).fetchone()
    return RobotCameraCalibration.model_validate(row[0]) if row else None


def save_camera_calibration(connections, organization_id, robot_id, configuration, expected):
    from hc_data_platform.core.errors import problem

    with connections() as connection:
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (organization_id + ":camera-calibration:" + robot_id,),
        )
        row = connection.execute(
            "SELECT configuration FROM robotics.robot_camera_calibrations "
            "WHERE organization_id=%s AND robot_id=%s",
            (organization_id, robot_id),
        ).fetchone()
        etag = (
            RobotCameraCalibration.model_validate(row[0]).content_sha256 if row else "unconfigured"
        )
        if expected != etag:
            raise problem(
                status=412,
                code="CAMERA_CALIBRATION_CHANGED",
                title="相机参数已更新",
                detail="请重新读取机器人相机参数后再保存。",
            )
        connection.execute(
            "INSERT INTO robotics.robot_camera_calibrations (organization_id,robot_id,configuration) "
            "VALUES (%s,%s,%s::jsonb) ON CONFLICT (organization_id,robot_id) "
            "DO UPDATE SET configuration=EXCLUDED.configuration,updated_at=now()",
            (organization_id, robot_id, configuration.model_dump_json()),
        )
        connection.commit()
