from .captures import capture_counts
from .models import ProgressFacts


def combine_capture_facts(legacy, captures):
    received = [item for item in captures if item.received and not item.canonical_episode_id]
    facts = legacy.model_dump()
    facts.update(
        received_package_count=legacy.received_package_count + len(received),
        pass_count=legacy.pass_count + sum(item.qc_outcome == "PASS" for item in received),
        risk_count=legacy.risk_count + sum(item.qc_outcome == "RISK" for item in received),
        reject_count=legacy.reject_count + sum(item.qc_outcome == "REJECT" for item in received),
        device_ids=tuple(sorted(set(legacy.device_ids) | {item.robot_id for item in received})),
        captures=capture_counts(captures),
    )
    if facts["received_package_count"] and not (
        legacy.duration_observed_package_count or legacy.duration_unknown_package_count
    ):
        facts["captured_duration_seconds"] = 0.0
    return ProgressFacts.model_validate(facts)
