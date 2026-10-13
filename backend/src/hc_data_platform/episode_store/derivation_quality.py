"""Re-execute QC on selected primary rows and decoded RGB/depth videos.

This evaluates the aligned Episode, not the raw recording. The original raw QC
remains attached as provenance. No parent verdict or review decision is inherited.
"""

import hashlib
import json
from fractions import Fraction
from typing import Annotated, Literal

import av
from pydantic import Field, field_serializer

from hc_data_platform.data_formats.lerobot_layout import DATA_PATH, VIDEO_PATH
from hc_data_platform.quality.engine import QualityEngine
from hc_data_platform.quality.models import (
    ActionObservation,
    ActionQualityProfileV1,
    JointObservation,
    QualityInputV1,
    QualityProfileV1,
    QualityStreamObservationV1,
    TopicTimingProfileV1,
)
from hc_data_platform.quality.spooled import SpilledQualityInput

from .errors import PackageConflict
from .models import FrozenModel
from .quality import read_episode_quality
from .reader import restore_value
from .validation import parquet_rows
from .writer import write_json

AUTO_QUALITY = object()


class TagQualityContract(FrozenModel):
    version: Literal["hc-tag-qc/1"] = "hc-tag-qc/1"
    scope: Literal["selected_aligned_episode"] = "selected_aligned_episode"
    timestamp_basis: Literal["parent_hc.timestamp_ns"] = "parent_hc.timestamp_ns"
    start_ns: int = Field(ge=0, strict=True)
    end_ns: int = Field(gt=0, strict=True)
    end_basis: Literal["next_parent_sample", "last_sample_plus_nominal_period"]
    expected_frames: dict[str, Annotated[int, Field(gt=0, strict=True)]]
    profile: QualityProfileV1
    parent_profile_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_serializer("profile", when_used="json")
    def serialize_profile(self, profile):
        return json.loads(profile.content_bytes())


def freeze_quality(package, info, start, last, following, count, override=AUTO_QUALITY):
    # An absent quality key is the exact legacy encoder/UNKNOWN contract. Old
    # queued jobs must remain replayable without gaining fields or a new hash.
    if override is None:
        return None
    mapping = info["hc.feature_mapping"]
    fps = info["fps"]
    period = Fraction(1_000_000_000) / Fraction(str(fps))
    end = following if following is not None else last + round(period)
    basis = "next_parent_sample" if following is not None else "last_sample_plus_nominal_period"
    expected = {topic: count for topic in mapping}
    if override is not AUTO_QUALITY:
        contract = TagQualityContract.model_validate(override)
        if (
            (contract.start_ns, contract.end_ns, contract.end_basis) != (start, end, basis)
            or contract.expected_frames != expected
            or contract.profile.required_topics != frozenset(mapping)
            or any(
                contract.profile.timing_for(topic).target_frequency_hz != fps for topic in mapping
            )
        ):
            raise PackageConflict("Tag QC contract differs from the selected aligned window")
        return contract.model_dump(mode="json")
    parent = read_episode_quality(package).evidence
    parent_profile = parent.profile if parent is not None else None
    reverse = {target: topic for topic, target in mapping.items()}
    action_topic = reverse.get("action", "/action")
    joint_topic = reverse.get("observation.state", "/joint_states")
    # Domain limits are reused only when their exact logical topics match. The
    # timing contract is always the aligned schema rate, with explicit counts.
    action = (
        parent_profile.action
        if parent_profile and parent_profile.action.topic == action_topic
        else ActionQualityProfileV1(topic=action_topic)
    )
    profile = QualityProfileV1(
        profile_id="hc-tag-aligned-qc",
        profile_version=2,
        required_topics=frozenset(mapping),
        default_timing=TopicTimingProfileV1(
            target_frequency_hz=fps,
            minimum_frequency_hz_risk=27.0 if fps == 30 else fps * 0.95,
            minimum_frequency_hz_reject=fps * 0.75,
        ),
        action=action,
        joint_topic=joint_topic,
        joint_limits=parent_profile.joint_limits
        if parent_profile and parent_profile.joint_topic == joint_topic
        else {},
    )
    return TagQualityContract(
        start_ns=start,
        end_ns=end,
        end_basis=basis,
        expected_frames=expected,
        profile=profile,
        parent_profile_sha256=parent_profile.content_sha256() if parent_profile else None,
    ).model_dump(mode="json")


def _vector(value, target, info):
    restored = restore_value(value, target, info)
    encoding = info.get("hc.structured_features", {}).get(target, {})
    names = info["features"][target].get("names")
    if encoding.get("encoding") == "vector":
        names = encoding.get("names")
        restored = restored[encoding["value_key"]]
    if not isinstance(restored, (list, tuple)) or not restored:
        return None, None
    if any(type(v) not in (int, float) for v in restored):
        return None, None
    return restored, names


