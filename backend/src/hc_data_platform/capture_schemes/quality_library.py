"""Reusable rules are copied into a scheme draft before publication."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from hc_data_platform.continuous_recordings.quality_checks import RECORDING_CHECKS
from hc_data_platform.quality.models import QualityCode
from hc_data_platform.quality.recording_diagnostics import LABELS
from hc_data_platform.verification.models import VerificationCode

from .common import encoded, reject
from .quality_models import CaptureQualityPolicy

Group = Literal["timing", "completeness", "synchronization", "image", "action", "point_cloud"]
Scalar = float | int | bool | None
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


def pair(stem: str) -> tuple[str, str]:
    return stem + "_risk", stem + "_reject"


PARAMETERS: dict[QualityCode, tuple[Group, tuple[str, ...]]] = {
    QualityCode.TIMESTAMP_DUPLICATE: ("timing", pair("maximum_duplicate_timestamps")),
    QualityCode.TIMESTAMP_BACKWARD: ("timing", ("maximum_backward_timestamps_reject",)),
    QualityCode.FREQUENCY_LOW: ("timing", pair("minimum_frequency_ratio")),
    QualityCode.GAP_EXCESSIVE: ("timing", pair("maximum_gap_ns")),
    QualityCode.CONSECUTIVE_FRAMES_MISSING: ("timing", pair("maximum_consecutive_missing")),
    QualityCode.COVERAGE_LOW: ("timing", pair("minimum_coverage_ratio")),
    QualityCode.IMAGE_BLACK: (
        "image",
        ("black_luma_threshold", *pair("maximum_black_frame_ratio")),
    ),
    QualityCode.IMAGE_REPEATED: ("image", pair("maximum_repeated_frame_ratio")),
    QualityCode.IMAGE_CORRUPT: ("image", pair("maximum_corrupt_frame_ratio")),
    QualityCode.ACTION_MISSING: ("action", pair("minimum_observation_count")),
    QualityCode.ACTION_JUMP: ("action", (*pair("maximum_jump"), "dimension_mismatch_is_reject")),
    QualityCode.POINT_CLOUD_EMPTY: ("point_cloud", pair("maximum_empty_ratio")),
    QualityCode.POINT_COUNT_ABNORMAL: (
        "point_cloud",
        ("minimum_point_count", "maximum_point_count", *pair("maximum_abnormal_count_ratio")),
    ),
    QualityCode.MODALITY_OFFSET: ("synchronization", pair("maximum_p95_offset_ns")),
    QualityCode.COMPLETE_STEP_RATIO_LOW: ("completeness", pair("minimum_ratio")),
}


class EngineRule(BaseModel):
    code: QualityCode
    group: Group | None = None
    defaults: dict[str, Scalar] = Field(default_factory=dict)


class ThresholdRule(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: Name
    code: QualityCode
    parameters: dict[str, Scalar]

    @model_validator(mode="after")
    def check_parameters(self):
        spec = PARAMETERS.get(self.code)
        if spec is None:
            raise ValueError("此检查不支持自定义阈值")
        group, fields = spec
        if set(self.parameters) != set(fields):
            raise ValueError("规则参数与所选检查不匹配")
        CaptureQualityPolicy.model_validate({group: self.parameters}, strict=True)
        return self


class RuleLibrary(BaseModel):
    engine_rules: list[EngineRule]
    recording_checks: dict[str, str]
    verification_checks: dict[str, str]
    custom_rules: list[ThresholdRule]


def library(db, org: str) -> RuleLibrary:
    policy = CaptureQualityPolicy()
    engine_rules = []
    for code in QualityCode:
        rule = EngineRule(code=code)
        if code in PARAMETERS:
            group, fields = PARAMETERS[code]
            settings = getattr(policy, group)
            rule = EngineRule(
                code=code,
                group=group,
                defaults={field: getattr(settings, field) for field in fields},
            )
        engine_rules.append(rule)
    rows = db.execute(
        "SELECT document FROM ingest.quality_rule_templates WHERE organization_id=%s ORDER BY name",
        (org,),
    ).fetchall()
    return RuleLibrary(
        engine_rules=engine_rules,
        recording_checks=RECORDING_CHECKS,
        verification_checks={c.value: LABELS.get(c.value, c.value) for c in VerificationCode},
        custom_rules=[ThresholdRule.model_validate(row[0]) for row in rows],
    )


def add_rule(db, org: str, rule: ThresholdRule, actor: str) -> ThresholdRule:
    value = rule.model_dump(mode="json")
    row = db.execute(
        """INSERT INTO ingest.quality_rule_templates (organization_id,name,document,created_by)
           VALUES (%s,%s,%s::jsonb,%s) ON CONFLICT (organization_id,name) DO NOTHING
           RETURNING document""",
        (org, rule.name, encoded(value), actor),
    ).fetchone()
    if row is None:
        existing = db.execute(
            "SELECT document FROM ingest.quality_rule_templates "
            "WHERE organization_id=%s AND name=%s",
            (org, rule.name),
        ).fetchone()
        if existing is None or existing[0] != value:
            reject("QUALITY_RULE_NAME_CONFLICT", "该组织已存在同名规则，请更换名称。")
    return rule
