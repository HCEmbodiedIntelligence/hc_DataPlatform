"""Exercise parallel preparation and version-safe publication through Temporal."""

from __future__ import annotations

import asyncio
import io
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from hc_data_platform.aligned_media.artifact_store import LocalAlignedMediaArtifactStore
from hc_data_platform.aligned_media.encoder import FFmpegMp4Encoder
from hc_data_platform.aligned_media.memory import InMemoryAlignedMediaRepository
from hc_data_platform.aligned_media.models import OriginalVideoReferenceV1
from hc_data_platform.aligned_media.profiles import AlignedMediaProfileCatalog
from hc_data_platform.aligned_media.service import AlignedMediaGenerationService
from hc_data_platform.lance_catalog.service import InMemoryLanceCatalog
from hc_data_platform.lerobot_imports.orchestration import LeRobotEpisodeSourceRefV1
from hc_data_platform.quality.models import QualityStatus
from hc_data_platform.workflow.activities import ALL_ACTIVITIES, configure_activity_dependencies
from hc_data_platform.workflow.ingest_guard import IngestCommitPending
from hc_data_platform.workflow.models import IngestRolloutWorkflowInput, JobStatus
from hc_data_platform.workflow.temporal_workflows import IngestRolloutWorkflow
from tests.aligned_media.test_aligned_media_service import Frame
from tests.workflow.test_temporal_workflows import (
    StaticDatasetIngestProjection,
    StaticProjection,
    StaticQuality,
    StaticVerifier,
    _dependencies,
    _ingest_input,
    _manifest_preflight,
    _schema,
    _verification_report,
)


class DispatchGuard:
    def __init__(self):
        self.reserved = set()

    def reserve(self, request, workflow_id):
        self.reserved.add(workflow_id)


class CommitGuard:
    def __init__(self):
        self.lock = threading.Lock()
        self.waits = 0

    @contextmanager
    def acquire(self, scope, dataset_id, rollout_id):
        if not self.lock.acquire(blocking=False):
            self.waits += 1
            raise IngestCommitPending("another publication is in progress")
        try:
            yield
        finally:
            self.lock.release()


