from unittest.mock import Mock

import pytest

from hc_data_platform.capabilities.registry import CapabilityDescriptor, ImplementationRef
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.publishing.contracts import FROZEN_MANIFEST
from hc_data_platform.publishing.exporters import LanceSnapshotExporter
from hc_data_platform.publishing.memory import InMemoryArtifactSink
from hc_data_platform.publishing.models import PublishedDatasetManifestV1
from hc_data_platform.publishing.ports import ExportSourcePort
from hc_data_platform.publishing.service import ExportCoordinator
from tests.publishing.test_publishing_service import export_manifest


class MetadataPreflight(LanceSnapshotExporter):
    """Alternate export policy checks manifest facts before reading rows later."""

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return CapabilityDescriptor(
            ImplementationRef(self.format.value, "metadata-preflight/v1"),
            frozenset({FROZEN_MANIFEST}),
        )

    def preflight(self, *, manifest: PublishedDatasetManifestV1, source: ExportSourcePort) -> int:
        return sum(
            interval.end_step - interval.start_step
            for rollout in manifest.rollouts
            for interval in rollout.included_step_ranges
        )


def test_coordinator_routes_preflight_without_forcing_step_collection() -> None:
    source = Mock(spec=ExportSourcePort)
    source.read_steps.side_effect = AssertionError("metadata preflight cannot read step data")
    exporter = MetadataPreflight()
    coordinator = ExportCoordinator(
        source=source, sink=InMemoryArtifactSink(), exporters=[exporter]
    )
    assert coordinator.preflight(export_manifest(), format=exporter.format) == 4
    assert coordinator.descriptors() == (exporter.descriptor,)
    source.read_steps.assert_not_called()


def test_unsupported_input_stops_before_any_exporter_or_source_work() -> None:
    class RequiresAudio(MetadataPreflight):
        @property
        def descriptor(self) -> CapabilityDescriptor:
            return CapabilityDescriptor(
                ImplementationRef(self.format.value, "audio/v1"), frozenset({"audio-samples/v1"})
            )

    exporter = RequiresAudio()
    source = Mock(spec=ExportSourcePort)
    sink = InMemoryArtifactSink()
    coordinator = ExportCoordinator(source=source, sink=sink, exporters=[exporter])
    with pytest.raises(ProblemException) as captured:
        coordinator.preflight(export_manifest(), format=exporter.format)
    assert captured.value.problem.code == "EXPORT_INPUT_CAPABILITY_UNSUPPORTED"
    assert captured.value.problem.details == {"missing_capabilities": ["audio-samples/v1"]}
    source.read_steps.assert_not_called()
    assert sink.artifacts == {} and sink.attempts == {}


def test_two_defaults_for_one_format_are_rejected_at_composition() -> None:
    with pytest.raises(ValueError, match="duplicate default"):
        ExportCoordinator(
            source=Mock(spec=ExportSourcePort),
            sink=InMemoryArtifactSink(),
            exporters=[LanceSnapshotExporter(), MetadataPreflight()],
        )
