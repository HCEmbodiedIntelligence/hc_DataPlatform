from hc_data_platform.annotation.models import (
    AnnotationActor,
    AnnotationOperation,
    OperationKind,
)
from hc_data_platform.annotation.service import InMemoryAnnotationService
from hc_data_platform.lance_catalog.models import StepRecord
from hc_data_platform.lance_catalog.ports import FakeStepReader
from hc_data_platform.preview.adapters import (
    AnnotationExclusionAdapter,
    LanceStepReaderAdapter,
    S3ImageRefResolver,
)


def test_lance_adapter_consumes_step_reader_window_and_camera_fields() -> None:
    records = [
        StepRecord(
            rollout_id="rollout-1",
            step_index=index,
            timestamp_ns=index * 10,
            modalities={"camera.front": f"file:///frames/{index}.png"},
            source_timestamps_ns={"camera.front": index * 10 + 1},
            valid={"camera.front": index != 1},
        )
        for index in range(3)
    ]
    adapter = LanceStepReaderAdapter(
        FakeStepReader("dataset-1", 7, records, project_id="project-1")
    )

    frames = adapter.read_steps(
        project_id="project-1",
        dataset_id="dataset-1",
        rollout_id="rollout-1",
        lance_version="v7",
        camera_id="front",
        start_step=0,
        end_step=3,
    )

    assert [frame.step_index for frame in frames] == [0, 1, 2]
    assert frames[0].image_ref == "file:///frames/0.png"
    assert frames[0].source_timestamp_ns == 1
    assert frames[1].valid is False
    assert frames[1].invalid_reason == "camera.front is invalid"


def test_lance_adapter_pages_an_open_ended_preview_without_a_huge_provider_range() -> None:
    records = [
        StepRecord(
            rollout_id="rollout-1",
            step_index=index,
            timestamp_ns=index,
            modalities={"camera.front": f"frame-{index}.png"},
        )
        for index in range(5)
    ]
    adapter = LanceStepReaderAdapter(
        FakeStepReader("dataset-1", 3, records, project_id="project-1"),
        page_size=2,
    )

    frames = adapter.read_steps(
        project_id="project-1",
        dataset_id="dataset-1",
        rollout_id="rollout-1",
        lance_version="3",
        camera_id="front",
        start_step=None,
        end_step=None,
    )

    assert [frame.step_index for frame in frames] == [0, 1, 2, 3, 4]


def test_lance_adapter_preserves_embedded_image_bytes_for_ffmpeg() -> None:
    image = b"\x89PNG\r\n\x1a\nembedded"
    adapter = LanceStepReaderAdapter(
        FakeStepReader(
            "dataset-1",
            3,
            [
                StepRecord(
                    rollout_id="rollout-1",
                    step_index=0,
                    timestamp_ns=0,
                    modalities={"camera.front": image},
                )
            ],
            project_id="project-1",
        )
    )

    frame = adapter.read_steps(
        project_id="project-1",
        dataset_id="dataset-1",
        rollout_id="rollout-1",
        lance_version="3",
        camera_id="front",
        start_step=0,
        end_step=1,
    )[0]

    assert frame.valid is True
    assert frame.image_ref == image


def test_lance_adapter_turns_an_empty_corrupt_camera_sample_into_a_placeholder() -> None:
    adapter = LanceStepReaderAdapter(
        FakeStepReader(
            "dataset-1",
            3,
            [
                StepRecord(
                    rollout_id="rollout-1",
                    step_index=0,
                    timestamp_ns=0,
                    modalities={"camera.front": b""},
                )
            ],
            project_id="project-1",
        )
    )

    frame = adapter.read_steps(
        project_id="project-1",
        dataset_id="dataset-1",
        rollout_id="rollout-1",
        lance_version="3",
        camera_id="front",
        start_step=0,
        end_step=1,
    )[0]

    assert frame.valid is False
    assert frame.image_ref is None
    assert frame.invalid_reason == "camera.front image reference is missing or unsupported"


def test_s3_image_resolver_reads_only_the_configured_bucket_and_closes_response_body() -> None:
    class Body:
        def __init__(self) -> None:
            self.closed = False

        def read(self, amount: int) -> bytes:
            assert amount == 33
            return b"image-bytes"

        def close(self) -> None:
            self.closed = True

    class Client:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []
            self.body = Body()

        def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
            self.calls.append((Bucket, Key))
            return {"ContentLength": 11, "Body": self.body}

    client = Client()
    resolver = S3ImageRefResolver(client, "hc-data", max_object_bytes=32)

    assert resolver("s3://hc-data/cameras/front%20image.ppm") == b"image-bytes"
    assert client.calls == [("hc-data", "cameras/front image.ppm")]
    assert client.body.closed is True
    assert resolver("s3://other-bucket/private.ppm") is None
    assert resolver("s3://hc-data/private.ppm?version=1") is None
    assert client.calls == [("hc-data", "cameras/front image.ppm")]


def test_s3_image_resolver_rejects_oversized_or_unavailable_objects_as_explicit_placeholders() -> (
    None
):
    class Body:
        def read(self, _: int) -> bytes:
            raise AssertionError("oversized response body must not be read")

        def close(self) -> None:
            raise AssertionError("body is not fetched for an oversized object")

    class OversizedClient:
        def get_object(self, **_: object) -> dict[str, object]:
            return {"ContentLength": 33, "Body": Body()}

    resolver = S3ImageRefResolver(OversizedClient(), "hc-data", max_object_bytes=32)
    assert resolver("s3://hc-data/cameras/large.ppm") is None

    adapter = LanceStepReaderAdapter(
        FakeStepReader(
            "dataset-1",
            3,
            [
                StepRecord(
                    rollout_id="rollout-1",
                    step_index=0,
                    timestamp_ns=0,
                    modalities={"camera.front": "s3://hc-data/cameras/large.ppm"},
                )
            ],
            project_id="project-1",
        ),
        image_ref_resolver=resolver,
    )
    frame = adapter.read_steps(
        project_id="project-1",
        dataset_id="dataset-1",
        rollout_id="rollout-1",
        lance_version="3",
        camera_id="front",
        start_step=0,
        end_step=1,
    )[0]

    assert frame.valid is False
    assert frame.invalid_reason == "camera.front image reference is missing or unsupported"


def test_annotation_adapter_consumes_effective_exclusions_at_requested_revision() -> None:
    annotations = InMemoryAnnotationService()
    task = annotations.create_task(
        task_id="task-1",
        project_id="project-1",
        dataset_id="dataset-1",
        dataset_version=7,
        rollout_id="rollout-1",
        base_step_count=10,
    )
    actor = AnnotationActor(
        actor_id="annotator-1",
        roles=frozenset({"annotator"}),
        project_ids=frozenset({"project-1"}),
    )
    task = annotations.claim(task.task_id, actor)
    annotations.save_draft(
        task.task_id,
        actor,
        [
            AnnotationOperation(
                operation_id="exclude-1",
                kind=OperationKind.EXCLUDE,
                start_step=2,
                end_step=5,
            )
        ],
        expected_revision=0,
        if_match=task.etag,
        mutation_id="mutation-1",
    )
    adapter = AnnotationExclusionAdapter(annotations)
    ranges = adapter.effective_ranges(
        project_id="project-1",
        rollout_id="rollout-1",
        annotation_revision=1,
    )

    assert [(item.start_step, item.end_step) for item in ranges] == [(2, 5)]
