"""Versioned and deterministic BE-06 quality contracts."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence, Set
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NANOSECONDS_PER_SECOND = 1_000_000_000
JsonScalar = bool | int | float | str


def _canonical_value(value: object) -> object:
    """Return a JSON-compatible value with deterministic collection ordering."""

    if isinstance(value, BaseModel):
        return _canonical_value(value.model_dump(mode="python"))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, Set) and not isinstance(value, (str, bytes, bytearray)):
        items = [_canonical_value(item) for item in value]
        return sorted(
            items,
            key=lambda item: json.dumps(
                item, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ),
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("canonical quality content cannot contain non-finite numbers")
    return value


def canonical_json_bytes(value: object) -> bytes:
    """Serialize a quality contract using the report hashing canonical form."""

    return json.dumps(
        _canonical_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


class QualityStatus(str, Enum):
    PASS = "PASS"
    RISK = "RISK"
    REJECT = "REJECT"


class FindingSeverity(str, Enum):
    WARNING = "warning"
    ERROR = "error"


class QualityCode(str, Enum):
    REQUIRED_TOPIC_MISSING = "QC_REQUIRED_TOPIC_MISSING"
    TIMESTAMP_DUPLICATE = "QC_TIMESTAMP_DUPLICATE"
    TIMESTAMP_BACKWARD = "QC_TIMESTAMP_BACKWARD"
    FREQUENCY_LOW = "QC_FREQUENCY_LOW"
    GAP_EXCESSIVE = "QC_GAP_EXCESSIVE"
    CONSECUTIVE_FRAMES_MISSING = "QC_CONSECUTIVE_FRAMES_MISSING"
    COVERAGE_LOW = "QC_COVERAGE_LOW"
    IMAGE_BLACK = "QC_IMAGE_BLACK"
    IMAGE_REPEATED = "QC_IMAGE_REPEATED"
    IMAGE_CORRUPT = "QC_IMAGE_CORRUPT"
    JOINT_OUT_OF_RANGE = "QC_JOINT_OUT_OF_RANGE"
    ACTION_MISSING = "QC_ACTION_MISSING"
    ACTION_JUMP = "QC_ACTION_JUMP"
    POINT_CLOUD_EMPTY = "QC_POINT_CLOUD_EMPTY"
    POINT_COUNT_ABNORMAL = "QC_POINT_COUNT_ABNORMAL"
    MODALITY_OFFSET = "QC_MODALITY_OFFSET"
    COMPLETE_STEP_RATIO_LOW = "QC_COMPLETE_STEP_RATIO_LOW"


class TopicTimingProfileV1(BaseModel):
    """Timing thresholds for one topic or the profile default."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_frequency_hz: int = Field(default=30, gt=0, le=1000)
    minimum_frequency_hz_risk: float = Field(default=28.5, ge=0)
    minimum_frequency_hz_reject: float = Field(default=22.5, ge=0)
    minimum_coverage_ratio_risk: float = Field(default=0.98, ge=0, le=1)
    minimum_coverage_ratio_reject: float = Field(default=0.80, ge=0, le=1)
    maximum_gap_ns_risk: int = Field(default=100_000_000, ge=0)
    maximum_gap_ns_reject: int = Field(default=1_000_000_000, ge=0)
    maximum_consecutive_missing_risk: int = Field(default=2, ge=0)
    maximum_consecutive_missing_reject: int = Field(default=29, ge=0)
    maximum_duplicate_timestamps_risk: int = Field(default=0, ge=0)
    maximum_duplicate_timestamps_reject: int | None = Field(default=None, ge=0)
    maximum_backward_timestamps_reject: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_threshold_order(self) -> TopicTimingProfileV1:
        if self.minimum_frequency_hz_risk > self.target_frequency_hz:
            raise ValueError("frequency risk threshold must not exceed target frequency")
        if self.minimum_frequency_hz_reject > self.minimum_frequency_hz_risk:
            raise ValueError("frequency reject threshold must not exceed risk threshold")
        if self.minimum_coverage_ratio_reject > self.minimum_coverage_ratio_risk:
            raise ValueError("coverage reject threshold must not exceed risk threshold")
        if self.maximum_gap_ns_reject < self.maximum_gap_ns_risk:
            raise ValueError("gap reject threshold must be at least risk threshold")
        if self.maximum_consecutive_missing_reject < self.maximum_consecutive_missing_risk:
            raise ValueError("missing-frame reject threshold must be at least risk threshold")
        duplicate_reject = self.maximum_duplicate_timestamps_reject
        if (
            duplicate_reject is not None
            and duplicate_reject < self.maximum_duplicate_timestamps_risk
        ):
            raise ValueError("duplicate reject threshold must be at least risk threshold")
        return self


