from types import SimpleNamespace

import pytest
from fastapi import Response

from hc_data_platform.annotation.delivery_queries import AnnotationDelivery
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.robot_ingest import processing_api
from hc_data_platform.robot_ingest.episode_delivery import EpisodeDeliveryProjection
from hc_data_platform.robot_ingest.models import RobotIngestEpisodeResult
from hc_data_platform.robot_ingest.processing_contract import (
    EpisodeProgress,
    EpisodeReceipt,
    ProcessingDocument,
)

from .test_robot_ingest_service import (
    NOW,
    ORG,
    PROJECT_A,
    PROJECT_B,
    REGION,
    Harness,
    _admin,
    _complete_first_asset,
    _manifest,
)


@pytest.mark.parametrize("annotation_status", ["PENDING_CONFIGURATION", "FAILED", "UNKNOWN"])
def test_annotation_read_updates_without_rewriting_data_receipt(annotation_status):
    harness = Harness()
    upload_id = _complete_first_asset(harness, _manifest())
    upload = harness.service.commit_upload(token=harness.token, upload_id=upload_id).data
    item = RobotIngestEpisodeResult(
        episode_id="ep",
        source_episode_index=0,
        status="READY",
        quality_status="PASS",
        frame_count=167,
        sample_count=167,
        dataset_version=1,
        lance_version=1,
        qc_report_id="qc",
        created_at=NOW,
        updated_at=NOW,
    )
    harness.service.apply_processing_result(
        organization_id=ORG,
        upload_id=upload_id,
        ingest_identity_id=harness.identity_id,
        episode_results=(item,),
    )
    receipt = EpisodeReceipt(
        quality_status="PASS",
        frame_count=167,
        sample_count=167,
        dataset_version=1,
        lance_version=1,
        qc_report_id="qc",
        annotation_status=annotation_status,
    )
    document = ProcessingDocument(
        phase="DONE",
        episodes=(
            EpisodeProgress(
                source_episode_id="0",
                source_episode_index=0,
                episode_id="ep",
                status="READY",
                quality_status="PASS",
                receipt=receipt,
            ),
        ),
    )
    deliveries = []
    reads = []

    def annotation_read(**kwargs):
        reads.append(kwargs)
        return tuple(deliveries)

    projection = EpisodeDeliveryProjection(
        SimpleNamespace(read=lambda _: (None, document)),
        SimpleNamespace(for_episodes=annotation_read),
    )
    harness.service.episode_deliveries = projection

    def read(project=PROJECT_A):
        return harness.service.list_upload_episode_results(
            auth=_admin(),
            organization_id=ORG,
            project_id=project,
            region_code=REGION,
            upload_id=upload_id,
        )

    original = document.model_dump_json()
    first = read().items[0]
    assert first.data_readable and first.annotation_status == annotation_status

    def robot_read():
        return harness.service.get_robot_episode_deliveries(
            token=harness.token, upload_id=upload_id
        )

    assert robot_read()[0] == first
    # An annotation for a different data version must not mask this version's failure.
    deliveries.append(AnnotationDelivery("ep", 2, 2, None, "APPROVED"))
    assert read().items[0].annotation_status == annotation_status
    deliveries.append(AnnotationDelivery("ep", 1, 1, None, "DRAFT"))
    assert read().items[0].annotation_status == "CREATED"
    deliveries[-1] = AnnotationDelivery("ep", 1, 1, None, "APPROVED")
    assert read().items[0].annotation_status == "APPROVED"
    assert robot_read()[0].annotation_status == "APPROVED"
    assert document.model_dump_json() == original
    assert harness.repository.list_episode_results(
        organization_id=ORG,
        project_id=PROJECT_A,
        region_code=REGION,
        upload_id=upload_id,
    ) == (item,)
    count = len(reads)
    with pytest.raises(ProblemException) as cross_project:
        read(PROJECT_B)
    assert cross_project.value.problem.status == 404 and len(reads) == count
    with pytest.raises(ProblemException):
        harness.service.get_robot_episode_deliveries(token="invalid", upload_id=upload_id)
    assert len(reads) == count
    document = document.model_copy(
        update={
            "episodes": (
                document.episodes[0].model_copy(
                    update={"status": "DISCARDED", "receipt": None},
                ),
            )
        }
    )
    assert projection.project(upload, (item,)) == ()
    assert robot_read() == ()


@pytest.mark.parametrize("version,expected", [(1, "APPROVED"), (2, "PENDING_CONFIGURATION")])
def test_robot_processing_poll_projects_only_the_exact_data_receipt(monkeypatch, version, expected):
    from hc_data_platform.robot_ingest.episode_delivery import project_episode

    from .test_processing_contract import upload as processing_upload

    receipt = EpisodeReceipt(
        quality_status="PASS",
        frame_count=167,
        sample_count=167,
        dataset_version=1,
        lance_version=1,
        qc_report_id="qc",
        annotation_status="PENDING_CONFIGURATION",
    )
    document = ProcessingDocument(
        phase="DONE",
        episodes=(
            EpisodeProgress(
                source_episode_id="0",
                source_episode_index=0,
                episode_id="ep",
                status="READY",
                quality_status="PASS",
                receipt=receipt,
            ),
        ),
    )
    delivery = project_episode(
        RobotIngestEpisodeResult(
            episode_id="ep",
            source_episode_index=0,
            status="READY",
            quality_status="PASS",
            frame_count=167,
            sample_count=167,
            dataset_version=version,
            lance_version=version,
            qc_report_id="qc",
            created_at=NOW,
            updated_at=NOW,
        ),
        "APPROVED",
    )
    # This unit isolates the read projection. The service test above exercises
    # real credential ownership and scoped repository reads before projection.
    monkeypatch.setattr(processing_api, "owned", lambda *args: processing_upload())
    result = processing_api.get_processing(
        "upload",
        Response(),
        "token",
        SimpleNamespace(get_robot_episode_deliveries=lambda **kwargs: (delivery,)),
        SimpleNamespace(read=lambda _: (object(), document)),
    )
    assert result.episodes[0].annotation_status == expected
    assert result.episodes[0].data_readable
    assert document.episodes[0].receipt.annotation_status == "PENDING_CONFIGURATION"
