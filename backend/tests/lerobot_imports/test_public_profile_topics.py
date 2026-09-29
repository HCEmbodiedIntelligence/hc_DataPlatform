"""Public LeRobot feature names must survive the internal topic namespace."""

import re

from hc_data_platform.lerobot_imports.feature_names import native_feature_topic
from hc_data_platform.lerobot_imports.profiles import profile_for_info
from hc_data_platform.publishing.feature_names import feature_names
from hc_data_platform.recording_fields import SCHEMA


def test_new_public_rgbd_uses_valid_topics_and_reversible_numeric_names():
    info = dict(
        robot_type="generic",
        fps=30,
        recording_field_schema=SCHEMA,
        features={
            "observation.state": dict(dtype="float32", shape=[2], names=["a", "b"]),
            "action": dict(dtype="float32", shape=[2], names=["a", "b"]),
            "observation.images.head": dict(dtype="video", shape=[480, 640, 3]),
            "observation.images.head_depth": dict(
                dtype="video", shape=[400, 640, 1], info={"is_depth_map": True}
            ),
            "source.timestamp_ns": dict(dtype="int64", shape=[1]),
        },
    )
    new = profile_for_info(info, preserve_features=True)
    assert all(re.fullmatch(r"/[A-Za-z0-9_./-]+", topic) for topic in new.topics)
    assert [c.feature_key for c in new.cameras] == [
        "observation.images.head",
        "observation.images.head_depth",
    ]
    mapping = feature_names(sorted(new.topics))
    assert mapping[new.cameras[0].topic] == "observation.images.head"
    assert mapping[new.cameras[1].topic] == "observation.images.head_depth"
    assert new.source_timestamp_topic == native_feature_topic("source.timestamp_ns")
    assert mapping[new.source_timestamp_topic] == "source.timestamp_ns"
    # Do not silently rewrite historical workflow topic identities.
    old = profile_for_info(info)
    assert old.cameras[0].topic == "observation.images.head"
    assert old.source_timestamp_topic == "source.timestamp_ns"