class ImageQualityProfileV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    black_luma_threshold: float = Field(default=5.0, ge=0, le=255)
    maximum_black_frame_ratio_risk: float = Field(default=0.02, ge=0, le=1)
    maximum_black_frame_ratio_reject: float = Field(default=0.50, ge=0, le=1)
    maximum_repeated_frame_ratio_risk: float = Field(default=0.05, ge=0, le=1)
    maximum_repeated_frame_ratio_reject: float = Field(default=0.50, ge=0, le=1)
    maximum_corrupt_frame_ratio_risk: float = Field(default=0.0, ge=0, le=1)
    maximum_corrupt_frame_ratio_reject: float = Field(default=0.0, ge=0, le=1)

    @model_validator(mode="after")
    def validate_threshold_order(self) -> ImageQualityProfileV1:
        pairs = (
            (self.maximum_black_frame_ratio_risk, self.maximum_black_frame_ratio_reject),
            (
                self.maximum_repeated_frame_ratio_risk,
                self.maximum_repeated_frame_ratio_reject,
            ),
            (self.maximum_corrupt_frame_ratio_risk, self.maximum_corrupt_frame_ratio_reject),
        )
        if any(reject < risk for risk, reject in pairs):
            raise ValueError("image reject thresholds must be at least risk thresholds")
        return self


class ActionQualityProfileV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    topic: str = "/action"
    minimum_observation_count_risk: int = Field(default=1, ge=0)
    minimum_observation_count_reject: int = Field(default=0, ge=0)
    maximum_jump_risk: float | None = Field(default=None, gt=0)
    maximum_jump_reject: float | None = Field(default=None, gt=0)
    dimension_mismatch_is_reject: bool = True

    @model_validator(mode="after")
    def validate_threshold_order(self) -> ActionQualityProfileV1:
        if self.minimum_observation_count_reject > self.minimum_observation_count_risk:
            raise ValueError("action reject count must not exceed risk count")
        if (
            self.maximum_jump_risk is not None
            and self.maximum_jump_reject is not None
            and self.maximum_jump_reject < self.maximum_jump_risk
        ):
            raise ValueError("action jump reject threshold must be at least risk threshold")
        return self


class PointCloudQualityProfileV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    minimum_point_count: int | None = Field(default=None, ge=0)
    maximum_point_count: int | None = Field(default=None, ge=0)
    maximum_empty_ratio_risk: float = Field(default=0.0, ge=0, le=1)
    maximum_empty_ratio_reject: float = Field(default=0.20, ge=0, le=1)
    maximum_abnormal_count_ratio_risk: float = Field(default=0.0, ge=0, le=1)
    maximum_abnormal_count_ratio_reject: float = Field(default=0.50, ge=0, le=1)

    @model_validator(mode="after")
    def validate_threshold_order(self) -> PointCloudQualityProfileV1:
        if (
            self.minimum_point_count is not None
            and self.maximum_point_count is not None
            and self.minimum_point_count > self.maximum_point_count
        ):
            raise ValueError("minimum point count must not exceed maximum point count")
        if self.maximum_empty_ratio_reject < self.maximum_empty_ratio_risk:
            raise ValueError("empty-cloud reject threshold must be at least risk threshold")
        if self.maximum_abnormal_count_ratio_reject < self.maximum_abnormal_count_ratio_risk:
            raise ValueError("point-count reject threshold must be at least risk threshold")
        return self


class ModalityOffsetProfileV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    maximum_p95_offset_ns_risk: int = Field(default=20_000_000, ge=0)
    maximum_p95_offset_ns_reject: int = Field(default=100_000_000, ge=0)

    @model_validator(mode="after")
    def validate_threshold_order(self) -> ModalityOffsetProfileV1:
        if self.maximum_p95_offset_ns_reject < self.maximum_p95_offset_ns_risk:
            raise ValueError("offset reject threshold must be at least risk threshold")
        return self


class CompleteStepProfileV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    topic: str = "__multimodal_step__"
    minimum_ratio_risk: float = Field(default=0.98, ge=0, le=1)
    minimum_ratio_reject: float = Field(default=0.80, ge=0, le=1)

    @model_validator(mode="after")
    def validate_threshold_order(self) -> CompleteStepProfileV1:
        if self.minimum_ratio_reject > self.minimum_ratio_risk:
            raise ValueError("complete-step reject threshold must not exceed risk threshold")
        return self


