from dataclasses import replace

import pytest
from temporalio.testing import ActivityEnvironment

from hc_data_platform.alignment.models import ModalityKind, ModalityStreamV1, TimedSampleV1
from hc_data_platform.lance_catalog.service import InMemoryLanceCatalog
from hc_data_platform.quality.engine import QualityEngine
from hc_data_platform.quality.models import (
    QualityProfileV1,
    QualityStatus,
    QualityStreamObservationV1,
)
from hc_data_platform.workflow import activities
from hc_data_platform.workflow.models import IngestSourceProcessingActivityInput
from tests.workflow.test_temporal_workflows import (
    StaticProjection,
    StaticQuality,
    StaticVerifier,
    _dependencies,
    _ingest_input,
    _schema,
)


@pytest.mark.asyncio
async def test_shared_ingest_trims_all_modalities_and_consumes_raw_audit_stream():
    consumed = []

    class Session(StaticProjection.Session):
        def __init__(self, source):
            super().__init__(source)
            self.quality_data = self.quality_data.model_copy(update={"end_ns": 1_000_000_000})
            self.alignment_data = self.alignment_data.model_copy(
                update={
                    "end_ns": 1_000_000_000,
                    "streams": {
                        name: ModalityStreamV1(kind=ModalityKind.CONTINUOUS, samples=())
                        for name in ("x", "y")
                    },
                }
            )

        def samples(self):
            for index in range(30):
                yield (
                    "x",
                    TimedSampleV1(timestamp_ns=index * 1_000_000_000 // 30, value=float(index)),
                )
                if 6 <= index < 24:
                    yield (
                        "y",
                        TimedSampleV1(timestamp_ns=index * 1_000_000_000 // 30, value=float(index)),
                    )

        def quality_observations(self):
            return (
                QualityStreamObservationV1(topic=topic, timestamp_ns=sample.timestamp_ns)
                for topic, sample in self.samples()
            )

        def alignment_samples(self):
            yield from self.samples()
            consumed.append(True)

    class Projection(StaticProjection):
        def open_local_session(self, source):
            return Session(source)

    catalog = InMemoryLanceCatalog()
    catalog.register_schema(_schema())
    dependencies, writers = _dependencies(
        verifier=StaticVerifier(), quality=StaticQuality(QualityStatus.PASS), catalog=catalog
    )
    activities.configure_activity_dependencies(
        replace(dependencies, quality=QualityEngine(), ingest_projection=Projection())
    )
    request = _ingest_input()
    profile = QualityProfileV1(
        profile_id="boundaries",
        engine_version="be06-qc/3",
        required_topics={"x", "y"},
        default_timing={"minimum_frequency_hz_risk": 0, "minimum_frequency_hz_reject": 0},
        action={"minimum_observation_count_risk": 0, "minimum_observation_count_reject": 0},
    )
    result = await ActivityEnvironment().run(
        activities.process_ingest_source,
        IngestSourceProcessingActivityInput(
            verification=request.verification,
            quality=request.quality.model_copy(update={"profile": profile}),
            alignment=request.alignment.model_copy(
                update={
                    "profile": request.alignment.profile.model_copy(
                        update={"required_modalities": frozenset({"x", "y"})}
                    )
                }
            ),
        ),
    )
    assert result.quality.report.status == QualityStatus.PASS
    assert result.alignment.staged_manifest.row_count == 18
    rows = next(iter(writers.by_attempt.values())).rows
    assert rows[0].timestamp_ns == 200_000_000 and rows[0].step_index == 0
    assert rows[-1].timestamp_ns < 800_000_000
    assert all(row.sample_valid and set(row.modalities) == {"x", "y"} for row in rows)
    assert consumed == [True]
