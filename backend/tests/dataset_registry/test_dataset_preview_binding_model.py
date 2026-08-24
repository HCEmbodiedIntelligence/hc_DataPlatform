from __future__ import annotations

import pytest
from pydantic import ValidationError

from hc_data_platform.dataset_registry.models import (
    DatasetPageEpisodeDataBinding,
    DatasetPageEpisodePreviewBinding,
    DatasetPageEpisodeStream,
)


def _binding(**overrides: object) -> DatasetPageEpisodePreviewBinding:
    values = {
        "rollout_id": "rollout-camera-1",
        "lance_version": 7,
        "annotation_revision": 2,
        "camera_id": "front-rgb",
        "frequency_hz": 30,
        "start_step": 0,
        "end_step": 30,
        **overrides,
    }
    return DatasetPageEpisodePreviewBinding.model_validate(values)


def _data_binding(**overrides: object) -> DatasetPageEpisodeDataBinding:
    values = {
        "rollout_id": "rollout-data-1",
        "lance_version": 7,
        "modality_key": "joint.position",
        "value_kind": "VECTOR",
        "start_step": 0,
        "end_step": 30,
        **overrides,
    }
    return DatasetPageEpisodeDataBinding.model_validate(values)


def test_episode_preview_binding_is_camera_only_bounded_and_has_no_storage_locator() -> None:
    stream = DatasetPageEpisodeStream(
        episode_stream_id="stream_camera_1",
        channel_path="/camera/front/image_raw",
        kind="RGB_VIDEO",
        t_start_ns="100",
        t_end_ns="1100",
        preview_binding=_binding(),
    )
    assert stream.preview_binding is not None
    assert stream.preview_binding.rollout_id == "rollout-camera-1"

    with pytest.raises(ValidationError, match="end_step must be greater"):
        _binding(start_step=10, end_step=10)
    with pytest.raises(ValidationError, match="only valid for RGB or depth"):
        DatasetPageEpisodeStream(
            episode_stream_id="stream_force_1",
            channel_path="/force/wrench",
            kind="FORCE",
            t_start_ns="100",
            t_end_ns="1100",
            preview_binding=_binding(),
        )
    with pytest.raises(ValidationError):
        DatasetPageEpisodePreviewBinding.model_validate(
            {
                **_binding().model_dump(),
                "object_locator": "s3://must-not-leak/object",
            }
        )


def test_episode_data_binding_is_non_camera_only_bounded_and_declares_the_decoder() -> None:
    stream = DatasetPageEpisodeStream(
        episode_stream_id="stream_joint_1",
        channel_path="/joint_states/position",
        kind="JOINT_STATE",
        t_start_ns="100",
        t_end_ns="1100",
        data_binding=_data_binding(),
    )
    assert stream.data_binding is not None
    assert stream.data_binding.modality_key == "joint.position"
    assert stream.data_binding.value_kind == "VECTOR"

    with pytest.raises(ValidationError, match="end_step must be greater"):
        _data_binding(start_step=10, end_step=10)
    with pytest.raises(ValidationError, match="only valid for supported non-camera"):
        DatasetPageEpisodeStream(
            episode_stream_id="stream_camera_data_1",
            channel_path="/camera/front/image_raw",
            kind="RGB_VIDEO",
            t_start_ns="100",
            t_end_ns="1100",
            data_binding=_data_binding(),
        )
    with pytest.raises(ValidationError, match="pointcloud data_binding"):
        DatasetPageEpisodeStream(
            episode_stream_id="stream_pointcloud_1",
            channel_path="/lidar/points",
            kind="POINTCLOUD",
            t_start_ns="100",
            t_end_ns="1100",
            data_binding=_data_binding(),
        )
    with pytest.raises(ValidationError):
        DatasetPageEpisodeDataBinding.model_validate(
            {
                **_data_binding().model_dump(),
                "object_locator": "s3://must-not-leak/object",
            }
        )
