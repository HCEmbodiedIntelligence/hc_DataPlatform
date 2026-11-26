from __future__ import annotations

from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot
from hc_data_platform.lerobot_imports.schema_diagnostics import (
    feature_label,
    schema_conflict_detail,
)
from hc_data_platform.robot_ingest.processing_contract import (
    EpisodeProgress,
    ProcessingDocument,
    ProcessingFailure,
)


def test_feature_label():
    # Native feature hex decoding
    encoded = "/lerobot/features/" + b"action.base.velocity".hex()
    assert feature_label(encoded) == "action.base.velocity（底盘速度命令）"

    # Camera topic mapping
    cam_topic = "/camera/observation_cameras_head_color/image"
    assert (
        feature_label(cam_topic)
        == "observation.images.observation_cameras_head_color（头部相机视频）"
    )


def test_format_schema_conflict_diff():
    existing_fields = {
        "/humanoid/observation/state": "json",
        "/humanoid/action": "json",
        "/metadata/source": "json",
        "/lerobot/features/" + b"action.base.velocity".hex(): "json",
    }
    existing = DatasetSchemaSnapshot.create(
        project_id="p",
        dataset_id="d",
        schema_snapshot_id="s1",
        frequency_hz=15.0,
        fields=existing_fields,
    )

    candidate_fields = {
        "/humanoid/observation/state": "json",
        "/humanoid/action": "json",
        "/metadata/source": "json",
        "/camera/observation_cameras_head_color/image": "json",
    }
    candidate = DatasetSchemaSnapshot.create(
        project_id="p",
        dataset_id="d",
        schema_snapshot_id="s1",
        frequency_hz=15.0,
        fields=candidate_fields,
    )

    detail = schema_conflict_detail(candidate, existing)
    assert "数据集结构已固定，上传数据与已有结构冲突" in detail
    assert "缺少 action.base.velocity（底盘速度命令）" in detail
    assert "新增 observation.images.observation_cameras_head_color（头部相机视频）" in detail


def test_processing_failure_retains_detail():
    detail = "数据集结构已固定，上传数据与已有结构冲突：缺少 action.base.velocity"
    failure = ProcessingFailure("LEROBOT_DATASET_SCHEMA_CONFLICT", detail=detail)
    assert failure.code == "LEROBOT_DATASET_SCHEMA_CONFLICT"
    assert failure.detail == detail
    assert str(failure) == failure.code

    unsafe = ProcessingFailure(failure.code, detail="https://storage.invalid/signed")
    assert unsafe.detail is None
    assert str(unsafe) == failure.code


def test_processing_document_and_episode_accept_error_detail():
    ep = EpisodeProgress(
        source_episode_id="src-0",
        source_episode_index=0,
        episode_id="ep-0",
        status="FAILED",
        error_code="LEROBOT_DATASET_SCHEMA_CONFLICT",
        error_detail="detail test",
    )
    assert ep.error_detail == "detail test"

    doc = ProcessingDocument(
        phase="DONE",
        error_code="LEROBOT_DATASET_SCHEMA_CONFLICT",
        error_detail="detail test",
        episodes=(ep,),
    )
    assert doc.error_detail == "detail test"
    dump = doc.model_dump()
    assert dump["error_detail"] == "detail test"
    assert dump["episodes"][0]["error_detail"] == "detail test"
