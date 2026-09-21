"""The explicitly approved QC risks; new rules require a policy revision."""

from collections.abc import Iterable

from .models import (
    CURRENT_QUALITY_ENGINE_VERSION,
    FindingSeverity,
    QcFinding,
    QcReportV1,
    QualityCode,
    QualityProfileV1,
    QualityStatus,
)

LEGACY_QUALITY_ENGINE_VERSION = "be06-qc/1"

# Six business categories, with separate codes for the timestamp/action cases.
APPROVED_RISK_CODES = frozenset(
    {
        QualityCode.IMAGE_CORRUPT,
        QualityCode.FREQUENCY_LOW,
        QualityCode.ACTION_MISSING,
        QualityCode.ACTION_JUMP,
        QualityCode.CONSECUTIVE_FRAMES_MISSING,
        QualityCode.TIMESTAMP_DUPLICATE,
        QualityCode.TIMESTAMP_BACKWARD,
        QualityCode.REQUIRED_TOPIC_MISSING,
    }
)


def approved_risk_findings(
    findings: Iterable[QcFinding],
    profile: QualityProfileV1,
) -> list[QcFinding]:
    result = []
    for finding in findings:
        if finding.code not in APPROVED_RISK_CODES:
            continue
        timing = profile.timing_for(finding.topic)
        threshold = finding.threshold
        if finding.code is QualityCode.FREQUENCY_LOW:
            threshold = timing.minimum_frequency_hz_risk
        elif finding.code is QualityCode.CONSECUTIVE_FRAMES_MISSING:
            threshold = timing.maximum_consecutive_missing_risk
        elif finding.code is QualityCode.TIMESTAMP_DUPLICATE:
            threshold = timing.maximum_duplicate_timestamps_risk
        elif finding.code is QualityCode.IMAGE_CORRUPT:
            threshold = profile.image_for(finding.topic).maximum_corrupt_frame_ratio_risk
        elif finding.code is QualityCode.ACTION_MISSING:
            threshold = profile.action.minimum_observation_count_risk
        elif (
            finding.code is QualityCode.ACTION_JUMP
            and finding.observed != "dimension_mismatch"
            and profile.action.maximum_jump_risk is not None
        ):
            threshold = profile.action.maximum_jump_risk
        result.append(
            finding.model_copy(
                update={
                    "severity": FindingSeverity.WARNING,
                    "threshold": threshold,
                }
            )
        )
    return result


def reclassify_report(
    report: QcReportV1,
    previous: QualityProfileV1,
    current: QualityProfileV1,
) -> QcReportV1:
    """Reapply the narrower policy to recorded evidence without replacing history."""
    if (
        report.profile_id != previous.profile_id
        or report.profile_version != previous.profile_version
        or report.profile_sha256 != previous.content_sha256()
        or report.engine_version != previous.engine_version
        or current.profile_id != previous.profile_id
        or current.profile_version <= previous.profile_version
        or current.engine_version != CURRENT_QUALITY_ENGINE_VERSION
        or previous.model_dump(exclude={"profile_version", "engine_version"})
        != current.model_dump(exclude={"profile_version", "engine_version"})
    ):
        raise ValueError("policy reclassification requires the same evidence and thresholds")
    findings = approved_risk_findings(report.findings, current)
    return QcReportV1.build(
        rollout_id=report.rollout_id,
        source_sha256=report.source_sha256,
        profile_id=current.profile_id,
        profile_version=current.profile_version,
        profile_sha256=current.content_sha256(),
        engine_version=current.engine_version,
        start_ns=report.start_ns,
        end_ns=report.end_ns,
        status=QualityStatus.RISK if findings else QualityStatus.PASS,
        topic_metrics=report.topic_metrics,
        findings=tuple(findings),
    )
