"""Owned input capability names and implementation revisions for export."""

from hc_data_platform.capabilities.registry import CapabilityDescriptor, ImplementationRef

FROZEN_MANIFEST = "frozen-manifest/v1"
SYNCHRONIZED_STEPS = "synchronized-steps/v1"
LEROBOT_EXPORT_REVISION = "lerobot-materialized-v4-holobrain"


def step_export_descriptor(key: str, version: str) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        ref=ImplementationRef(key, version),
        required_inputs=frozenset({FROZEN_MANIFEST, SYNCHRONIZED_STEPS}),
        outputs=frozenset({"portable-archive/v1"}),
    )
