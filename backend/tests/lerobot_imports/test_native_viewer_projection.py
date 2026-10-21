from datetime import datetime, timedelta, timezone
from fractions import Fraction
from math import ceil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from hc_data_platform.dataset_registry.projection_writer import DatasetProjectionWriter
from hc_data_platform.lance_catalog.models import DerivedReadyV1, StepRecord, StepWindow
from hc_data_platform.lerobot_imports.pipeline import LeRobotPipeline
from hc_data_platform.workflow.models import IngestProjectionSourceV1


@pytest.mark.parametrize("fps, frames", [(15, 167), (30, 7), (60, 113), (29.97, 301), (25, 6)])
@pytest.mark.parametrize("native", [False, True])
def test_native_committed_values_preserve_numeric_viewer_types(
    tmp_path: Path, fps, frames, native
) -> None:
    """Streaming metadata without samples must not turn G1 vectors into events."""
    values = {
        "/joint_states": {"names": ["hip"], "positions": [0.25]},
        "/robot/action/joints": {"names": ["hip"], "values": [0.5]},
        "/robot/end_effector/state": {
            "position_xyz": [0, 1, 2],
            "orientation_wxyz": [1, 0, 0, 0],
        },
        "/metadata/source": {"source_format": "lerobot"},
    }
    source = IngestProjectionSourceV1(
        organization_id="org",
        project_id="project",
        region_code="local",
        session_id="session",
        rollout_id="rollout",
        data_package_id="dataset",
        object_key="raw/manifest.json",
        manifest_key="episode/manifest.json",
        manifest_fingerprint="a" * 64,
        source_sha256="b" * 64,
    )
    catalog = Mock()
    catalog.read_steps.return_value = StepWindow(
        project_id="project",
        dataset_id="dataset",
        dataset_version=2,
        rollout_id="rollout",
        start_step=0,
        end_step=1,
        steps=(StepRecord(rollout_id="rollout", step_index=0, timestamp_ns=0, modalities=values),),
    )
    native_catalog = Mock()
    native_catalog.read_steps.return_value = catalog.read_steps.return_value
    pipeline = LeRobotPipeline(
        Mock(),
        catalog,
        Mock(),
        tmp_path,
        raw_sources=Mock(),
        state=Mock(),
        registrations=Mock(),
        planning=Mock(),
        quality_profiles=Mock(),
        native_schemas=Mock(),
        native_catalog=native_catalog,
    )
    start = datetime(1970, 1, 1, tzinfo=timezone.utc)
    end_ns = int(Fraction(frames * 10**9) / Fraction(str(fps)))
    preflight = SimpleNamespace(
        manifest=SimpleNamespace(
            cameras=(),
            actual_topics=tuple(values),
            start_time=start,
            end_time=start + timedelta(microseconds=(end_ns + 999) // 1000),
        )
    )
    with patch("hc_data_platform.lerobot_imports.pipeline.ObjectStorageManifestParser") as parser:
        parser.return_value.parse.return_value = preflight
        if native:
            with pytest.raises(ValueError, match="requires the frozen source timeline"):
                pipeline.alignment_metadata(
                    source, dataset_id="dataset", dataset_version=2, storage_format="lerobot_v3"
                )
            native_catalog.read_steps.assert_not_called()
        metadata = pipeline.alignment_metadata(
            source,
            dataset_id="dataset",
            dataset_version=2,
            **(
                {"storage_format": "lerobot_v3", "source_time_range": (0, end_ns)} if native else {}
            ),
        )
        if native:
            assert metadata.end_ns == end_ns
            assert ceil(Fraction(metadata.end_ns, 10**9) * Fraction(str(fps))) == frames
            for invalid_range in (
                (0, end_ns - 10**6),
                (1, end_ns),
                (0, ((end_ns + 999) // 1000) * 1000 + 1),
                (0, 0),
            ):
                with pytest.raises(ValueError, match="timeline differs"):
                    pipeline.alignment_metadata(
                        source,
                        dataset_id="dataset",
                        dataset_version=2,
                        storage_format="lerobot_v3",
                        source_time_range=invalid_range,
                    )

    ready = DerivedReadyV1(
        project_id="project",
        dataset_id="dataset",
        rollout_id="rollout",
        source_sha256=source.source_sha256,
        converter_version="native/1",
        dataset_version=2,
        lance_version=2,
        step_count=frames,
        content_hash="c" * 64,
    )
    streams = DatasetProjectionWriter._streams(
        alignment=metadata,
        camera_topics=frozenset(),
        ready=ready,
        frequency_hz=fps,
        media_artifacts=(),
    )
    projected = {stream.channel_path: stream for stream in streams}
    assert {topic: stream.kind for topic, stream in projected.items()} == {
        "/joint_states": "JOINT_STATE",
        "/robot/action/joints": "JOINT_STATE",
        "/robot/end_effector/state": "POSE",
        "/metadata/source": "EVENT",
    }
    for topic, stream in projected.items():
        assert stream.data_binding is not None
        assert stream.data_binding.value_kind == (
            "EVENT" if topic == "/metadata/source" else "VECTOR"
        )
    (native_catalog if native else catalog).read_steps.assert_called_once_with(
        "dataset", "rollout", 0, 1, project_id="project", version=2
    )
    if native:
        native_catalog.read_steps.return_value = catalog.read_steps.return_value.model_copy(
            update={
                "steps": (
                    catalog.read_steps.return_value.steps[0].model_copy(
                        update={"timestamp_ns": 1}
                    ),
                )
            }
        )
        with patch(
            "hc_data_platform.lerobot_imports.pipeline.ObjectStorageManifestParser"
        ) as parser:
            parser.return_value.parse.return_value = preflight
            with pytest.raises(ValueError, match="committed first Step"):
                pipeline.alignment_metadata(
                    source,
                    dataset_id="dataset",
                    dataset_version=2,
                    storage_format="lerobot_v3",
                    source_time_range=(0, end_ns),
                )
