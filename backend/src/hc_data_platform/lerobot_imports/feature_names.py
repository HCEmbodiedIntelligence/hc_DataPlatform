"""Reversible native-feature identities without importing processing services."""

NATIVE_FEATURE_PREFIX = "/lerobot/features/"


def native_feature_topic(feature: str) -> str:
    return NATIVE_FEATURE_PREFIX + feature.encode("utf-8").hex()
