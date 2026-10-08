"""Published evidence rule sets must never grow with the current code catalog.

These identifiers describe archived evidence, not the latest engine's plugins.
New rules require a new engine/evidence registration; old registrations retain
their exact content and use the same exact-version registry as other capabilities.
"""

from dataclasses import dataclass

from hc_data_platform.capabilities.registry import (
    CapabilityDescriptor,
    ImplementationRef,
    VersionedRegistry,
)

EVIDENCE_VERSION = "hc-qc-rule-evidence/1"
_KEY = "qc-evidence.v1"

# Literal historical vocabulary: never derive these from QualityCode iteration
# or a mutable algorithm/policy's current list of supported rules.
BASE_RULE_CODES = frozenset(
    {
        "QC_REQUIRED_TOPIC_MISSING",
        "QC_TIMESTAMP_DUPLICATE",
        "QC_TIMESTAMP_BACKWARD",
        "QC_FREQUENCY_LOW",
        "QC_GAP_EXCESSIVE",
        "QC_CONSECUTIVE_FRAMES_MISSING",
        "QC_COVERAGE_LOW",
        "QC_IMAGE_BLACK",
        "QC_IMAGE_REPEATED",
        "QC_IMAGE_CORRUPT",
        "QC_JOINT_OUT_OF_RANGE",
        "QC_ACTION_MISSING",
        "QC_ACTION_JUMP",
        "QC_POINT_CLOUD_EMPTY",
        "QC_POINT_COUNT_ABNORMAL",
        "QC_MODALITY_OFFSET",
        "QC_COMPLETE_STEP_RATIO_LOW",
    }
)
BOUNDARY_RULE_CODES = BASE_RULE_CODES | frozenset(
    {
        "QC_LEADING_IDLE",
        "QC_TRAILING_IDLE",
        "QC_NO_COMMON_WINDOW",
    }
)


@dataclass(frozen=True)
class EvidenceRuleSet:
    engine_version: str
    codes: frozenset[str]


class EvidenceRuleSets:
    def __init__(self, entries: tuple[EvidenceRuleSet, ...]) -> None:
        self._registry: VersionedRegistry[EvidenceRuleSet] = VersionedRegistry()
        for entry in entries:
            if not entry.codes or not isinstance(entry.codes, frozenset):
                raise ValueError("evidence rules must be a nonempty immutable set")
            self._registry.register(
                CapabilityDescriptor(ImplementationRef(_KEY, entry.engine_version)), entry
            )

    def required(self, evidence_version: str, engine_version: str) -> frozenset[str]:
        if evidence_version != EVIDENCE_VERSION:
            raise ValueError("unsupported evidence format")
        return self._registry.select(
            _KEY, version=engine_version, available_inputs=frozenset()
        ).implementation.codes


ARCHIVED_RULE_SETS = (
    EvidenceRuleSet("be06-qc/1", BASE_RULE_CODES),
    EvidenceRuleSet("be06-qc/2", BASE_RULE_CODES),
    EvidenceRuleSet("be06-qc/3", BOUNDARY_RULE_CODES),
)
BUILTIN_EVIDENCE_RULE_SETS = EvidenceRuleSets(ARCHIVED_RULE_SETS)
