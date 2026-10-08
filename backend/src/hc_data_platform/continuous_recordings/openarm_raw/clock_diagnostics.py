"""Shared timestamp-evidence diagnostics, independent of alignment algorithms."""

from collections.abc import Mapping


def clock_reasons(d: Mapping[str, object]) -> list[str]:
    reasons: list[str] = []
    if d.get("timestamp_quality") == "ros_stamped":
        return reasons
    if d.get("timestamp_quality") not in {"hardware_synced", "mapped", "ros_stamped"}:
        reasons.append("timestamp_quality_insufficient")
    if d.get("clock_uncertainty_ns") is None or not d.get("clock_evidence"):
        reasons.append("clock_uncertainty_unavailable")
    return reasons
