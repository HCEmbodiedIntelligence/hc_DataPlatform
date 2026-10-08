"""Execution evidence alongside, never inside, the immutable qc-report/v1 bytes.

This collector is invoked by the evaluator while it consumes the actual input.
It does not infer a pass from an archived report having no findings.
"""

import json
from collections import Counter, defaultdict
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from hc_data_platform.capabilities.registry import ImplementationUnavailable

from .boundaries import BOUNDARY_CODES, BOUNDARY_QUALITY_ENGINE_VERSION
from .models import (
    CURRENT_QUALITY_ENGINE_VERSION,
    QcFinding,
    QcReportV1,
    QualityCode,
    QualityInputV1,
    QualityProfileV1,
    canonical_json_bytes,
)
from .policy import APPROVED_RISK_CODES, LEGACY_QUALITY_ENGINE_VERSION
from .rule_sets import BASE_RULE_CODES, BUILTIN_EVIDENCE_RULE_SETS, EVIDENCE_VERSION


def evidence_rule_codes(engine_version: str) -> frozenset[QualityCode]:
    try:
        codes = BUILTIN_EVIDENCE_RULE_SETS.required(EVIDENCE_VERSION, engine_version)
    except ImplementationUnavailable:
        # v1 historically recorded unsupported engines using the original 17
        # rules, all explicitly marked unsupported by the collector. Preserve
        # those old bytes; this does not select an executable engine fallback.
        codes = BASE_RULE_CODES
    return frozenset(QualityCode(code) for code in codes)


