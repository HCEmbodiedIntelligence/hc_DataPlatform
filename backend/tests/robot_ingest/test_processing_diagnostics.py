from unittest.mock import Mock

import pytest

from hc_data_platform.lerobot_imports.delivery import NativeDeliveryProjection
from hc_data_platform.lerobot_imports.processing import NativeImportProgress
from hc_data_platform.robot_ingest.processing_contract import (
    EpisodeProgress,
    ProcessingDocument,
    ProcessingFailure,
)
from hc_data_platform.robot_ingest.processing_errors import ProcessingError, current_failure


def test_safe_detail_survives_persistence_and_discard_removes_aggregate_failure():
    episode = EpisodeProgress(
        source_episode_id="source",
        source_episode_index=0,
        episode_id="episode",
        status="FAILED",
        error_code="CAPTURE_DATASET_INCOMPATIBLE",
        error_detail="缺少 observation.images.head（头部相机视频）",
    )
    doc = ProcessingDocument(
        phase="DONE",
        error_code=episode.error_code,
        error_detail=episode.error_detail,
        episodes=(episode,),
    )
    reloaded = ProcessingDocument.model_validate_json(doc.model_dump_json())
    assert current_failure(reloaded).detail == episode.error_detail
    discarded = reloaded.model_copy(
        update={"episodes": (episode.model_copy(update={"status": "DISCARDED"}),)}
    )
    assert current_failure(discarded) is None
    assert current_failure(
        ProcessingDocument.model_validate({"phase": "DONE", "error_code": "OLD_FAILURE"})
    ) == ProcessingError("OLD_FAILURE", None)


@pytest.mark.parametrize(
    "detail",
    [
        "failed https://bucket/file?X-Amz-Signature=private",
        "Authorization: Bearer private",
        "password=private",
        "token: private",
    ],
)
def test_exception_secrets_are_not_exposed_in_persisted_diagnostics(detail):
    error = ProcessingFailure("CAPTURE_SOURCE_INVALID", detail=detail)
    assert error.detail is None and str(error) == "CAPTURE_SOURCE_INVALID"
    document = ProcessingDocument(error_code=error.code, error_detail=detail)
    assert document.error_detail is None


def test_native_progress_uses_owned_current_error_with_matching_code_only():
    sources = Mock(list_episodes=Mock(return_value=()))
    errors = Mock(
        for_source=Mock(return_value=ProcessingError("CAPTURE_SOURCE_INVALID", "字段缺失"))
    )
    projection = NativeDeliveryProjection(
        sources, Mock(), Mock(for_episodes=Mock(return_value=())), errors=errors
    )
    progress = NativeImportProgress(
        import_id="raw",
        dataset_id="dataset",
        status="FAILED",
        episode_count=1,
        ready=0,
        failed=1,
        last_error_code="CAPTURE_SOURCE_INVALID",
        updated_at="2026-10-21T00:00:00Z",
    )
    enriched = projection.enrich(("org", "project", "region"), progress)
    assert enriched.last_error_detail == "字段缺失"
    errors.for_source.assert_called_once_with(
        scope=("org", "project", "region"), raw_source_id="raw"
    )
    errors.for_source.return_value = ProcessingError("OLD_FAILURE", "old")
    assert projection.enrich(("org", "project", "region"), progress).last_error_detail is None