def request_for(rollout):
    value = _ingest_input().model_dump(mode="json")
    preflight = _manifest_preflight(rollout)

    def rename(item):
        if isinstance(item, dict):
            return {key: rename(v) for key, v in item.items()}
        if isinstance(item, list):
            return [rename(v) for v in item]
        return item.replace("r1", rollout) if isinstance(item, str) else item

    value = rename(value)
    for section in (value["manifest"], value["quality"]["source"], value["alignment"]["source"]):
        section["manifest_fingerprint"] = preflight.manifest_fingerprint
    for section in (
        value["verification"],
        value["quality"]["source"],
        value["alignment"]["source"],
    ):
        section["object_key"] = "raw/r1.mcap"
    value["parallel_preparation"] = True
    return IngestRolloutWorkflowInput.model_validate(value)


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True])
async def test_parallel_preparation_serializes_only_publication_and_recovers_receipt(
    tmp_path, native
):
    """Two files overlap before either can commit; retries never encode twice."""
    barrier = threading.Barrier(2)
    preparing = set()
    catalog = InMemoryLanceCatalog()
    catalog.register_schema(_schema())
    dependencies, _ = _dependencies(
        verifier=StaticVerifier(),
        quality=StaticQuality(QualityStatus.PASS),
        catalog=catalog,
    )
    original = (
        OriginalVideoReferenceV1(
            object_key="raw/shared.mp4",
            size_bytes=5,
            content_sha256="a" * 64,
            start_seconds=0,
            end_seconds=1 / 30,
            width=16,
            height=16,
            fps=30,
            codec="h264",
        )
        if native
        else None
    )

    class Session(StaticProjection.Session):
        def __enter__(self):
            preparing.add(self.source.rollout_id)
            barrier.wait(timeout=10)
            return self

        @property
        def original_videos(self):
            return {"/camera/front/image": original} if original else {}

        @property
        def verification_report(self):
            return _verification_report(rollout_id=self.source.rollout_id)

    class Projection(StaticProjection):
        def open_local_session(self, source):
            return Session(source)

        def open_session(self, source):
            return Session(source)

        def alignment_metadata(self, source, **kwargs):
            return self.project_alignment_metadata(source)

    class Reader:
        def read_camera_frames(self, request):
            image = io.BytesIO()
            Image.new("RGB", (16, 16), "blue").save(image, format="JPEG")
            return [Frame(0, 0, image.getvalue())]

    class Encoder(FFmpegMp4Encoder):
        calls = 0

        def encode(self, **kwargs):
            self.calls += 1
            return super().encode(**kwargs)

    class Projector(StaticDatasetIngestProjection):
        calls = 0

        def project(self, **kwargs):
            self.calls += 1
            # Simulate a crash after catalog commit, before product projection.
            if self.calls == 1:
                raise OSError("publication process exited after immutable commit")
            time.sleep(0.1)
            return super().project(**kwargs)

    encoder = Encoder(tmp_path / "encoder")
    raw = tmp_path / "objects/raw/shared.mp4"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"video")
    repo = InMemoryAlignedMediaRepository()
    media = AlignedMediaGenerationService(
        frame_reader=Reader(),
        encoder=encoder,
        repository=repo,
        store=LocalAlignedMediaArtifactStore(tmp_path / "objects"),
        profiles=AlignedMediaProfileCatalog(
            ("canonical-h264-crf20-v1", "original-video-reference-v1")
        ),
    )
    dispatch, commit = DispatchGuard(), CommitGuard()
    configure_activity_dependencies(
        replace(
            dependencies,
            ingest_dispatch_guard=dispatch,
            ingest_commit_guard=commit,
            ingest_projection=Projection(),
            lerobot_pipeline=Projection(),
            aligned_media=media,
            aligned_media_repository=repo,
            dataset_ingest_projection=Projector(),
        )
    )
    requests = [request_for(f"parallel-{i}") for i in range(2)]
    if native:
        requests = [
            r.model_copy(
                update={
                    "quality": r.quality.model_copy(
                        update={
                            "source": r.quality.source.model_copy(
                                update={
                                    "lerobot": LeRobotEpisodeSourceRefV1(
                                        raw_upload_id="a" * 32,
                                        raw_manifest_key="raw/native.json",
                                        episode_index=i,
                                    ),
                                }
                            )
                        }
                    ),
                    "alignment": r.alignment.model_copy(
                        update={
                            "source": r.alignment.source.model_copy(
                                update={
                                    "lerobot": LeRobotEpisodeSourceRefV1(
                                        raw_upload_id="a" * 32,
                                        raw_manifest_key="raw/native.json",
                                        episode_index=i,
                                    ),
                                }
                            )
                        }
                    ),
                }
            )
            for i, r in enumerate(requests)
        ]
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as environment, Worker(
        environment.client,
        task_queue="workflow-tests",
        workflows=[IngestRolloutWorkflow],
        activities=list(ALL_ACTIVITIES),
    ):
        handles = [
            await environment.client.start_workflow(
                IngestRolloutWorkflow.run,
                r,
                id=f"parallel-{i}",
                task_queue="workflow-tests",
            )
            for i, r in enumerate(requests)
        ]
        results = await asyncio.wait_for(asyncio.gather(*(h.result() for h in handles)), 45)
        for handle in handles:
            await Replayer(
                workflows=[IngestRolloutWorkflow], data_converter=pydantic_data_converter
            ).replay_workflow(await handle.fetch_history())
    assert preparing == {"parallel-0", "parallel-1"}
    assert len(dispatch.reserved) == 2
    assert all(job.status is JobStatus.SUCCEEDED for job in results), results
    assert sorted(job.result["derived"]["dataset_version"] for job in results) == [1, 2]
    assert [v.version for v in catalog.list_versions("d1", project_id="p1")] == [1, 2]
    assert encoder.calls == (0 if native else 2)
    assert not list(Path(dependencies.alignment_staging._root).rglob("media.mp4"))