class TopicTimingMetricsV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    topic: str = Field(min_length=1)
    message_count: int = Field(ge=0)
    unique_frame_count: int = Field(ge=0)
    expected_frame_count: int = Field(ge=1)
    duplicate_count: int = Field(ge=0)
    backward_count: int = Field(ge=0)
    actual_frequency_hz: float = Field(ge=0)
    interval_p50_ns: int | None = Field(default=None, ge=0)
    interval_p95_ns: int | None = Field(default=None, ge=0)
    interval_p99_ns: int | None = Field(default=None, ge=0)
    maximum_gap_ns: int = Field(ge=0)
    maximum_consecutive_missing: int = Field(ge=0)
    coverage_ratio: float = Field(ge=0, le=1)

    @property
    def unique_timestamp_count(self) -> int:
        """Compatibility name used by the initial BE-06 draft."""

        return self.unique_frame_count


class QcFinding(BaseModel):
    """Traceable rule result with an affected range; point events use equal bounds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: QualityCode
    severity: FindingSeverity
    message: str
    topic: str = Field(min_length=1)
    start_ns: int = Field(ge=0)
    end_ns: int = Field(ge=0)
    observed: JsonScalar
    threshold: JsonScalar

    @model_validator(mode="after")
    def validate_interval(self) -> QcFinding:
        if self.end_ns < self.start_ns:
            raise ValueError("finding end_ns must be at least start_ns")
        return self

    @property
    def rule(self) -> QualityCode:
        return self.code


class ImageObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    timestamp_ns: int = Field(ge=0)
    luma_mean: float | None = Field(default=None, ge=0, le=255)
    fingerprint: str | None = None
    corrupt: bool = False

    @model_validator(mode="after")
    def validate_decoded_fields(self) -> ImageObservation:
        if not self.corrupt and (self.luma_mean is None or not self.fingerprint):
            raise ValueError("decoded images require luma_mean and fingerprint")
        return self


class JointObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    timestamp_ns: int = Field(ge=0)
    positions: dict[str, float]

    @field_validator("positions")
    @classmethod
    def validate_positions(cls, value: dict[str, float]) -> dict[str, float]:
        if any(not math.isfinite(position) for position in value.values()):
            raise ValueError("joint positions must be finite")
        return value


class ActionObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    timestamp_ns: int = Field(ge=0)
    values: tuple[float, ...]

    @field_validator("values")
    @classmethod
    def validate_values(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        if any(not math.isfinite(item) for item in value):
            raise ValueError("action values must be finite")
        return value


class PointCloudObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    timestamp_ns: int = Field(ge=0)
    point_count: int = Field(ge=0)


class QualityInputV1(BaseModel):
    """Already-decoded observations; MCAP parsing is outside BE-06."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["quality-input/v1"] = "quality-input/v1"
    rollout_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    start_ns: int = Field(ge=0)
    end_ns: int = Field(gt=0)
    topic_timestamps_ns: dict[str, tuple[int, ...]]
    images: dict[str, tuple[ImageObservation, ...]] = Field(default_factory=dict)
    joints: tuple[JointObservation, ...] = ()
    actions: tuple[ActionObservation, ...] = ()
    point_clouds: dict[str, tuple[PointCloudObservation, ...]] = Field(default_factory=dict)
    modality_offsets_ns: dict[str, tuple[int, ...]] = Field(default_factory=dict)
    complete_step_ratio: float = Field(default=1.0, ge=0, le=1)

    @model_validator(mode="after")
    def validate_window(self) -> QualityInputV1:
        if self.end_ns <= self.start_ns:
            raise ValueError("end_ns must be greater than start_ns")
        topic_names = (
            tuple(self.topic_timestamps_ns)
            + tuple(self.images)
            + tuple(self.point_clouds)
            + tuple(self.modality_offsets_ns)
        )
        if any(not topic.strip() for topic in topic_names):
            raise ValueError("input topic names must not be blank")
        timestamp_groups: list[tuple[str, tuple[int, ...]]] = list(self.topic_timestamps_ns.items())
        timestamp_groups.extend(
            (topic, tuple(item.timestamp_ns for item in observations))
            for topic, observations in self.images.items()
        )
        timestamp_groups.extend(
            (topic, tuple(item.timestamp_ns for item in observations))
            for topic, observations in self.point_clouds.items()
        )
        timestamp_groups.extend(
            (
                ("joints", tuple(item.timestamp_ns for item in self.joints)),
                ("actions", tuple(item.timestamp_ns for item in self.actions)),
            )
        )
        for topic, timestamps in timestamp_groups:
            if any(
                timestamp < self.start_ns or timestamp >= self.end_ns for timestamp in timestamps
            ):
                raise ValueError(f"{topic} contains a timestamp outside the rollout window")
        return self