class RuleExecution(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: QualityCode
    topic: str | None
    status: Literal["pass", "warn", "fail", "skip", "info"]
    reason: str | None
    participates_in_verdict: bool | None
    evaluated_count: int = Field(ge=0)
    expected_count: int = Field(ge=0)
    observed: dict[str, bool | int | float | str | None]
    thresholds: dict[str, bool | int | float | str | None]
    detector_findings: tuple[QcFinding, ...]
    report_finding_indexes: tuple[int, ...]

    @model_validator(mode="after")
    def complete_pass(self):
        if self.status == "pass" and (
            self.evaluated_count != self.expected_count
            or self.evaluated_count == 0
            or self.reason is not None
            or not self.participates_in_verdict
            or self.detector_findings
            or self.report_finding_indexes
        ):
            raise ValueError("pass requires complete executed evidence")
        if self.status in {"skip", "info"} and not self.reason:
            raise ValueError("skip/info requires an explicit reason")
        return self


class QcRuleEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    format: Literal["hc-qc-rule-evidence/1"] = EVIDENCE_VERSION
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rollout_id: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    start_ns: int = Field(ge=0)
    end_ns: int = Field(gt=0)
    engine_version: str
    profile: QualityProfileV1
    input_mode: Literal["batch", "stream"]
    rules: tuple[RuleExecution, ...]
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_serializer("profile", when_used="json")
    def serialize_profile(self, profile):
        # Set iteration can change after validation or between worker processes.
        # Freeze the same canonical ordering used by the profile's existing hash.
        return json.loads(profile.content_bytes())

    @model_validator(mode="after")
    def valid(self):
        keys = [(r.code.value, r.topic or "") for r in self.rules]
        if keys != sorted(set(keys)) or {r.code for r in self.rules} != evidence_rule_codes(
            self.engine_version
        ):
            raise ValueError("evidence must contain every rule with unique sorted targets")
        if self.end_ns <= self.start_ns:
            raise ValueError("invalid evidence source window")
        if self.content_sha256 != sha256(self.content_bytes()).hexdigest():
            raise ValueError("rule evidence digest mismatch")
        return self

    def content_bytes(self):
        return canonical_json_bytes(self.model_dump(exclude={"content_sha256"}))

    def validate_report(self, report):
        if (
            self.report_sha256 != report.content_sha256
            or self.rollout_id != report.rollout_id
            or self.source_sha256 != report.source_sha256
            or (self.start_ns, self.end_ns) != (report.start_ns, report.end_ns)
            or self.engine_version != report.engine_version
            or self.profile.content_sha256() != report.profile_sha256
            or self.profile.profile_id != report.profile_id
            or self.profile.profile_version != report.profile_version
        ):
            raise ValueError("rule evidence differs from its frozen QC report/profile")
        indexes = []
        for rule in self.rules:
            if any(f.code != rule.code or f.topic != rule.topic for f in rule.detector_findings):
                raise ValueError("detector finding differs from its rule target")
            for index in rule.report_finding_indexes:
                if not 0 <= index < len(report.findings):
                    raise ValueError("rule references an absent report finding")
                finding = report.findings[index]
                if finding.code != rule.code or finding.topic != rule.topic:
                    raise ValueError("rule references a different finding target")
                indexes.append(index)
            if rule.report_finding_indexes:
                expected = (
                    "fail"
                    if any(
                        report.findings[i].severity.value == "error"
                        for i in rule.report_finding_indexes
                    )
                    else "warn"
                    if any(
                        report.findings[i].severity.value == "warning"
                        for i in rule.report_finding_indexes
                    )
                    else "info"
                )
                if rule.status != expected and rule.reason != "UNSUPPORTED_ENGINE_EVIDENCE":
                    raise ValueError("rule result differs from the report finding severity")
            elif rule.status in {"warn", "fail"}:
                raise ValueError("warning/failure requires its original report finding")
        if sorted(indexes) != list(range(len(report.findings))):
            raise ValueError("evidence must account for every report finding exactly once")


class QcEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    report: QcReportV1
    evidence: QcRuleEvidence

    @model_validator(mode="after")
    def bound(self):
        self.evidence.validate_report(self.report)
        return self


class RuleEvidenceCollector:
    """Bounded per-topic counters; image payloads and streaming timestamps are not retained."""

    def __init__(self, data: QualityInputV1, profile: QualityProfileV1, *, streaming=False):
        self.data, self.profile = data, profile
        self.streaming = streaming
        self.images = {}
        self.seen_topics = set() if streaming else set(data.topic_timestamps_ns)
        self.result = None
        if not streaming:
            for topic, observations in data.images.items():
                for observation in observations:
                    self.image(topic, observation)

    def image(self, topic, observation):
        state = self.images.setdefault(topic, Counter())
        state["frames"] += 1
        state["luma_known"] += observation.luma_mean is not None
        state["black"] += (
            observation.luma_mean is not None
            and observation.luma_mean <= self.profile.image_for(topic).black_luma_threshold
        )
        # An explicit decode result or both decoded image facts establish that
        # corruption was checked. A timestamp-only camera observation does not.
        state["corruption_known"] += (
            "corrupt" in observation.model_fields_set
            or observation.luma_mean is not None
            and bool(observation.fingerprint)
        )
        state["corrupt"] += observation.corrupt
        if state["frames"] > 1:
            state["pairs_known"] += bool(state.get("last_fingerprint")) and bool(
                observation.fingerprint
            )
            state["repeated"] += (
                observation.fingerprint is not None
                and observation.fingerprint == state.get("last_fingerprint")
            )
        state["last_fingerprint"] = observation.fingerprint

    def observe(self, observations):
        for observation in observations:
            self.seen_topics.add(observation.topic)
            if observation.is_camera:
                self.image(observation.topic, observation)
            yield observation

    def finish(self, report, raw_findings):
        rules = []
        profile, data = self.profile, self.data
        raw_by_target, report_by_target = defaultdict(list), defaultdict(list)
        for finding in raw_findings:
            raw_by_target[(finding.code, finding.topic)].append(finding)
        for index, finding in enumerate(report.findings):
            report_by_target[(finding.code, finding.topic)].append(index)
        known_engine = report.engine_version in {
            LEGACY_QUALITY_ENGINE_VERSION,
            CURRENT_QUALITY_ENGINE_VERSION,
            BOUNDARY_QUALITY_ENGINE_VERSION,
        }
        active = (
            evidence_rule_codes(report.engine_version)
            if report.engine_version == LEGACY_QUALITY_ENGINE_VERSION
            else APPROVED_RISK_CODES | {QualityCode.NO_COMMON_WINDOW}
            if report.engine_version == BOUNDARY_QUALITY_ENGINE_VERSION
            else APPROVED_RISK_CODES
        )

        def add(code, topic, count, expected, observed, thresholds, reason=None):
            raw = tuple(raw_by_target.get((code, topic), ()))
            indexes = tuple(report_by_target.get((code, topic), ()))
            participates = known_engine and code in active
            if reason is None and (count == 0 or count < expected):
                reason = "NO_OBSERVATIONS" if count == 0 else "PARTIAL_OBSERVATIONS"
            if not known_engine:
                status, reason = "skip", "UNSUPPORTED_ENGINE_EVIDENCE"
            elif indexes:
                status = (
                    "fail"
                    if any(report.findings[i].severity.value == "error" for i in indexes)
                    else "warn"
                    if any(report.findings[i].severity.value == "warning" for i in indexes)
                    else "info"
                )
                if status == "info":
                    reason = "INFORMATIONAL_FINDING"
            elif reason or count == 0 or count < expected:
                status, reason = (
                    "skip",
                    reason or ("NO_OBSERVATIONS" if count == 0 else "PARTIAL_OBSERVATIONS"),
                )
            elif not participates:
                status, reason = "info", "EXCLUDED_BY_VERDICT_POLICY"
            else:
                status = "pass"
            rules.append(
                RuleExecution(
                    code=code,
                    topic=topic,
                    status=status,
                    reason=reason,
                    participates_in_verdict=participates,
                    evaluated_count=count,
                    expected_count=expected,
                    observed=observed,
                    thresholds=thresholds,
                    detector_findings=raw,
                    report_finding_indexes=indexes,
                )
            )

        C = QualityCode
        for topic in sorted(profile.required_topics):
            add(
                C.REQUIRED_TOPIC_MISSING,
                topic,
                1,
                1,
                {"topic_present": topic in self.seen_topics},
                {"required": True},
            )
        if not profile.required_topics:
            add(C.REQUIRED_TOPIC_MISSING, None, 0, 0, {}, {}, "NO_REQUIRED_TOPICS")
        timing = (
            (C.TIMESTAMP_DUPLICATE, "duplicate_count", "maximum_duplicate_timestamps"),
            (C.TIMESTAMP_BACKWARD, "backward_count", "maximum_backward_timestamps"),
            (C.FREQUENCY_LOW, "actual_frequency_hz", "minimum_frequency_hz"),
            (C.GAP_EXCESSIVE, "maximum_gap_ns", "maximum_gap_ns"),
            (
                C.CONSECUTIVE_FRAMES_MISSING,
                "maximum_consecutive_missing",
                "maximum_consecutive_missing",
            ),
            (C.COVERAGE_LOW, "coverage_ratio", "minimum_coverage_ratio"),
        )
        for metric in report.topic_metrics:
            thresholds = profile.timing_for(metric.topic).model_dump()
            for code, name, prefix in timing:
                count = (
                    metric.message_count
                    if code in {C.TIMESTAMP_DUPLICATE, C.TIMESTAMP_BACKWARD}
                    else 1
                )
                reason = (
                    "INSUFFICIENT_OBSERVATIONS"
                    if code == C.TIMESTAMP_BACKWARD and count < 2
                    else None
                )
                add(
                    code,
                    metric.topic,
                    count,
                    count,
                    {
                        name: getattr(metric, name),
                        "message_count": metric.message_count,
                        "expected_frame_count": metric.expected_frame_count,
                    },
                    {k: v for k, v in thresholds.items() if k.startswith(prefix)},
                    reason,
                )
        if not report.topic_metrics:
            for code, _, _ in timing:
                add(code, None, 0, 0, {}, {})
        image_rules = (
            (C.IMAGE_BLACK, "luma_known", "black", "maximum_black_frame_ratio"),
            (C.IMAGE_REPEATED, "pairs_known", "repeated", "maximum_repeated_frame_ratio"),
            (C.IMAGE_CORRUPT, "corruption_known", "corrupt", "maximum_corrupt_frame_ratio"),
        )
        for topic in sorted(set(self.images) | set(profile.image_profiles)) or [None]:
            state = self.images.get(topic, Counter())
            thresholds = profile.image_for(topic).model_dump()
            for code, known, name, prefix in image_rules:
                total = max(0, state["frames"] - 1) if code == C.IMAGE_REPEATED else state["frames"]
                add(
                    code,
                    topic,
                    state[known],
                    total,
                    {
                        "observed_count": state[name],
                        "frame_count": state["frames"],
                        "denominator": max(1, total),
                    },
                    {
                        k: v
                        for k, v in thresholds.items()
                        if k.startswith(prefix)
                        or (code == C.IMAGE_BLACK and k == "black_luma_threshold")
                    },
                )
        bounded = sum(
            name in profile.joint_limits for joint in data.joints for name in joint.positions
        )
        expected = len(data.joints) * len(profile.joint_limits)
        add(
            C.JOINT_OUT_OF_RANGE,
            profile.joint_topic,
            bounded,
            expected,
            {
                "joint_observations": len(data.joints),
                "configured_joints": len(profile.joint_limits),
            },
            {"reject": profile.joint_out_of_range_is_reject},
            "NO_JOINT_LIMITS" if not profile.joint_limits else None,
        )
        action = profile.action.model_dump()
        add(
            C.ACTION_MISSING,
            profile.action.topic,
            1,
            1,
            {"observation_count": len(data.actions)},
            {k: v for k, v in action.items() if k.startswith("minimum_observation_count")},
            "ACTION_COUNT_NOT_REQUIRED"
            if profile.action.minimum_observation_count_risk == 0
            else None,
        )
        pairs = max(0, len(data.actions) - 1)
        add(
            C.ACTION_JUMP,
            profile.action.topic,
            pairs,
            pairs,
            {"adjacent_pairs": pairs},
            {
                k: v
                for k, v in action.items()
                if k
                not in {
                    "topic",
                    "minimum_observation_count_risk",
                    "minimum_observation_count_reject",
                }
            },
        )
        for topic in sorted(set(data.point_clouds) | set(profile.point_cloud_profiles)) or [None]:
            clouds = data.point_clouds.get(topic, ())
            thresholds = profile.point_cloud_for(topic)
            add(
                C.POINT_CLOUD_EMPTY,
                topic,
                len(clouds),
                len(clouds),
                {"empty_count": sum(item.point_count == 0 for item in clouds)},
                {k: v for k, v in thresholds.model_dump().items() if k.startswith("maximum_empty")},
            )
            add(
                C.POINT_COUNT_ABNORMAL,
                topic,
                len(clouds),
                len(clouds),
                {},
                thresholds.model_dump(),
                "NO_POINT_COUNT_LIMITS"
                if thresholds.minimum_point_count is None and thresholds.maximum_point_count is None
                else None,
            )
        for topic in sorted(data.modality_offsets_ns) or [None]:
            values = data.modality_offsets_ns.get(topic, ())
            add(
                C.MODALITY_OFFSET,
                topic,
                len(values),
                len(values),
                {"offset_count": len(values)},
                profile.modality_offset.model_dump(),
            )
        measured_ratio = "complete_step_ratio" in data.model_fields_set
        add(
            C.COMPLETE_STEP_RATIO_LOW,
            profile.complete_step.topic,
            int(measured_ratio),
            1,
            {"ratio": data.complete_step_ratio if measured_ratio else None},
            profile.complete_step.model_dump(),
            None if measured_ratio else "COMPLETE_STEP_RATIO_NOT_MEASURED",
        )
        if report.engine_version == BOUNDARY_QUALITY_ENGINE_VERSION:
            metrics = {metric.topic: metric for metric in report.topic_metrics}
            for code in sorted(BOUNDARY_CODES, key=lambda item: item.value):
                for topic in sorted(profile.required_topics) or [None]:
                    count = metrics[topic].message_count if topic in metrics else 0
                    add(
                        code,
                        topic,
                        count,
                        count,
                        {"message_count": count},
                        {"policy": "common-valid-boundaries/v1"},
                    )
            add(
                C.NO_COMMON_WINDOW,
                "__multimodal_step__",
                1,
                1,
                {
                    "has_common_frame": not report_by_target.get(
                        (C.NO_COMMON_WINDOW, "__multimodal_step__")
                    )
                },
                {"required": "non-empty common active window"},
            )
        values = dict(
            format=EVIDENCE_VERSION,
            report_sha256=report.content_sha256,
            rollout_id=report.rollout_id,
            source_sha256=report.source_sha256,
            start_ns=report.start_ns,
            end_ns=report.end_ns,
            engine_version=report.engine_version,
            profile=profile,
            input_mode="stream" if self.streaming else "batch",
            rules=tuple(sorted(rules, key=lambda rule: (rule.code.value, rule.topic or ""))),
        )
        evidence = QcRuleEvidence(
            **values, content_sha256=sha256(canonical_json_bytes(values)).hexdigest()
        )
        evidence.validate_report(report)
        self.result = evidence
