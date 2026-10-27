"""Compile the frozen capture scheme into the existing QC engine input.

This is an execution artifact, never a separately configured project policy.
Schema and required values are checked by capture validation. Every additional
threshold is owned by the same scheme and exposed in its editor.
"""

from hc_data_platform.quality.models import QualityProfileV1

from .common import digest
from .schema import native_topic
from .quality_models import CaptureQualityPolicy


def from_snapshot(snapshot, topics, frequency_hz, *, joint_topic=None, action_topic=None,
                  feature_topics=None):
    output = snapshot["output"]
    features = output["features"]
    state = next((name for name, f in features.items() if f["role"] == "state"), "observation.state")
    action = next((name for name, f in features.items() if f["role"] == "action"), "action")
    policy = CaptureQualityPolicy.model_validate(snapshot.get("quality_policy") or {})
    identity = digest({"compiler": "capture-scheme-qc/3", "engine": "be06-qc/4", "output": output,
                       "quality_policy": policy.model_dump(mode="json"),
                       "feature_topics": feature_topics, "joint_topic": joint_topic, "action_topic": action_topic,
                       "adapter": snapshot.get("effective_adapter"),
                       "topics": sorted(topics), "observed_frequency_hz": frequency_hz})
    def topic(name):
        if feature_topics and name in feature_topics:
            return feature_topics[name]
        if name == state:
            return joint_topic or native_topic(name)
        if name == action:
            return action_topic or native_topic(name)
        return native_topic(name)

    return QualityProfileV1(
        profile_id="capture-scheme-" + identity[:32], profile_version=1,
        engine_version="be06-qc/4", required_topics=frozenset(topics),
        joint_topic=topic(state),
        action=policy.action.model_copy(update={"topic": topic(action)}),
        default_timing=policy.timing.compile(frequency_hz),
        topic_timing={topic(name): rule.compile(frequency_hz) for name, rule in policy.feature_timing.items() if topic(name) in topics},
        default_image=policy.image,
        image_profiles={topic(name): rule for name, rule in policy.feature_images.items() if topic(name) in topics},
        joint_limits=policy.joint_limits, joint_out_of_range_is_reject=policy.joint_out_of_range_is_reject,
        default_point_cloud=policy.point_cloud,
        point_cloud_profiles={topic(name): rule for name, rule in policy.feature_point_clouds.items() if topic(name) in topics},
        modality_offset=policy.synchronization, complete_step=policy.completeness,
    )