class QualityProfileV1(BaseModel):
    """Immutable versioned rule profile. Every decision threshold lives here."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["quality-profile/v1"] = "quality-profile/v1"
    profile_id: str = Field(min_length=1)
    profile_version: int = Field(default=1, ge=1)
    engine_version: str = Field(default="be06-qc/1", min_length=1)
    required_topics: frozenset[str]
    default_timing: TopicTimingProfileV1 = Field(default_factory=TopicTimingProfileV1)
    topic_timing: dict[str, TopicTimingProfileV1] = Field(default_factory=dict)
    default_image: ImageQualityProfileV1 = Field(default_factory=ImageQualityProfileV1)
    image_profiles: dict[str, ImageQualityProfileV1] = Field(default_factory=dict)
    joint_topic: str = "/joint_states"
    joint_limits: dict[str, tuple[float, float]] = Field(default_factory=dict)
    joint_out_of_range_is_reject: bool = True
    action: ActionQualityProfileV1 = Field(default_factory=ActionQualityProfileV1)
    default_point_cloud: PointCloudQualityProfileV1 = Field(
        default_factory=PointCloudQualityProfileV1
    )
    point_cloud_profiles: dict[str, PointCloudQualityProfileV1] = Field(default_factory=dict)
    modality_offset: ModalityOffsetProfileV1 = Field(default_factory=ModalityOffsetProfileV1)
    complete_step: CompleteStepProfileV1 = Field(default_factory=CompleteStepProfileV1)

    @model_validator(mode="before")
    @classmethod
    def upgrade_initial_draft(cls, value: object) -> object:
        """Map the initial flat draft fields into the versioned nested contract."""

        if not isinstance(value, dict):
            return value
        data = dict(value)
        timing_names = {
            "target_frequency_hz",
            "frequency_risk_ratio",
            "frequency_reject_ratio",
            "minimum_coverage_ratio",
            "gap_risk_periods",
            "gap_reject_periods",
        }
        if timing_names.intersection(data):
            if "default_timing" in data:
                raise ValueError("cannot mix draft timing fields with default_timing")
            target = int(data.pop("target_frequency_hz", 30))
            risk_ratio = float(data.pop("frequency_risk_ratio", 0.95))
            reject_ratio = float(data.pop("frequency_reject_ratio", 0.75))
            risk_periods = int(data.pop("gap_risk_periods", 3))
            reject_periods = int(data.pop("gap_reject_periods", 30))
            timing: dict[str, object] = {
                "target_frequency_hz": target,
                "minimum_frequency_hz_risk": target * risk_ratio,
                "minimum_frequency_hz_reject": target * reject_ratio,
                "maximum_gap_ns_risk": round(risk_periods * NANOSECONDS_PER_SECOND / target),
                "maximum_gap_ns_reject": round(reject_periods * NANOSECONDS_PER_SECOND / target),
            }
            if "minimum_coverage_ratio" in data:
                timing["minimum_coverage_ratio_risk"] = data.pop("minimum_coverage_ratio")
            data["default_timing"] = timing

        image_names = {
            "black_luma_threshold": "black_luma_threshold",
            "black_frame_ratio_risk": "maximum_black_frame_ratio_risk",
            "repeated_frame_ratio_risk": "maximum_repeated_frame_ratio_risk",
        }
        image_values = {
            new_name: data.pop(old_name)
            for old_name, new_name in image_names.items()
            if old_name in data
        }
        if image_values:
            if "default_image" in data:
                raise ValueError("cannot mix draft image fields with default_image")
            data["default_image"] = image_values

        if "max_action_jump" in data:
            if "action" in data:
                raise ValueError("cannot mix max_action_jump with action")
            jump = data.pop("max_action_jump")
            data["action"] = {"maximum_jump_risk": jump}

        point_values: dict[str, object] = {}
        if "point_count_range" in data:
            point_range = data.pop("point_count_range")
            if point_range is not None:
                point_values["minimum_point_count"] = point_range[0]
                point_values["maximum_point_count"] = point_range[1]
        if "empty_point_cloud_ratio_reject" in data:
            point_values["maximum_empty_ratio_reject"] = data.pop("empty_point_cloud_ratio_reject")
        if point_values:
            if "default_point_cloud" in data:
                raise ValueError("cannot mix draft point-cloud fields with default_point_cloud")
            data["default_point_cloud"] = point_values

        offset_values: dict[str, object] = {}
        if "modality_offset_risk_ns" in data:
            offset_values["maximum_p95_offset_ns_risk"] = data.pop("modality_offset_risk_ns")
        if "modality_offset_reject_ns" in data:
            offset_values["maximum_p95_offset_ns_reject"] = data.pop("modality_offset_reject_ns")
        if offset_values:
            if "modality_offset" in data:
                raise ValueError("cannot mix draft offset fields with modality_offset")
            data["modality_offset"] = offset_values

        complete_values: dict[str, object] = {}
        if "complete_step_ratio_risk" in data:
            complete_values["minimum_ratio_risk"] = data.pop("complete_step_ratio_risk")
        if "complete_step_ratio_reject" in data:
            complete_values["minimum_ratio_reject"] = data.pop("complete_step_ratio_reject")
        if complete_values:
            if "complete_step" in data:
                raise ValueError("cannot mix draft complete-step fields with complete_step")
            data["complete_step"] = complete_values
        return data

    @model_validator(mode="after")
    def validate_profile(self) -> QualityProfileV1:
        names = (
            tuple(self.required_topics)
            + tuple(self.topic_timing)
            + tuple(self.image_profiles)
            + tuple(self.point_cloud_profiles)
            + (self.joint_topic, self.action.topic, self.complete_step.topic)
        )
        if any(not name.strip() for name in names):
            raise ValueError("profile topic names must not be blank")
        for joint, limits in self.joint_limits.items():
            if not joint.strip():
                raise ValueError("joint names must not be blank")
            if not all(math.isfinite(limit) for limit in limits) or limits[0] > limits[1]:
                raise ValueError(f"invalid limits for joint {joint}")
        return self

    def timing_for(self, topic: str) -> TopicTimingProfileV1:
        return self.topic_timing.get(topic, self.default_timing)

    def image_for(self, topic: str) -> ImageQualityProfileV1:
        return self.image_profiles.get(topic, self.default_image)

    def point_cloud_for(self, topic: str) -> PointCloudQualityProfileV1:
        return self.point_cloud_profiles.get(topic, self.default_point_cloud)

    def content_sha256(self) -> str:
        return hashlib.sha256(self.content_bytes()).hexdigest()

    def content_bytes(self) -> bytes:
        return canonical_json_bytes(self)

    @property
    def target_frequency_hz(self) -> int:
        """Compatibility accessor for the initial flat profile draft."""

        return self.default_timing.target_frequency_hz


class QcReportV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["qc-report/v1"] = "qc-report/v1"
    rollout_id: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_id: str
    profile_version: int = Field(ge=1)
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    engine_version: str
    start_ns: int = Field(ge=0)
    end_ns: int = Field(gt=0)
    duration_ns: int = Field(gt=0)
    status: QualityStatus
    topic_metrics: tuple[TopicTimingMetricsV1, ...]
    findings: tuple[QcFinding, ...]
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_report(self) -> QcReportV1:
        if self.end_ns <= self.start_ns or self.duration_ns != self.end_ns - self.start_ns:
            raise ValueError("report duration must equal end_ns - start_ns")
        topics = tuple(metrics.topic for metrics in self.topic_metrics)
        if topics != tuple(sorted(topics)) or len(topics) != len(set(topics)):
            raise ValueError("topic metrics must be unique and sorted by topic")
        expected_hash = hashlib.sha256(self.content_bytes()).hexdigest()
        if expected_hash != self.content_sha256:
            raise ValueError("report content hash does not match canonical content")
        return self

    @classmethod
    def build(
        cls,
        *,
        rollout_id: str,
        source_sha256: str,
        profile_id: str,
        profile_version: int,
        profile_sha256: str,
        engine_version: str,
        start_ns: int,
        end_ns: int,
        status: QualityStatus,
        topic_metrics: tuple[TopicTimingMetricsV1, ...],
        findings: tuple[QcFinding, ...],
    ) -> QcReportV1:
        values: dict[str, object] = {
            "schema_version": "qc-report/v1",
            "rollout_id": rollout_id,
            "source_sha256": source_sha256,
            "profile_id": profile_id,
            "profile_version": profile_version,
            "profile_sha256": profile_sha256,
            "engine_version": engine_version,
            "start_ns": start_ns,
            "end_ns": end_ns,
            "duration_ns": end_ns - start_ns,
            "status": status,
            "topic_metrics": topic_metrics,
            "findings": findings,
        }
        digest = hashlib.sha256(canonical_json_bytes(values)).hexdigest()
        return cls(**values, content_sha256=digest)

    def content_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(exclude={"content_sha256"}))

    def has_valid_digest(self) -> bool:
        return hashlib.sha256(self.content_bytes()).hexdigest() == self.content_sha256


class QualitySummaryV1(BaseModel):
    """Mutable metadata pointer; the referenced report remains immutable."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["quality-summary/v1"] = "quality-summary/v1"
    rollout_id: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_id: str
    profile_version: int = Field(ge=1)
    engine_version: str
    status: QualityStatus
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def from_report(cls, report: QcReportV1) -> QualitySummaryV1:
        return cls(
            rollout_id=report.rollout_id,
            source_sha256=report.source_sha256,
            profile_id=report.profile_id,
            profile_version=report.profile_version,
            engine_version=report.engine_version,
            status=report.status,
            report_sha256=report.content_sha256,
        )