def evaluate_tag_quality(root, plan, info, *, checkpoint=lambda: None):
    contract = TagQualityContract.model_validate(plan.encoding["quality"])
    mapping, features = info["hc.feature_mapping"], info["features"]
    count = complete = 0
    for row in parquet_rows(root / DATA_PATH):
        checkpoint()
        count += 1
        complete += row["hc.sample_valid"]
    if count != plan.frame_count:
        raise PackageConflict("Tag QC primary frame count differs from the frozen selection")
    base = QualityInputV1(
        rollout_id=plan.input.identity.rollout_id,
        source_sha256=plan.input.source_sha256,
        start_ns=contract.start_ns,
        end_ns=contract.end_ns,
        topic_timestamps_ns={},
        complete_step_ratio=complete / count,
    )
    skipped = {"action_vector_not_recorded": 0, "named_joint_vector_not_recorded": 0}
    with SpilledQualityInput(base) as facts:
        for row in parquet_rows(root / DATA_PATH):
            checkpoint()
            stamp = row["hc.timestamp_ns"]
            if not base.start_ns <= stamp < base.end_ns:
                raise PackageConflict("Tag QC sample falls outside its frozen window")
            for topic, target in mapping.items():
                offset = json.loads(row[f"hc.time_error_ns.{target}"])
                if offset is not None:
                    facts.add_offset(topic, offset)
                if not row[f"hc.valid.{target}"] or features[target]["dtype"] == "video":
                    continue
                if topic not in (contract.profile.action.topic, contract.profile.joint_topic):
                    continue
                values, names = _vector(row[target], target, info)
                if topic == contract.profile.action.topic:
                    if values is None:
                        skipped["action_vector_not_recorded"] += 1
                    else:
                        facts.add("action", ActionObservation(timestamp_ns=stamp, values=values))
                if topic == contract.profile.joint_topic:
                    if (
                        values is None
                        or not isinstance(names, list)
                        or len(names) != len(values)
                        or any(not isinstance(name, str) or not name.strip() for name in names)
                        or len(set(names)) != len(names)
                    ):
                        skipped["named_joint_vector_not_recorded"] += 1
                    else:
                        facts.add(
                            "joint",
                            JointObservation(
                                timestamp_ns=stamp, positions=dict(zip(names, values, strict=True))
                            ),
                        )
        facts.database.commit()
        evaluation = QualityEngine().evaluate_with_evidence(
            facts,
            contract.profile,
            observations=_observations(root, info, checkpoint),
            expected_frames=contract.expected_frames,
        )
    write_json(
        root,
        "hc/quality.json",
        {
            "schema_version": "hc-tag-episode-qc/1",
            "status": evaluation.report.status.value,
            "quality_report": evaluation.report.model_dump(mode="json"),
            "input_contract": contract.model_dump(mode="json"),
            "numeric_observation_skips": skipped,
            "parent_report_path": "hc/derivation/parent/hc/quality.json",
            "parent_decision_inherited": False,
        },
    )
    write_json(root, "hc/quality_rules.json", evaluation.evidence.model_dump(mode="json"))
    return evaluation


def _observations(root, info, checkpoint):
    for topic, target in info["hc.feature_mapping"].items():
        feature = info["features"][target]
        if feature["dtype"] != "video":
            for row in parquet_rows(root / DATA_PATH):
                checkpoint()
                if row[f"hc.valid.{target}"]:
                    yield QualityStreamObservationV1(
                        topic=topic, timestamp_ns=row["hc.timestamp_ns"]
                    )
            continue
        depth = feature.get("info", {}).get("is_depth_map", False)
        path = root / VIDEO_PATH.format(video_key=target, chunk_index=0, file_index=0)
        with av.open(str(path)) as container:
            stream = container.streams.video[0]
            stream.codec_context.thread_count = 1
            if stream.width * stream.height * (2 if depth else 1) > 32 * 1024**2:
                raise ValueError("video frame exceeds bounded Tag QC decode size")
            frames = iter(container.decode(stream))
            failed = False
            for row in parquet_rows(root / DATA_PATH):
                checkpoint()
                values = None
                if not failed:
                    try:
                        values = next(frames).to_ndarray(format="gray12le" if depth else "gray")
                    except (StopIteration, av.FFmpegError):
                        failed = True
                if not row[f"hc.valid.{target}"]:
                    continue
                decoded = (
                    {}
                    if values is None or depth
                    else {
                        "luma_mean": float(values.mean()),
                        "fingerprint": hashlib.sha256(values.tobytes()).hexdigest(),
                    }
                )
                # A depth code is never treated as RGB luminance or an RGB
                # duplicate-frame fingerprint; HC masks remain separate facts.
                yield QualityStreamObservationV1(
                    topic=topic,
                    timestamp_ns=row["hc.timestamp_ns"],
                    is_camera=True,
                    corrupt=values is None,
                    **decoded,
                )
            if not failed and next(frames, None) is not None:
                raise PackageConflict("Tag QC video contains extra frames")