class QualityCompletedV1(BaseModel):
    """Emitted only after both persistence operations succeed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_version: Literal["quality-completed/v1"] = "quality-completed/v1"
    rollout_id: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_id: str
    profile_version: int = Field(ge=1)
    engine_version: str
    status: QualityStatus
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def from_report(cls, report: QcReportV1) -> QualityCompletedV1:
        return cls(
            rollout_id=report.rollout_id,
            source_sha256=report.source_sha256,
            profile_id=report.profile_id,
            profile_version=report.profile_version,
            engine_version=report.engine_version,
            status=report.status,
            report_sha256=report.content_sha256,
        )


class AutoQualityProblemV1(BaseModel):
    """Latest failed automatic-QC result projected for the unified issue center."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["auto-quality-problem/v1"] = "auto-quality-problem/v1"
    id: str = Field(pattern=r"^qc_[0-9a-f]{32}$")
    source: Literal["AUTO_QC"] = "AUTO_QC"
    session_id: str | None = Field(default=None, min_length=1, max_length=256)
    rollout_id: str = Field(min_length=1, max_length=256)
    data_package_id: str | None = Field(default=None, min_length=1, max_length=256)
    status: Literal["RISK", "REJECT"]
    severity: Literal["HIGH", "CRITICAL"]
    start_ns: str = Field(pattern=r"^(?:0|[1-9][0-9]*)$")
    end_ns: str = Field(pattern=r"^[1-9][0-9]*$")
    message: str = Field(min_length=1, max_length=1_000)
    finding_count: int = Field(gt=0)
    finding_codes: tuple[QualityCode, ...] = Field(min_length=1)
    topics: tuple[str, ...] = Field(min_length=1)
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    updated_at: datetime

    @model_validator(mode="after")
    def validate_range(self) -> AutoQualityProblemV1:
        if int(self.end_ns) <= int(self.start_ns):
            raise ValueError("end_ns must be greater than start_ns")
        return self

    @classmethod
    def from_report(
        cls,
        report: QcReportV1,
        *,
        session_id: str | None,
        data_package_id: str | None,
        updated_at: datetime,
    ) -> AutoQualityProblemV1:
        if report.status is QualityStatus.PASS or not report.findings:
            raise ValueError("only failed QC reports with findings can become problem data")
        first = report.findings[0]
        start_ns = min(item.start_ns for item in report.findings)
        end_ns = max(max(item.end_ns, item.start_ns + 1) for item in report.findings)
        return cls(
            id=f"qc_{report.content_sha256[:32]}",
            session_id=session_id,
            rollout_id=report.rollout_id,
            data_package_id=data_package_id,
            status=report.status.value,
            severity="CRITICAL" if report.status is QualityStatus.REJECT else "HIGH",
            start_ns=str(start_ns),
            end_ns=str(end_ns),
            message=first.message[:1_000],
            finding_count=len(report.findings),
            finding_codes=tuple(sorted({item.code for item in report.findings}, key=str)),
            topics=tuple(sorted({item.topic for item in report.findings})),
            report_sha256=report.content_sha256,
            updated_at=updated_at,
        )


class AutoQualityProblemListV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["auto-quality-problem-list/v1"] = "auto-quality-problem-list/v1"
    items: tuple[AutoQualityProblemV1, ...]
    total: int = Field(ge=0)
